"""Durable queue and fenced singleton worker, extending the existing RSS normalizer."""

from datetime import timedelta
import hashlib
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from django.conf import settings
from django.db import transaction, DatabaseError
from django.db.models import Q, F
from django.utils import timezone
from .models import (
    AutomationSettings,
    AutomationWorkerLock,
    CrawlRun,
    NewsSource,
    NewsCategory,
    NewsArticle,
    AdminEvent,
)
from .safe_fetch import fetch_xml
from .regions import item_location
from .utils.rss_ingestor import parse_rss_or_atom_xml, strip_html_tags


class Busy(Exception):
    pass


def config():
    return AutomationSettings.objects.get_or_create(pk=1)[0]


def event(kind, message, **kwargs):
    return AdminEvent.objects.create(kind=kind, message=message, **kwargs)


def queue_run(trigger="manual", source=None, actor=None, dry_run=False, publishing_rules=None):
    if source:
        from .source_feeds import effective_source
        source = effective_source(source)
    config()
    with transaction.atomic():
        AutomationSettings.objects.select_for_update().get(pk=1)
        active = CrawlRun.objects.filter(status__in=['queued', 'running']).order_by('id').first()
        if active:
            raise Busy(f'Run #{active.pk} is already queued or executing. Wait for it to finish, or use Cancel pending run to clear a waiting job before starting another.')
        import uuid
        run = CrawlRun.objects.create(trigger="on_demand" if publishing_rules is not None else trigger, source=source, dry_run=dry_run, publishing_rules=publishing_rules, request_key=uuid.uuid4() if publishing_rules is not None else None)
        event("crawl", "Crawler run queued", actor=actor, run=run, status="queued")
        return run


def cancel_run(run_id, actor=None):
    """Cancel waiting/stopped work only after acquiring the execution fence."""
    config()
    try:
        with transaction.atomic():
            AutomationWorkerLock.objects.get_or_create(pk=1)
            AutomationWorkerLock.objects.select_for_update(nowait=True).get(pk=1)
            AutomationSettings.objects.select_for_update().get(pk=1)
            run = CrawlRun.objects.select_for_update(nowait=True).get(pk=run_id)
            if run.status == 'cancelled':
                return run
            if run.status not in ('queued', 'running'):
                raise Busy('This run has already finished. Refresh the dashboard before starting another.')
            run.status = 'cancelled'
            run.completed_at = timezone.now()
            run.duration = (run.completed_at-run.started_at).total_seconds() if run.started_at else None
            run.error = 'Pending run cancelled by an administrator. Published articles are unchanged.'
            run.save(update_fields=['status', 'completed_at', 'duration', 'error'])
            event('audit', 'Pending crawler run cancelled', run=run, actor=actor, status='cancelled')
            return run
    except DatabaseError:
        raise Busy('A worker is executing a batch, or the queue is busy. It cannot be cancelled while processing; wait briefly and refresh.') from None


def canonical_url(raw):
    p = urlsplit(str(raw or "").strip())
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        raise ValueError("Invalid article URL")
    host = p.hostname.lower()
    if p.port and p.port not in (80, 443):
        host += ":" + str(p.port)
    query = sorted(
        (k, v)
        for k, v in parse_qsl(p.query)
        if not k.lower().startswith("utm_") and k.lower() not in (
            "fbclid", "gclid", "at_medium", "at_campaign", "cmp", "traffic_source",
        )
    )
    return urlunsplit((p.scheme.lower(), host, p.path or "/", urlencode(query), ""))[
        :2000
    ]


def source_items(source, timeout):
    xml = fetch_xml(source.feed_url, timeout)
    items = parse_rss_or_atom_xml(
        xml,
        default_source=source.name,
        default_source_url=source.url,
        default_location=source.location,
        default_category=(
            source.default_category.name if source.default_category else "News"
        ),
        strict=True,
    )
    # A saved configuration is not a successful health check. Invalid/empty feeds fail visibly.
    if not items:
        raise ValueError("Feed contains no eligible RSS/Atom entries.")
    return items


