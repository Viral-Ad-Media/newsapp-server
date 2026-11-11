import io
import os
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from .automation import config, queue_run
from .models import NewsSource, NewsArticle, SourcePhotoCheck, PublishingRetry
from .worker_schedule import work_plan, idle_delay, _readiness_sql


class WorkerReadinessTests(TestCase):
    def setUp(self):
        self.cfg = config()
        self.cfg.trends_enabled = False
        self.cfg.save()
        _readiness_sql.cache_clear()

    def test_readiness_is_one_query_and_cached_sql_reads_fresh_state(self):
        with self.assertNumQueries(1):
            self.assertFalse(work_plan()['publish'])
        queue_run(dry_run=True)
        with self.assertNumQueries(1):
            self.assertTrue(work_plan()['publish'])
        self.assertEqual(_readiness_sql.cache_info().misses, 1)

    def test_future_photo_retry_is_not_ready_and_no_article_body_is_loaded(self):
        source = NewsSource.objects.create(name='Photo publisher', url='https://example.org')
        article = NewsArticle.all_objects.create(title='Photo story', content='evidence '*1000, source=source)
        check = SourcePhotoCheck.objects.create(article=article, status='retry', hosted=False,
            next_attempt=timezone.now()+timedelta(hours=1))
        self.assertFalse(work_plan()['images'])
        check.next_attempt = timezone.now()-timedelta(seconds=1)
        check.save()
        self.assertTrue(work_plan()['images'])
        check.hosted = True
        check.status = 'source'
        check.next_attempt = None
        check.save()
        self.assertFalse(work_plan()['images'])
        article.archived = True
        article.save()
        check.hosted = False
        check.next_attempt = timezone.now()
        check.status = 'pending'
        check.save()
        self.assertFalse(work_plan()['images'])

    def test_new_photo_and_pending_deadline_are_preserved(self):
        source = NewsSource.objects.create(name='New publisher', url='https://example.org')
        article = NewsArticle.all_objects.create(title='New photo story', source=source)
        self.assertTrue(work_plan()['images'])
        now = timezone.now()
        SourcePhotoCheck.objects.create(article=article, status='retry', next_attempt=now+timedelta(seconds=8))
        plan = work_plan(now)
        self.assertFalse(plan['images'])
        self.assertEqual(plan['wait'], 8)
        self.assertEqual(idle_delay(20, plan['wait']), 8)

    def test_pause_and_source_authorization_are_not_cached(self):
        source = NewsSource.objects.create(name='Scheduled publisher', url='https://example.org', enabled=True)
        job = PublishingRetry.objects.create(source=source, key='scheduler-check', next_attempt=timezone.now())
        self.assertFalse(work_plan()['retry'])
        self.cfg.enabled = True
        self.cfg.save()
        self.assertTrue(work_plan()['retry'])
        source.enabled = False
        source.save()
        self.assertFalse(work_plan()['retry'])
        job.manual = True
        job.save()
        self.assertTrue(work_plan()['retry'])

    def test_idle_cycles_use_one_interruptible_wait_and_skip_empty_photo_work(self):
        from news.management.commands import run_news_worker as worker
        handlers = {}
        def register(sig, handler):
            handlers[sig] = handler
        waits = []
        def wait(delay):
            waits.append(delay)
            if len(waits) == 2:
                handlers[worker.signal.SIGTERM]()
        with patch.object(worker.signal, 'signal', side_effect=register), \
             patch.object(worker.Event, 'wait', side_effect=wait), \
             patch.object(worker, 'tick_images') as photos, \
             patch.object(worker, 'initialise_trends') as trends:
            worker.Command(stdout=io.StringIO()).handle(once=False)
        self.assertEqual(len(waits), 2)
        photos.assert_not_called()
        trends.assert_not_called()
        self.cfg.refresh_from_db()
        self.assertIsNotNone(self.cfg.worker_seen)


class IdleLimitTests(SimpleTestCase):
    @patch.dict(os.environ, {'NEWS_WORKER_IDLE_MAX_SECONDS': '30'})
    def test_operator_can_retain_thirty_second_latency(self):
        self.assertEqual(idle_delay(50), 30)

    @patch.dict(os.environ, {'NEWS_WORKER_IDLE_MAX_SECONDS': '900'})
    def test_maximum_stays_below_heartbeat_health_window(self):
        self.assertEqual(idle_delay(50), 120)
