"""Durable per-story failures and bounded, policy-aware retry processing."""
import hashlib
from datetime import timedelta
from django.db import transaction
from django.db.models import Q, Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import PublishingRetry, WritingServiceHealth, NewsSource, CrawlRun, AutomationWorkerLock, NewsArticle
from .publishing_failures import failure_info, retry_time, MESSAGES, WRITING

MAX_ATTEMPTS=5
OPEN=('queued','retry_wait','retrying','blocked','exhausted')


def root_run(run):
    if run and run.request_key:
        return CrawlRun.objects.filter(request_key=run.request_key).order_by('pk').first() or run
    if run and run.parent_id and run.cycle_started_at:
        root = CrawlRun.objects.filter(parent__isnull=True, trigger=run.trigger, source_id=run.source_id, cycle_started_at=run.cycle_started_at, pk__lte=run.pk).order_by('-pk').first()
        if root:
            return root
    while run and run.parent_id:
        run=run.parent
    return run


def scope_for(run):
    return str(run.request_key) if run and run.request_key else 'scheduled'


def writing_health():
    from .utils.ai_summarizer import client
    obj=WritingServiceHealth.objects.filter(pk=1).first()
    return {'configured':client is not None,'code':obj.code if obj else 'not_checked','message':obj.message if obj else 'No successful writing request recorded yet.','checked_at':obj.checked_at if obj else None,'last_success':obj.last_success if obj else None,'retry_after':obj.retry_after if obj else None}


def writing_hold():
    return WritingServiceHealth.objects.filter(pk=1,retry_after__gt=timezone.now()).first()


def mark_writing_success():
    WritingServiceHealth.objects.update_or_create(pk=1,defaults={'code':'ok','message':'Last article writing request completed successfully.','checked_at':timezone.now(),'last_success':timezone.now(),'retry_after':None})


def record_failure(source, item, run, exc, kind='article'):
    if run and run.dry_run:
        return None
    from .automation import canonical_url
    info=failure_info(exc, 'feed' if kind == 'feed' else 'source')
    try: url=canonical_url(source.feed_url if kind=='feed' else item.get('article_url'))
    except ValueError: return None
    key=hashlib.sha256(url.encode()).hexdigest()
    obj,_=PublishingRetry.objects.get_or_create(source=source,kind=kind,key=key,scope=scope_for(run),defaults={'run':root_run(run)})
    if obj.status in ('published','resolved','skipped'):
        obj.attempts=0
    held=bool((getattr(exc,'details',{}) or {}).get('held'))
    if not held:
        obj.attempts+=1
    obj.item={key:item[key] for key in ('title','description','content','source_text','author','article_url','published_at','categories','remote_image_url','image_alt','image_credit','trend','_prepared_article','_prepared_fingerprint') if key in item}
    obj.reason_code,obj.reason=info['code'],info['message']
    obj.status='retry_wait' if info['retryable'] else 'blocked'
    obj.next_attempt=retry_time(max(1,obj.attempts),info) if info['retryable'] else None
    if obj.attempts>=MAX_ATTEMPTS:
        obj.status,obj.next_attempt='exhausted',None
    obj.updated_at=timezone.now();obj.save()
    if info['writing'] and not held:
        cooldown=obj.next_attempt if info['retryable'] else timezone.now()+timedelta(hours=1)
        WritingServiceHealth.objects.update_or_create(pk=1,defaults={'code':info['code'],'message':info['message'],'checked_at':timezone.now(),'retry_after':cooldown})
    from .automation import event
    event('publishing' if kind=='article' else 'source','Article retry scheduled' if obj.status=='retry_wait' else 'Article publication blocked' if kind=='article' else 'Feed retrieval blocked',source=source,run=run,severity='warning',status=obj.status,details={'retry_id':obj.pk,'reason_code':obj.reason_code,'reason':obj.reason,'attempts':obj.attempts,'next_attempt':obj.next_attempt.isoformat() if obj.next_attempt else None})
    return obj


