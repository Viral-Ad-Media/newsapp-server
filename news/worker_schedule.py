"""Cheap readiness probes; do not acquire execution fences when no work is due."""
from django.db.models import Min, Q
from django.utils import timezone
from .models import AutomationSettings, CrawlRun, ArticlePreparation, PublishingRetry, TrendSnapshot


def work_plan(now=None):
    now = now or timezone.now()
    cfg, _ = AutomationSettings.objects.get_or_create(pk=1)
    queued = CrawlRun.objects.filter(status='queued').exists()
    authorized = Q(manual=True) | Q(scope__regex=r'^[0-9a-f-]{36}$')
    if cfg.enabled: authorized |= Q(source__enabled=True)
    retry = PublishingRetry.objects.filter(authorized,status__in=('queued','retry_wait')).aggregate(next=Min('next_attempt'))['next']
    trend = TrendSnapshot.objects.aggregate(next=Min('next_attempt'))['next'] if cfg.trends_enabled and (cfg.enabled or queued) else None
    schedule = cfg.next_run if cfg.enabled else None
    deadlines = [value for value in (retry,trend,schedule) if value is not None and value > now]
    return {'publish':queued or bool(schedule and schedule <= now),
            'retry':bool(retry and retry <= now),
            'prepare':ArticlePreparation.objects.filter(status='queued').exists(),
            'trends':bool(trend and trend <= now),
            'wait':max(1,min(30,(min(deadlines)-now).total_seconds())) if deadlines else 30}


def idle_delay(idle_count, next_due=30):
    return max(1,min(30,5*(2**min(idle_count,3)),next_due))
