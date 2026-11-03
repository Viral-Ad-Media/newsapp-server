"""One-off publishing plans and honest progress, independent of the schedule."""
from datetime import timedelta
from django.utils import timezone
from rest_framework import serializers
from .models import NewsSource, CrawlRun, AdminEvent


class OnDemandInput(serializers.Serializer):
    from .admin_api import PublishingRuleInput
    rules = PublishingRuleInput(many=True, allow_empty=False)
    dry_run = serializers.BooleanField(default=False)

    def validate_rules(self, rules):
        if any(rule['source'] == 0 for rule in rules):
            raise serializers.ValidationError('Choose explicit sources for a one-off request.')
        from .admin_api import SettingsInput
        rules = SettingsInput().validate_publishing_rules(rules)
        sources = NewsSource.objects.filter(pk__in={r['source'] for r in rules})
        if sources.filter(feed_url__isnull=True).exists() or sources.filter(feed_url='').exists():
            raise serializers.ValidationError('Every selected source needs a configured RSS/Atom feed. Configure it in Sources first.')
        return rules


def publishing_readiness():
    from .automation import config
    from .utils.ai_summarizer import client
    cfg = config()
    return {
        'on_demand_supported': True,
        'writing_configured': client is not None,
        'worker_healthy': bool(cfg.worker_seen and cfg.worker_seen > timezone.now() - timedelta(minutes=5)),
        'worker_seen': cfg.worker_seen,
        'feed_sources': NewsSource.objects.exclude(feed_url__isnull=True).exclude(feed_url='').count(),
    }


def run_progress(run):
    if run.request_key:
        chain = list(CrawlRun.objects.filter(request_key=run.request_key).order_by('pk'))
        root = chain[0]
    else:
        root = run
        while root.parent_id:
            root = root.parent
        chain, current = [], root
        while current and len(chain) < 20000:
            chain.append(current)
            current = current.continuations.order_by('pk').first()
    last = chain[-1]
    active = next((r for r in chain if r.status in ('queued', 'running')), None)
    totals = {}
    for batch in chain:
        for key, value in batch.counts.items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
    rules = root.publishing_rules or []
    requested = sum(r['limit'] for r in rules)
    published = totals.get('published', 0)
    from .models import PublishingRetry
    retries = PublishingRetry.objects.filter(scope=str(root.request_key)) if root.request_key else PublishingRetry.objects.none()
    waiting_retries = retries.filter(status__in=('queued', 'retry_wait', 'retrying')).count()
    unmet = max(0, requested - published)
    status = active.status if active else last.status
    if waiting_retries and not active and status != 'cancelled':
        status = 'retry_wait'
    if rules and not root.dry_run and not active and unmet and status == 'success':
        status = 'partial'
    problems = list(AdminEvent.objects.filter(run_id__in=[r.pk for r in chain], severity__in=['warning', 'error']).order_by('-pk').values('message', 'details')[:20])
    source_ids = {r['source'] for r in rules}
    names = dict(NewsSource.objects.filter(pk__in=source_ids).values_list('pk', 'name'))
    return {
        'root_id': root.pk, 'active_run_id': active.pk if active else None,
        'status': status, 'dry_run': root.dry_run, 'requested': requested,
        'published': published, 'remaining': unmet, 'counts': totals,
        'batches': len(chain), 'rules': rules, 'waiting_retries': waiting_retries,
        'sources': [{'id': pk, 'name': names.get(pk, 'Removed source'), 'published': last.request_state.get(str(pk), {}).get('counts', {}).get('*', 0)} for pk in sorted(source_ids)],
        'error': last.error, 'problems': problems,
        **publishing_readiness(),
    }