def pending_issue(source,item,run):
    from .automation import canonical_url
    try: key=hashlib.sha256(canonical_url(item.get('article_url')).encode()).hexdigest()
    except ValueError: return None
    return PublishingRetry.objects.filter(source=source,kind='article',key=key,scope=scope_for(run),status__in=OPEN).first()


def feed_issue(source,run):
    return PublishingRetry.objects.filter(source=source,kind='feed',scope=scope_for(run),status__in=OPEN).first()


def resolve_feed(source,run):
    PublishingRetry.objects.filter(source=source,kind='feed',scope=scope_for(run),status__in=OPEN).update(status='resolved',reason_code='',reason='Feed fetched successfully.',next_attempt=None,updated_at=timezone.now())


def retry_summary():
    counts={row['status']:row['total'] for row in PublishingRetry.objects.values('status').annotate(total=Count('pk'))}
    return {'waiting':counts.get('retry_wait',0)+counts.get('queued',0)+counts.get('retrying',0),'blocked':counts.get('blocked',0),'exhausted':counts.get('exhausted',0),'retryable_articles':PublishingRetry.objects.filter(kind='article',status__in=('retry_wait','blocked','exhausted')).count()}


def requeue_failed(actor=None):
    from .automation import event
    # It only stages jobs; no publisher or writing calls happen in an API request.
    with transaction.atomic():
        changed=PublishingRetry.objects.filter(kind='article',status__in=('retry_wait','blocked','exhausted')).update(status='queued',attempts=0,next_attempt=timezone.now(),manual=True,updated_at=timezone.now())
        if changed:
            WritingServiceHealth.objects.filter(pk=1).update(retry_after=None)
        event('audit','Failed articles queued for retry',actor=actor,details={'queued':changed})
    return changed


def _finish(job,status,code,message,run=None):
    job.status=status;job.reason_code=code;job.reason=message;job.next_attempt=None;job.updated_at=timezone.now();job.save()
    from .automation import event
    event('publishing',message,source=job.source,run=run,status=status,details={'retry_id':job.pk,'reason_code':code})