def feed_batch(source, cfg, run):
    """Reuse a recent parsed feed across one-article continuations.

    Explicit feed tests always perform a fresh network request. A process restart
    can safely fetch again; durable article/quota/retry progress remains in SQL.
    """
    if run.dry_run or run.trigger == 'test':
        return source_items(source, cfg.timeout_seconds)
    from django.core.cache import cache
    cycle = str(run.request_key) if run.request_key else (run.cycle_started_at or run.created_at).isoformat()
    identity = repr((cycle, source.pk, source.feed_url, source.default_category_id, source.location, source.name, cfg.revision))
    key = 'publishing-feed-v1:' + hashlib.sha256(identity.encode()).hexdigest()
    items = cache.get(key)
    if items is None:
        items = source_items(source, cfg.timeout_seconds)
        cache.set(key, items, timeout=min(cfg.interval_minutes * 60, 300))
    return items


@transaction.atomic
def store_item(item, source, dry_run=False, run=None, budget=None, categories=None):
    title = strip_html_tags(item.get("title", "")).strip()[:255]
    summary = strip_html_tags(item.get("description", "")).strip()[:1000]
    url = canonical_url(item.get("article_url"))
    if not title or not summary or not item.get("published_at"):
        raise ValueError("Missing title, summary, link or publication time")
    if item["published_at"] > timezone.now() + timedelta(minutes=5):
        raise ValueError("Future publication time")
    key = hashlib.sha256(url.encode()).hexdigest()
    if NewsArticle.all_objects.filter(
        Q(ingestion_key=key) | Q(article_url=url) | Q(title=title, source=source)
    ).exists():
        return "duplicates"
    from .article_writing import prepare_article, source_evidence, ArticlePreparationError
    from .seo import words
    if dry_run:
        return "eligible"
    if budget is not None:
        if any(remaining <= 0 for remaining in budget):
            return "deferred"
        # Limit candidate/evidence work separately from paid model invocations.
        # Failed retrieval must not consume the allowance for another publisher.
        for index in range(1, len(budget)):
            budget[index] -= 1
    from .publishing_retries import mark_writing_success
    evidence = item.get("source_text") or item.get("content") or ""
    if len(words(evidence)) < 400:
        evidence = source_evidence(url, source.url)
    from .source_photos import photo_url
    page_photo = getattr(evidence, 'photo', {})
    image_url = photo_url(item.get('remote_image_url'), url) or page_photo.get('url', '')
    # Persist trusted preparation on an individual retry when only its photo is
    # blocked. The next attempt must not pay to rewrite the same report again.
    item['source_text'] = str(evidence)
    if image_url: item['remote_image_url'] = image_url
    fingerprint = hashlib.sha256(__import__('json').dumps([source.pk,url,title,summary,str(evidence)],ensure_ascii=False).encode()).hexdigest()
    prepared = item.get('_prepared_article') if item.get('_prepared_fingerprint') == fingerprint else None
    from .article_writing import article_quality
    required = ('content','focus_keyword','seo_title','seo_description')
    if not isinstance(prepared,dict) or not all(isinstance(prepared.get(field),str) for field in required) or not article_quality(prepared['content'])['passed']:
        from .utils.ai_summarizer import client
        from .publishing_retries import writing_hold
        if client is None:
            raise ArticlePreparationError('OPENAI_API_KEY is not configured; full article cannot be prepared.')
        hold = writing_hold()
        if hold:
            raise ArticlePreparationError(hold.message, details={'code':hold.code,'held':True,'retry_after':max(0,int((hold.retry_after-timezone.now()).total_seconds()))})
        if budget is not None: budget[0] -= 1
        prepared = prepare_article(title=title, summary=summary, evidence=evidence, source_name=source.name, source_url=url)
        item['_prepared_article'] = prepared
        item['_prepared_fingerprint'] = fingerprint
    if not dry_run:
        mark_writing_success()
        article = NewsArticle.all_objects.create(
            title=title,
            description=summary,
            content=prepared["content"],
            remote_image_url=image_url,
            image_alt=(item.get("image_alt") or page_photo.get("alt") or title)[:255] if image_url else "",
            image_credit=(item.get("image_credit") or page_photo.get("credit") or (f"Image via {source.name}" if image_url else ""))[:500],
            image_reference_url=url if image_url else "",
            focus_keyword=prepared["focus_keyword"],
            seo_title=prepared["seo_title"],
            seo_description=prepared["seo_description"],
            source=source,
            article_url=url,
            ingestion_key=key,
            imported=True,
            published_at=timezone.now(),
            source_published_at=item["published_at"],
            author=item.get("author", "")[:255],
            location=item_location(item.get('categories'), source.location),
            total_sources=1,
            left_coverage=0,
            center_coverage=0,
            right_coverage=0,
        )
        article.tags.add('ai-assisted-source-article')
        from .category_taxonomy import item_categories
        article.categories.add(*(categories if categories is not None else item_categories(item.get('categories'), source.default_category)))
        if not image_url:
            from .photo_library import cached_fallback, resolve_photo
            photograph = cached_fallback(article) or resolve_photo(article,source_first=False)
            if not photograph:
                raise ArticlePreparationError('A real topic photograph is still being prepared.',details={'code':'photo_unavailable'})
            NewsArticle.all_objects.filter(pk=article.pk).update(remote_image_url=photograph['image_url'],image_alt=photograph['image_alt'],image_credit=photograph['image_credit'],image_origin=photograph['image_origin'],image_reference_url=photograph['image_reference_url'])

        event(
            "publishing",
            "Imported article published",
            article=article,
            source=source,
            run=run,
            status="published",
            details={"selection": "google_trends_match" if item.get("trend") else "source_recency", "trend": item.get("trend")},
        )
    return "eligible" if dry_run else "created"


