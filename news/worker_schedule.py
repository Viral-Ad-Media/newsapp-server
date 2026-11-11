"""One small readiness query; execution fences are acquired only for ready work."""
import os
from functools import lru_cache
from django.conf import settings
from django.db import connection
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import (AutomationSettings, CrawlRun, ArticlePreparation, PublishingRetry,
                     TrendSnapshot, SourcePhotoCheck, NewsArticle)


def idle_limit():
    # Bounded by the five-minute heartbeat health threshold. Use 30 for the
    # previous maximum manual-request latency, or 60 for lower idle overhead.
    return max(5, min(120, int(os.environ.get('NEWS_WORKER_IDLE_MAX_SECONDS', '60'))))


PROBE_FIELDS = ('enabled', 'trends_enabled', 'next_run', 'queued', 'preparing',
                'retry_at', 'trend_at', 'photo_at', 'missing_photo')


@lru_cache(maxsize=4)
def _readiness_sql(vendor):
    # Cache the SQL shape, never readiness results. This avoids rebuilding and
    # compiling six ORM subqueries on every idle poll; every call still reads
    # fresh state, so a web request in another process is visible immediately.
    authorized = Q(manual=True) | Q(scope__regex=r'^[0-9a-f-]{36}$')
    authorized |= Q(source__enabled=True) & Q(source__enabled=OuterRef('enabled'))
    retries = PublishingRetry.objects.filter(authorized, status__in=('queued', 'retry_wait'),
                                            next_attempt__isnull=False).order_by('next_attempt')
    photos = SourcePhotoCheck.objects.filter(article__archived=False,
        status__in=('pending', 'retry', 'searching'), next_attempt__isnull=False).order_by('next_attempt')
    missing_photos = NewsArticle.objects.filter(Q(photo_check__isnull=True) |
        Q(photo_check__hosted=False, photo_check__next_attempt__isnull=True))
    # Return scalar readiness/deadlines, never rules, article bodies or retry JSON.
    probe = AutomationSettings.objects.filter(pk=1).annotate(
        queued=Exists(CrawlRun.objects.filter(status='queued')),
        preparing=Exists(ArticlePreparation.objects.filter(status='queued')),
        retry_at=Subquery(retries.values('next_attempt')[:1]),
        trend_at=Subquery(TrendSnapshot.objects.order_by('next_attempt').values('next_attempt')[:1]),
        photo_at=Subquery(photos.values('next_attempt')[:1]),
        missing_photo=Exists(missing_photos),
    ).values(*PROBE_FIELDS)[:1]
    return probe.query.sql_with_params()


def _readiness_row():
    sql, params = _readiness_sql(connection.vendor)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        values = cursor.fetchone()
    if values is None:
        return None
    row = dict(zip(PROBE_FIELDS, values))
    # PostgreSQL returns datetimes; SQLite's raw cursor returns ISO strings.
    for key in ('next_run', 'retry_at', 'trend_at', 'photo_at'):
        value = row[key]
        if isinstance(value, str):
            value = parse_datetime(value)
        if value is not None and settings.USE_TZ and timezone.is_naive(value):
            value = timezone.make_aware(value, connection.timezone)
        row[key] = value
    return row


def work_plan(now=None):
    now = now or timezone.now()
    row = _readiness_row()
    if row is None:
        AutomationSettings.objects.get_or_create(pk=1)
        row = _readiness_row()
    schedule = row['next_run'] if row['enabled'] else None
    discover_trends = bool(row['trends_enabled'] and (row['enabled'] or row['queued']))
    trend = row['trend_at'] if discover_trends else None
    retry, photo = row['retry_at'], row['photo_at']
    deadlines = [value for value in (retry, trend, schedule, photo) if value and value > now]
    return {'publish': bool(row['queued'] or (schedule and schedule <= now)),
            'retry': bool(retry and retry <= now), 'prepare': bool(row['preparing']),
            'trends': bool(trend and trend <= now), 'discover_trends': discover_trends,
            'images': bool(row['missing_photo'] or (photo and photo <= now)),
            'wait': max(1, min(idle_limit(), (min(deadlines)-now).total_seconds())) if deadlines else idle_limit()}


def idle_delay(idle_count, next_due=None):
    limit = idle_limit()
    return max(1, min(limit, 5 * (2 ** min(idle_count, 5)), limit if next_due is None else next_due))
