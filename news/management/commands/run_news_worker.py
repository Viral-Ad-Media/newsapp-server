import os
from pathlib import Path
import signal
import time
from threading import Event
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connections
from django.utils import timezone
from news.photo_library import tick_images
from news.automation import tick
from news.article_jobs import tick_article
from news.publishing_retries import tick_retries
from news.trends import initialise_trends, tick_trends
from news.worker_schedule import work_plan, idle_delay
from news.models import AutomationSettings, PublicResponseCache, PublicContentRevision


class Command(BaseCommand):
    help = 'Process durable publishing jobs with adaptive idle waiting.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process one bounded batch, then exit.')

    def handle(self, *args, **options):
        stopped = False
        stop_event = Event()
        idle_count = 0
        next_images = next_maintenance = next_heartbeat = 0
        next_trend_init = 0
        report_started, cpu_started = time.monotonic(), time.process_time()
        report_at = report_started + 900
        loops = batches = 0

        def stop(*_):
            nonlocal stopped
            stopped = True
            stop_event.set()

        def progress():
            filename = os.environ.get('NEWS_WORKER_PROGRESS_FILE')
            if filename: Path(filename).touch()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopped:
            worked = False
            delay = 10
            try:
                clock = time.monotonic()
                plan = work_plan()
                loops += 1
                if plan.get('discover_trends', True) and clock >= next_trend_init:
                    initialise_trends()
                    next_trend_init = clock + 3600
                if clock >= next_maintenance:
                    PublicResponseCache.objects.filter(expires_at__lte=timezone.now()).delete()
                    cutoff = PublicContentRevision.objects.order_by('-pk').values_list('pk',flat=True)[5000:5001].first()
                    if cutoff: PublicContentRevision.objects.filter(pk__lt=cutoff).delete()
                    next_maintenance = clock + 3600
                if plan['publish']:
                    run = tick()
                    worked |= run is not None
                    if run: self.stdout.write(f'Run {run.pk}: {run.status}')
                    progress()
                if plan['retry']:
                    retried = tick_retries()
                    worked |= retried is not None
                    if retried: self.stdout.write(f'Retry {retried.pk}: {retried.status}')
                    progress()
                if plan['prepare']:
                    prepared = tick_article()
                    worked |= prepared is not None
                    if prepared: self.stdout.write(f'Article preparation {prepared.pk}: {prepared.status}')
                    progress()
                if plan['trends']:
                    tick_trends()
                    worked = True
                    progress()
                if plan.get('images', True) and clock >= next_images:
                    try:
                        photo = tick_images()
                        next_images = time.monotonic() + (15 if photo else 60)
                        worked |= photo is not None
                    except Exception as exc:
                        from news.worker_diagnostics import safe_worker_error
                        self.stderr.write('Photo processing: ' + safe_worker_error(exc))
                        next_images = time.monotonic() + 60
                    progress()
                if clock >= next_heartbeat:
                    AutomationSettings.objects.filter(pk=1).update(worker_seen=timezone.now())
                    next_heartbeat = time.monotonic() + 60
                idle_count = 0 if worked else idle_count + 1
                batches += int(worked)
                delay = 1 if worked else idle_delay(idle_count, plan['wait'])
                if plan.get('images', True):
                    delay = min(delay, max(1, next_images-time.monotonic()))
                delay = min(delay, max(1, next_heartbeat-time.monotonic()))
                if time.monotonic() >= report_at:
                    self.stdout.write(
                        f'Worker activity: loops={loops} work_batches={batches} '
                        f'process_cpu_seconds={time.process_time()-cpu_started:.3f} '
                        f'elapsed_seconds={time.monotonic()-report_started:.0f}'
                    )
                    report_started, cpu_started = time.monotonic(), time.process_time()
                    report_at = report_started + 900
                    loops = batches = 0
            except Exception as exc:
                from news.worker_diagnostics import safe_worker_error
                self.stderr.write(safe_worker_error(exc))
                connections.close_all()
                if options['once']: raise
            finally:
                close_old_connections()
                progress()
            if options['once']: return
            # A single timed wait replaces one wake-up per second. Signals wake
            # the event immediately, including while automation is paused.
            if not stopped:
                stop_event.wait(max(1, delay))
