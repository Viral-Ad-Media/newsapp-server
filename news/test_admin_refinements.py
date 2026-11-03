from datetime import timedelta
from unittest.mock import patch
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from . import test_publishing_limits as publishing_tests
from .automation import config, tick
from .models import NewsSource, CrawlRun
from .worker_diagnostics import safe_worker_error


class RefinedScheduleTests(TestCase):
    setUp = publishing_tests.PublishingLimitTests.setUp
    items = publishing_tests.PublishingLimitTests.items
    save_settings = publishing_tests.PublishingLimitTests.save_settings
    drain = publishing_tests.PublishingLimitTests.drain
    def test_all_sources_rules_include_new_sources_without_expanding_rows(self):
        response = self.save_settings(publishing_rules=[{'source': 0, 'category': self.sports.pk, 'limit': 1}])
        self.assertEqual(response.status_code, 200)
        extra = NewsSource.objects.create(name='Later publisher', url='https://example.org', feed_url='https://example.org/later', enabled=True)
        self.fetch.side_effect = lambda source, timeout: self.items(source, 2)
        from .automation import queue_run
        queue_run(); self.drain()
        from .models import NewsArticle
        self.assertEqual(NewsArticle.objects.filter(source=extra).count(), 1)
        self.assertEqual(NewsArticle.objects.count(), 3)

    def test_due_time_is_not_postponed_by_unrelated_saves(self):
        cfg = config(); cfg.enabled=True; cfg.next_run=timezone.now()-timedelta(seconds=1); cfg.save()
        due = cfg.next_run
        self.assertEqual(self.save_settings(publish_per_source=3).status_code, 200)
        cfg.refresh_from_db(); self.assertEqual(cfg.next_run, due)

    def test_automatic_intervals_work_without_manual_queue(self):
        for interval in (1, 7, 20, 1000):
            with self.subTest(interval=interval):
                self.assertEqual(self.save_settings(enabled=True, interval_minutes=interval, publish_per_source=1, publishing_rules=[]).status_code, 200)
                cfg=config(); cfg.next_run=timezone.now()-timedelta(seconds=1); cfg.save()
                run=tick(); self.assertEqual(run.trigger, 'scheduled')
                cfg.refresh_from_db()
                self.assertAlmostEqual((cfg.next_run-run.cycle_started_at).total_seconds(), interval*60, delta=1)
                self.drain()
                self.assertIsNone(tick())

    def test_old_worker_insert_has_database_default(self):
        old_apps = MigrationLoader(connection).project_state(('news','0021_developingstory_newsarticle_context_source_url_and_more')).apps
        old_run = old_apps.get_model('news','CrawlRun').objects.create(trigger='scheduled')
        self.assertEqual(CrawlRun.objects.get(pk=old_run.pk).request_state, {})

    @patch('news.admin_api.validate_feed_url')
    def test_enabling_old_source_recovers_catalogue_feed(self, validate):
        source=NewsSource.objects.create(name='Nairametrics',url='https://nairametrics.com',enabled=False)
        response=self.client.put(f'/api/admin/sources/{source.pk}/', {'enabled': True}, format='json')
        self.assertEqual(response.status_code,200)
        source.refresh_from_db(); self.assertTrue(source.enabled)
        self.assertEqual(source.feed_url, 'https://nairametrics.com/feed/')

    def test_disable_all_does_not_touch_settings(self):
        due=config().next_run
        response=self.client.post('/api/admin/sources/disable-all/',{},format='json')
        self.assertEqual(response.status_code,200)
        self.assertFalse(NewsSource.objects.filter(enabled=True).exists())
        self.assertEqual(config().next_run,due)

    @patch('news.admin_api.validate_feed_url', side_effect=ValueError('Unavailable DNS'))
    def test_disabling_an_existing_unreachable_feed_needs_no_network(self, validate):
        source = self.sources[0]
        response = self.client.put(f'/api/admin/sources/{source.pk}/', {'enabled': False}, format='json')
        self.assertEqual(response.status_code, 200)
        validate.assert_not_called()


class WorkerDiagnosticTests(SimpleTestCase):
    def test_errors_do_not_log_credentials_and_identify_schema(self):
        output=safe_worker_error(Exception('column secret does not exist postgres://private:password@host/db'))
        self.assertIn('schema',output)
        self.assertNotIn('password',output)
        self.assertNotIn('postgres://',output)


class LegacyFeedTests(SimpleTestCase):
    @patch('news.safe_fetch.fetch_xml', return_value='<rss version="2.0"><channel><item><title>Report</title><link>https://publisher.test/report</link><description>Summary</description></item></channel></rss>')
    @patch('news.safe_fetch.fetch_document', return_value='<link rel="alternate" type="application/rss+xml" href="/feed/">')
    def test_publisher_advertised_feed_is_discovered(self, page, feed):
        from .source_feeds import discover_feed
        self.assertEqual(discover_feed('https://publisher.test'), 'https://publisher.test/feed/')

    @patch('news.safe_fetch.fetch_xml')
    @patch('news.safe_fetch.fetch_document', return_value='<link rel="alternate" type="application/rss+xml" href="http://127.0.0.1/private">')
    def test_discovery_never_fetches_a_foreign_or_private_advertised_feed(self, page, feed):
        from .source_feeds import discover_feed
        self.assertIsNone(discover_feed('https://publisher.test'))
        feed.assert_not_called()
