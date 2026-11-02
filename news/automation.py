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


def queue_run(trigger="manual", source=None, actor=None, dry_run=False):
    config()
    with transaction.atomic():
        AutomationSettings.objects.select_for_update().get(pk=1)
        active = CrawlRun.objects.filter(status__in=['queued', 'running']).order_by('id').first()
        if active:
            raise Busy(f'Run #{active.pk} is already queued or executing. Wait for it to finish, or use Cancel pending run to clear a waiting job before starting another.')
        run = CrawlRun.objects.create(trigger=trigger, source=source, dry_run=dry_run)
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
    from .utils.ai_summarizer import client
    if client is None:
        raise ArticlePreparationError("OPENAI_API_KEY is not configured; full article cannot be prepared.")
    evidence = item.get("source_text") or item.get("content") or ""
    if len(words(evidence)) < 400:
        evidence = source_evidence(url, source.url)
    if budget is not None:
        budget[0] -= 1
    prepared = prepare_article(title=title, summary=summary, evidence=evidence, source_name=source.name, source_url=url)
    if not dry_run:
        article = NewsArticle.all_objects.create(
            title=title,
            description=summary,
            content=prepared["content"],
            focus_keyword=prepared["focus_keyword"],
            seo_title=prepared["seo_title"],
            seo_description=prepared["seo_description"],
            source=source,
            article_url=url,
            ingestion_key=key,
            imported=True,
            published_at=item["published_at"],
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
        event(
            "publishing",
            "Imported article published",
            article=article,
            source=source,
            run=run,
            status="published",
        )
    return "eligible" if dry_run else "created"


def tick():
    """One bounded work unit. Row lock fences all writes, including across instances.

    The transaction is held through network work deliberately: PostgreSQL releases
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
        if run.trigger == "scheduled" and not cfg.enabled:
            run.status = "cancelled"
            run.completed_at = timezone.now()
            run.save()
            return run
        run.started_at = timezone.now()
        run.cycle_started_at = run.cycle_started_at or run.started_at
        cycle_end = run.cycle_started_at + timedelta(minutes=cfg.interval_minutes)
        if run.parent_id and timezone.now() >= cycle_end:
            run.status, run.completed_at = 'cancelled', timezone.now()
            run.error = 'Publishing interval ended; remaining work returns to the next scheduled cycle.'
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
        )
        generation_limit = getattr(settings, 'ARTICLE_GENERATION_PER_RUN', 2)
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
        if cfg.publishing_rules and run.trigger != 'test':
            # A rule set is the editor's selected publishing source list.
            sources = sources.filter(pk__in={rule['source'] for rule in cfg.publishing_rules})
        if run.pending_sources is not None:
            sources = sources.filter(pk__in=run.pending_sources)
        source_ids = list(sources.values_list('id', flat=True))
        pending = []
        from .category_taxonomy import item_categories
        from .publishing_limits import source_limits, publishing_window, matching_limits, has_capacity, source_has_capacity, record_publication
        # Process at most 20 feeds per run; oldest attempts first prevent starvation
        # when the configured collection contains more regions than a single batch.
        visited = set()
        for source in sources[:20]:
            if not run.dry_run and (budget[0] <= 0 or budget[1] <= 0):
                # Leave unvisited feeds' attempt times unchanged so the next run
                # selects them before publishers already given a turn.
                break
            if not run.dry_run and timezone.now() >= cycle_end:
                break
            visited.add(source.pk)
            limits = source_limits(cfg, source.pk)
            window = publishing_window(source, run.cycle_started_at, cfg.interval_minutes) if run.trigger != 'test' else None
            if window and not source_has_capacity(window, limits):
                counts['capped_sources'] += 1
                continue
            budget[2] = 2
            counts["sources"] += 1
            source.last_attempt = timezone.now()
            try:
                items = source_items(source, cfg.timeout_seconds)[: cfg.max_per_source]
                eligible = 0
                for item in items:
                    counts["fetched"] += 1
                    if not run.dry_run and timezone.now() >= cycle_end:
                        break
                    token = hashlib.sha256(str(item.get('article_url') or item).encode()).hexdigest()
                    if window and token in window.attempted:
                        counts['previously_blocked'] += 1
                        eligible += 1
                        continue
                    categories = item_categories(item.get('categories'), source.default_category)
                    keys = matching_limits(categories, limits)
                    if window and item.get('title') and not keys:
                        counts['filtered'] += 1
                        eligible += 1
                        continue
                    if window and item.get('title') and not has_capacity(window, limits, keys):
                        counts['quota_skipped'] += 1
                        eligible += 1
                        continue
                    try:
                        with transaction.atomic():
                            result = store_item(item, source, run.dry_run, run=run, budget=budget, categories=categories)
                        counts[result] += 1
                        eligible += 1
                        if result == "created" and not run.dry_run:
                            counts["published"] += 1
                        if window and result in ('created', 'eligible'):
                            record_publication(window, categories)
                        if result == 'deferred' and source.pk not in pending:
                            pending.append(source.pk)
                    except (ValueError, TypeError) as exc:
                        from .article_writing import ArticlePreparationError
                        counts["invalid"] += 1
                        if window and token not in window.attempted:
                            window.attempted = [*window.attempted, token][-1000:]
                        if isinstance(exc, ArticlePreparationError):
                            event("publishing", "Full article preparation blocked", source=source, run=run, status="blocked", severity="warning", details={"reason": str(exc)[:500]})
                if not eligible:
                    raise ValueError("No eligible records")
                source.last_success = timezone.now()
                source.last_error = ""
                source.retrieved = len(items)
                event(
                    "source", "Feed fetched", source=source, run=run, status="success"
                )
            except Exception:
                counts["failed_sources"] += 1
                # Never persist exception strings: remote URLs may carry secrets.
                source.last_error = "Feed fetch or processing failed. Check HTTPS URL, XML format, network and publisher limits."
                event(
                    "source",
                    source.last_error,
                    source=source,
                    run=run,
                    severity="error",
                    status="failed",
                )
            source.save(
                update_fields=[
                    "last_attempt",
                    "last_success",
                    "last_error",
                    "retrieved",
                ]
            )
            if window and not run.dry_run:
                window.save()
        pending.extend(source_id for source_id in source_ids if source_id not in visited and source_id not in pending)
        run.counts = counts
        run.completed_at = timezone.now()
        run.duration = (run.completed_at - run.started_at).total_seconds()
        failed = counts["failed_sources"] + counts["invalid"]
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
        if not counts["sources"] and not counts['capped_sources']:
            run.status, run.error = 'failed', ('No enabled feeds match the selected publishing rules.' if cfg.publishing_rules else 'No enabled RSS sources configured.')
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
        if pending and not run.dry_run and timezone.now() < cycle_end:
            CrawlRun.objects.create(trigger=run.trigger, source=run.source,
                cycle_started_at=run.cycle_started_at, pending_sources=pending, parent=run)
        AutomationSettings.objects.filter(pk=1).update(worker_seen=timezone.now())
        cutoff = timezone.now() - timedelta(days=90)
        AdminEvent.objects.filter(timestamp__lt=cutoff).delete()
        CrawlRun.objects.filter(completed_at__lt=cutoff).delete()
        return run
