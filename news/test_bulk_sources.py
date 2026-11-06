from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from .automation import config
from .models import NewsSource, AdminEvent, CrawlRun


class BulkSourceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user('bulk-editor', is_staff=True)
        self.reader = get_user_model().objects.create_user('bulk-reader')
        self.client.force_authenticate(self.staff)

    def test_all_pages_enable_without_changing_rules_or_starting_jobs(self):
        cfg = config()
        cfg.publishing_rules = [{'source': 123, 'category': None, 'limit': 1}]
        cfg.save()
        due = cfg.next_run
        NewsSource.objects.bulk_create([NewsSource(name=f'Feed {i}', url='https://example.org', feed_url=f'https://example.org/feed-{i}', enabled=i == 0) for i in range(25)])
        for feed in ['', None, '   ']:
            NewsSource.objects.create(name='Attribution only', url='https://example.org', feed_url=feed)
        response = self.client.post('/api/admin/sources/enable-all/?search=nonexistent&page=2', {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({key: response.data[key] for key in ('enabled','already_enabled','without_feed')}, {'enabled': 24, 'already_enabled': 1, 'without_feed': 3})
        self.assertEqual(len(response.data['unconfigured']), 3)
        self.assertEqual(NewsSource.objects.filter(enabled=True).count(), 25)
        cfg.refresh_from_db()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.next_run, due)
        self.assertEqual(cfg.publishing_rules, [{'source': 123, 'category': None, 'limit': 1}])
        self.assertEqual(CrawlRun.objects.count(), 0)
        audit = AdminEvent.objects.get(message='Enabled all configured feed sources')
        self.assertEqual(audit.actor, self.staff)
        self.assertEqual(len(audit.details['source_ids']), 24)
        self.assertIn('no-store', response['Cache-Control'])

    def test_repeated_calls_and_empty_collection_are_safe(self):
        self.assertEqual(self.client.post('/api/admin/sources/enable-all/').data['enabled'], 0)
        source = NewsSource.objects.create(name='Feed', url='https://example.org', feed_url='https://example.org/feed')
        self.assertEqual(self.client.post('/api/admin/sources/enable-all/').data['enabled'], 1)
        self.assertEqual(self.client.post('/api/admin/sources/enable-all/').data['enabled'], 0)
        source.refresh_from_db(); self.assertTrue(source.enabled)

    def test_guests_and_readers_cannot_enable_sources(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/admin/sources/enable-all/').status_code, 401)
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.post('/api/admin/sources/enable-all/').status_code, 403)