def tick_retries():
    """One article per transaction; a failed retry never rolls back prior articles."""
    from .automation import config, store_item, event
    from .category_taxonomy import item_categories
    from .publishing_limits import source_limits,publishing_window,matching_limits,has_capacity,record_publication
    AutomationWorkerLock.objects.get_or_create(pk=1)
    with transaction.atomic():
        AutomationWorkerLock.objects.select_for_update().get(pk=1)
        cfg=config()
        due=PublishingRetry.objects.select_for_update().select_related('source','run').filter(status__in=('queued','retry_wait'),next_attempt__lte=timezone.now())
        # Scheduled automatic work respects pause/source switches; explicit manual
        # requests and on-demand plans retain their independent authorization.
        authorized = Q(manual=True) | Q(scope__regex=r'^[0-9a-f-]{36}$')
        if cfg.enabled:
            authorized |= Q(source__enabled=True)
        due=due.filter(authorized)
        job=due.order_by('next_attempt','pk').first()
        if not job: return None
        root=job.run
        demand=bool(root and root.request_key)
        if job.scope != 'scheduled' and not demand:
            _finish(job,'skipped','request_expired','Retry skipped: original request is no longer available.');return job
        latest=CrawlRun.objects.filter(request_key=root.request_key).order_by('-pk').first() if demand else None
        if demand and CrawlRun.objects.filter(request_key=root.request_key,status__in=('queued','running')).exists():
            return None  # Never overwrite a continuation's quota snapshot with a retry.
        if demand and (latest.status=='cancelled' or timezone.now()> (root.cycle_started_at or root.created_at)+timedelta(hours=24)):
            _finish(job,'skipped','request_expired','Retry skipped: the original request expired or was cancelled.');return job
        if job.kind=='feed':
            if CrawlRun.objects.filter(status__in=('queued','running')).exists():return None
            values={'source':job.source,'trigger':'feed_retry'}
            if demand:
                values.update(trigger='on_demand',parent=latest,request_key=root.request_key,publishing_rules=root.publishing_rules,request_state=latest.request_state,cycle_started_at=root.cycle_started_at,pending_sources=[job.source_id])
            CrawlRun.objects.create(**values)
            job.status='retrying';job.next_attempt=None;job.save();return job
        item=dict(job.item)
        if isinstance(item.get('published_at'),str):item['published_at']=parse_datetime(item['published_at'])
        categories=item_categories(item.get('categories'),job.source.default_category)
        if demand:
            from types import SimpleNamespace
            state=dict(latest.request_state)
            source_state=state.setdefault(str(job.source_id),{'counts':{},'attempted':[]})
            window=SimpleNamespace(counts=dict(source_state['counts']),attempted=source_state['attempted'])
            limits={str(r['category']) if r['category'] is not None else '*':r['limit'] for r in root.publishing_rules if r['source']==job.source_id}
        else:
            from .publishing_limits import matching_source_rules
            if cfg.publishing_rules and not matching_source_rules(cfg.publishing_rules, job.source_id):
                _finish(job,'skipped','source_not_selected','Retry skipped: source is outside the saved publishing rules.');return job
            limits=source_limits(cfg,job.source_id)
            window=publishing_window(job.source,timezone.now(),cfg.interval_minutes)
        keys=matching_limits(categories,limits)
        if demand and '*' not in limits:
            available=[key for key in keys if has_capacity(window,limits,[key])]
            if available: keys=available[:1]
        if not keys:
            _finish(job,'skipped','category_mismatch','Retry skipped: no selected category matches this source report.');return job
        # Duplicates must resolve even if the interval quota is already full.
        from .automation import canonical_url
        try: url=canonical_url(item.get('article_url'))
        except ValueError:
            _finish(job,'blocked','invalid_record',MESSAGES['invalid_record']);return job
        if NewsArticle.all_objects.filter(Q(article_url=url)|Q(ingestion_key=job.key)|Q(title=item.get('title'),source=job.source)).exists():
            _finish(job,'skipped','duplicate','Retry skipped: article is already stored.');return job
        if not has_capacity(window,limits,keys):
            if demand:
                _finish(job,'skipped','quota_reached','Retry skipped: the original request category target is already fulfilled.');return job
            job.next_attempt=max(timezone.now()+timedelta(seconds=5),window.started_at+timedelta(minutes=cfg.interval_minutes))
            job.status='retry_wait';job.reason_code='quota_reached';job.reason='Waiting for the source/category publishing quota to renew.';job.save();return job
        hold=writing_hold()
        if hold:
            job.next_attempt=hold.retry_after;job.status='retry_wait';job.reason_code=hold.code;job.reason=hold.message;job.save();return job
        # A distinct run keeps original counters honest and exposes actual outcomes.
        run=CrawlRun.objects.create(trigger='article_retry',status='running',source=job.source,started_at=timezone.now(),cycle_started_at=root.cycle_started_at if demand else timezone.now(),parent=latest if demand else None,request_key=root.request_key if demand else None,publishing_rules=root.publishing_rules if demand else None,request_state=latest.request_state if demand else {})
        try:
            with transaction.atomic():
                result=store_item(item,job.source,run=run,categories=categories,budget=[1,1,1])
            if result=='created':
                credited=[c for c in categories if str(c.pk) in keys] if demand and '*' not in limits else categories
                record_publication(window,credited)
                if demand:
                    state[str(job.source_id)]={'counts':window.counts,'attempted':window.attempted};run.request_state=state
                else:window.save()
                _finish(job,'published','','Previously failed article published.',run)
                run.counts={'published':1,'created':1,'retries_succeeded':1};run.status='success'
            else:
                _finish(job,'skipped','duplicate','Retry skipped: article is already stored.',run);run.counts={'duplicates':1,'skipped':1};run.status='success'
        except Exception as exc:
            from django.db import DatabaseError
            if isinstance(exc,DatabaseError):raise
            record_failure(job.source,item,run,exc)
            run.counts={'failed_articles':1};run.status='failed';run.error=failure_info(exc)['message']
        run.completed_at=timezone.now();run.duration=(run.completed_at-run.started_at).total_seconds();run.save()
        from .models import AutomationSettings
        AutomationSettings.objects.filter(pk=1).update(worker_seen=timezone.now())
        return job
