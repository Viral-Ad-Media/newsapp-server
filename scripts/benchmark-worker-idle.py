"""Compare an idle hour locally, with an in-memory DB and no external requests.

Run from the repository root with Django installed. No production credentials are
read. Baseline is the reviewed version immediately before worker optimization.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BASELINE = 'fc6fb7d40b2c06d1298f28f2954983321755e136'
sys.path.insert(0, str(ROOT))
os.environ.pop('DATABASE_URL', None)
os.environ['DEBUG'] = 'True'
os.environ['NEWS_WORKER_IDLE_MAX_SECONDS'] = '60'
os.environ['DJANGO_SETTINGS_MODULE'] = 'newsapp.settings'
import django
django.setup()
from django.conf import settings
settings.DATABASES['default']['NAME'] = ':memory:'
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from datetime import timedelta
from news.automation import config
from news.models import NewsSource, NewsArticle, SourcePhotoCheck
from news.management.commands import run_news_worker as current


def old_module(name, path):
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:{path}'], cwd=ROOT, text=True)
    module = types.ModuleType(name)
    module.__package__ = name.rpartition('.')[0]
    exec(compile(source, path, 'exec'), module.__dict__)
    return module


def measure(module):
    clock, waits, photo_polls = 0.0, 0, 0
    handlers = {}
    reference = timezone.now()
    actual_photos = module.tick_images
    def photos():
        nonlocal photo_polls
        photo_polls += 1
        return actual_photos()
    def wait(seconds):
        nonlocal clock, waits
        waits += 1
        clock += seconds
        if clock >= 3600:
            handlers[module.signal.SIGTERM]()
    def register(sig, handler):
        handlers[sig] = handler
    with patch.object(module.signal, 'signal', side_effect=register), \
         patch.object(module.time, 'monotonic', side_effect=lambda: clock), \
         patch.object(module.time, 'sleep', side_effect=wait), \
         patch('threading.Event.wait', side_effect=wait), \
         patch('django.utils.timezone.now', side_effect=lambda: reference+timedelta(seconds=clock)), \
         patch.object(module, 'tick_images', side_effect=photos), \
         patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden in benchmark')), \
         CaptureQueriesContext(connection) as queries:
        start = time.process_time()
        module.Command(stdout=io.StringIO(), stderr=io.StringIO()).handle(once=False)
        cpu = time.process_time()-start
    return {'simulated_seconds': clock, 'sql_statements': len(queries),
            'wait_wakeups': waits, 'photo_polls': photo_polls,
            'local_process_cpu_seconds': round(cpu, 4)}


if __name__ == '__main__':
    call_command('migrate', verbosity=0)
    cfg = config()
    cfg.enabled = cfg.trends_enabled = False
    cfg.save()
    source = NewsSource.objects.create(name='Local benchmark', url='https://example.org')
    article = NewsArticle.all_objects.create(title='Local fixture', source=source)
    SourcePhotoCheck.objects.create(article=article, status='retry', hosted=False,
        next_attempt=timezone.now()+timedelta(hours=2))
    old_schedule = old_module('news.old_schedule', 'news/worker_schedule.py')
    baseline = old_module('news.management.commands.old_worker', 'news/management/commands/run_news_worker.py')
    baseline.work_plan, baseline.idle_delay = old_schedule.work_plan, old_schedule.idle_delay
    print(json.dumps({'baseline_commit': BASELINE, 'before': measure(baseline),
                      'after': measure(current)}, indent=2))