def tick():
    """One bounded work unit. Row lock fences all writes, including across instances.

    Each transaction publishes at most one article. The lock spans its network work: PostgreSQL releases
    the lock on process loss, so restart recovery cannot race a surviving worker.
    No background threads run inside serverless web requests.
    """
    config()
    AutomationWorkerLock.objects.get_or_create(pk=1)
    with transaction.atomic():
        AutomationWorkerLock.objects.select_for_update().get(pk=1)
        cfg = AutomationSettings.objects.get(pk=1)
        # Running rows are only visible here after a prior process committed one;
        # normal interrupted transactions roll back to the durable queued row.
        CrawlRun.objects.filter(status="running").update(
            status="failed",
            completed_at=timezone.now(),
            error="Interrupted worker recovered",
        )
        run = CrawlRun.objects.filter(status="queued").order_by("id").first()
        if not run and cfg.enabled and cfg.next_run <= timezone.now():
            run = CrawlRun.objects.create(trigger="scheduled")
        if not run:
            AutomationSettings.objects.filter(pk=1).update(worker_seen=timezone.now())
            return None
        paused_feed_retry = run.trigger == 'feed_retry' and (not cfg.enabled or not run.source_id or not NewsSource.objects.filter(pk=run.source_id, enabled=True).exists())
        if (run.trigger == 'scheduled' and not cfg.enabled) or paused_feed_retry:
            run.status = 'cancelled'
            run.completed_at = timezone.now()
            run.error = 'Automatic work paused; no articles were published.'
            run.save()
            if paused_feed_retry and run.source_id:
                from .models import PublishingRetry
                PublishingRetry.objects.filter(source_id=run.source_id, kind='feed', scope='scheduled', status='retrying').update(status='retry_wait', next_attempt=timezone.now())
            return run
        run.started_at = timezone.now()
        run.cycle_started_at = run.cycle_started_at or run.started_at
        on_demand = run.publishing_rules is not None
        cycle_end = run.cycle_started_at + (timedelta(hours=24) if on_demand else timedelta(minutes=cfg.interval_minutes))
        if run.parent_id and timezone.now() >= cycle_end:
            run.status, run.completed_at = 'cancelled', timezone.now()
            run.error = ('On-demand request expired after 24 hours. Review the published counts before starting another request.' if on_demand else 'Publishing interval ended; remaining work returns to the next scheduled cycle.')
            run.save()
            AutomationSettings.objects.filter(pk=1).update(worker_seen=timezone.now())
            return run
        run.status = "running"
        run.save()
        counts = dict(
            sources=0,
            fetched=0,
            invalid=0,
            duplicates=0,
            created=0,
            published=0,
            failed_sources=0,
            eligible=0,
            deferred=0,
            filtered=0,
            quota_skipped=0,
            capped_sources=0,
            previously_blocked=0,
            failed_articles=0, retries_waiting=0, blocked_articles=0, skipped=0, skip_reasons={},
        )
        generation_limit = 1
        # At most three candidates per allowed generation, two per publisher.
        budget = [generation_limit, generation_limit * 3, 2]
        sources = (
            NewsSource.objects.filter(enabled=True)
            .exclude(feed_url__isnull=True)
            .exclude(feed_url="")
            .order_by(F('last_attempt').asc(nulls_first=True), 'id')
        )
        if run.source_id:
            sources = (
                NewsSource.objects.filter(pk=run.source_id)
                if run.trigger == "test"
                else sources.filter(pk=run.source_id)
            )
        if on_demand:
            # Explicit requests can use paused schedule sources. They never turn
            # on that source's schedule or replace the saved automation policy.
            sources = NewsSource.objects.filter(pk__in={rule['source'] for rule in run.publishing_rules}).exclude(feed_url__isnull=True).exclude(feed_url='').order_by(F('last_attempt').asc(nulls_first=True), 'id')
        elif cfg.publishing_rules and run.trigger != 'test' and not any(rule['source'] == 0 for rule in cfg.publishing_rules):
            # A rule set is the editor's selected publishing source list.
            from .source_feeds import source_group_ids
            sources = sources.filter(pk__in=source_group_ids(rule['source'] for rule in cfg.publishing_rules))
        if run.pending_sources is not None:
            sources = sources.filter(pk__in=run.pending_sources)
        source_ids = list(sources.values_list('id', flat=True))
        pending = []
        from .category_taxonomy import item_categories
        from .publishing_limits import source_limits, publishing_window, matching_limits, has_capacity, source_has_capacity, record_publication, matching_source_rules
        # Process at most 20 feeds per run; oldest attempts first prevent starvation
        # when the configured collection contains more regions than a single batch.
        from .publishing_retries import record_failure, pending_issue, feed_issue, resolve_feed
        from .publishing_failures import failure_info
        def note_skip(reason, message):
            counts['skipped'] += 1
            counts['skip_reasons'][reason] = counts['skip_reasons'].get(reason, 0) + 1
            if counts['skip_reasons'][reason] == 1:
                event('publishing', message, run=run, status='skipped', details={'reason_code': reason})
        visited = set()
        category_ids = tuple(NewsCategory.objects.values_list('pk', flat=True))
        for source in sources[:20]:
            if not run.dry_run and (budget[0] <= 0 or budget[1] <= 0):
                # Leave unvisited feeds' attempt times unchanged so the next run
                # selects them before publishers already given a turn.
                break
            if not run.dry_run and timezone.now() >= cycle_end:
                break
            visited.add(source.pk)
            if on_demand:
                from types import SimpleNamespace
                rules = [r for r in run.publishing_rules if r['source'] == source.pk]
                limits = {str(r['category']) if r['category'] is not None else '*': r['limit'] for r in rules}
                state = run.request_state.setdefault(str(source.pk), {'counts': {}, 'attempted': []})
                window = SimpleNamespace(counts=state['counts'], attempted=state['attempted'])
            else:
                limits = source_limits(cfg, source.pk, category_ids)
                window = publishing_window(source, run.cycle_started_at, cfg.interval_minutes) if run.trigger != 'test' else None
            if window and not source_has_capacity(window, limits):
                counts['capped_sources'] += 1
                note_skip('quota_reached', 'Source skipped: interval quota is already filled.')
                continue
            feed_retry = feed_issue(source, run)
            if run.trigger not in ('test', 'manual') and feed_retry and (feed_retry.status in ('blocked', 'exhausted') or feed_retry.next_attempt and feed_retry.next_attempt > timezone.now()):
                counts['retries_waiting'] += 1
                note_skip('feed_retry_pending', 'Feed skipped: waiting for its retry time or configuration review.')
                continue
            budget[2] = 2
            counts["sources"] += 1
            source.last_attempt = timezone.now()
            try:
                per_category = not on_demand and any(rule['category'] is None for rule in matching_source_rules(cfg.publishing_rules, source.pk))
                items = feed_batch(source, cfg, run)[: (100 if on_demand or per_category else cfg.max_per_source)]
                from .seo import words
                from .trends import rank_items
                items = rank_items(items, source, enabled=cfg.trends_enabled)
                source.feed_quality = {'checked_at': timezone.now().isoformat(), 'entries': len(items), 'full_text_entries': sum(len(words(item.get('source_text') or item.get('content') or '')) >= 400 for item in items)}
                resolve_feed(source, run)
                eligible = 0
                for item_index, item in enumerate(items):
                    counts["fetched"] += 1
                    if not run.dry_run and timezone.now() >= cycle_end:
                        break
                    token = hashlib.sha256(str(item.get('article_url') or item).encode()).hexdigest()
                    issue = pending_issue(source, item, run) if not run.dry_run else None
                    if issue:
                        if issue.status in ('blocked', 'exhausted'):
                            counts['blocked_articles'] += 1
                            note_skip('article_review_pending', 'Article skipped: the existing blocker needs editorial/configuration review.')
                        else:
                            counts['retries_waiting'] += 1
                            note_skip('article_retry_pending', 'Article skipped: a durable retry already exists.')
                        eligible += 1
                        continue
                    if window and token in window.attempted:
                        counts['previously_blocked'] += 1
                        note_skip('previously_blocked', 'Article skipped: already attempted in this interval.')
                        eligible += 1
                        continue
                    categories = item_categories(item.get('categories'), source.default_category)
                    keys = matching_limits(categories, limits)
                    if on_demand and keys and '*' not in limits:
                        # A multi-category report fills one requested slot, not
                        # two quantities at once. Keep its full public taxonomy.
                        available = [key for key in keys if has_capacity(window, limits, [key])]
                        if available:
                            keys = available[:1]
                    if window and item.get('title') and not keys:
                        counts['filtered'] += 1
                        note_skip('category_mismatch', 'Article skipped: category does not match the saved publishing rules.')
                        eligible += 1
                        continue
                    if window and item.get('title') and not has_capacity(window, limits, keys):
                        counts['quota_skipped'] += 1
                        note_skip('quota_reached', 'Article skipped: source/category quota is already filled.')
                        eligible += 1
                        continue
                    try:
                        with transaction.atomic():
                            result = store_item(item, source, run.dry_run, run=run, budget=budget, categories=categories)
                        counts[result] += 1
                        if result == 'duplicates': note_skip('duplicate', 'Article skipped: the report is already stored.')
                        eligible += 1
                        if result == "created" and not run.dry_run:
                            counts["published"] += 1
                        if window and result in ('created', 'eligible'):
                            credited = [category for category in categories if str(category.pk) in keys] if on_demand and '*' not in limits else categories
                            record_publication(window, credited)
                        if result == 'deferred' and source.pk not in pending:
                            pending.append(source.pk)
                        if result == 'created' and not run.dry_run:
                            # Commit this publication and quota before performing
                            # any network/writing work for another article.
                            if window and source_has_capacity(window, limits) and item_index + 1 < len(items):
                                pending.append(source.pk)
                            break
                    except Exception as exc:
                        if isinstance(exc, DatabaseError): raise
                        counts['invalid'] += 1
                        info = failure_info(exc)
                        if window and token not in window.attempted:
                            window.attempted = [*window.attempted, token][-1000:]
                        if info['code'] == 'invalid_record':
                            note_skip('invalid_record', info['message'])
                        elif not run.dry_run:
                            retry = record_failure(source, item, run, exc)
                            counts['failed_articles'] += 1
                            if retry and retry.status == 'retry_wait': counts['retries_waiting'] += 1
                            else: counts['blocked_articles'] += 1
                        eligible += 1
                source.last_success = timezone.now()
                source.last_error = ""
                source.retrieved = len(items)
                event(
                    "source", "Feed fetched", source=source, run=run, status="success"
                )
            except Exception as exc:
                if isinstance(exc, DatabaseError): raise
                counts['failed_sources'] += 1
                info = failure_info(exc, 'feed')
                source.last_error = info['message']
                record_failure(source, {}, run, exc, kind='feed')
                event('source', info['message'], source=source, run=run, severity='error', status='failed', details={'reason_code': info['code'], 'retryable': info['retryable']})
            source.save(
                update_fields=[
                    "last_attempt",
                    "last_success",
                    "last_error",
                    "retrieved", "feed_quality",
                ]
            )
            if window and not run.dry_run:
                if on_demand:
                    run.request_state[str(source.pk)] = {'counts': window.counts, 'attempted': window.attempted}
                else:
                    window.save()
        pending.extend(source_id for source_id in source_ids if source_id not in visited and source_id not in pending)
        run.counts = counts
        run.completed_at = timezone.now()
        run.duration = (run.completed_at - run.started_at).total_seconds()
        failed = counts["failed_sources"] + counts["failed_articles"]
        run.status = (
            (
                "partial"
                if counts["sources"] > counts["failed_sources"]
                and counts["fetched"] > counts["invalid"]
                else "failed"
            )
            if failed
            else "success"
        )
        if not counts["sources"] and not counts['capped_sources'] and not counts['retries_waiting']:
            run.status, run.error = 'failed', ('No enabled feeds match the selected publishing rules.' if cfg.publishing_rules else 'No enabled RSS sources configured.')
        if on_demand and not run.dry_run and not pending:
            requested = sum(rule['limit'] for rule in run.publishing_rules)
            published = sum(state['counts'].get('*', 0) for state in run.request_state.values())
            if published < requested and run.status == 'success':
                run.status = 'partial'
                run.error = f'Published {published} of {requested} requested articles. No more eligible new reports matched; review categories, source availability and publishing errors.'
        run.save()
        event(
            "crawl",
            "Crawler run completed",
            run=run,
            status=run.status,
            severity="error" if run.status == "failed" else "info",
            details=counts,
        )
        if not run.parent_id and run.trigger == 'scheduled':
            AutomationSettings.objects.filter(pk=1, revision=cfg.revision).update(next_run=cycle_end)
        if pending and (not run.dry_run or on_demand) and timezone.now() < cycle_end:
            CrawlRun.objects.create(trigger=run.trigger, source=run.source,
                cycle_started_at=run.cycle_started_at, pending_sources=pending, parent=run,
                publishing_rules=run.publishing_rules, request_state=run.request_state,
                request_key=run.request_key, dry_run=run.dry_run)
        AutomationSettings.objects.filter(pk=1).update(worker_seen=timezone.now())
        cutoff = timezone.now() - timedelta(days=90)
        AdminEvent.objects.filter(timestamp__lt=cutoff).delete()
        CrawlRun.objects.filter(completed_at__lt=cutoff).delete()
        return run
