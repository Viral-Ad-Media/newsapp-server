from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import NewsCategory, NewsSource, NewsArticle, CrawlRun, SourcePublishingWindow
from .automation import config, tick, cancel_run
from .article_test_data import FULL_ARTICLE, PREPARED
from .on_demand import run_progress


class OnDemandPublishingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user('one-off-editor', is_staff=True)
        self.reader = get_user_model().objects.create_user('one-off-reader')
        self.client.force_authenticate(self.staff)
        self.sport = NewsCategory.objects.create(name='Sport')
        self.business = NewsCategory.objects.create(name='Business')
        self.sources = [NewsSource.objects.create(name=f'Publisher {i}', url=f'https://publisher{i}.test', feed_url=f'https://publisher{i}.test/feed', location='ng', enabled=False, default_category=self.business) for i in range(2)]
        for target, value in [('news.article_writing.prepare_article', PREPARED), ('news.article_writing.source_evidence', FULL_ARTICLE)]:
            patcher = patch(target, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch('news.utils.ai_summarizer.client', object()); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch('news.automation.source_items', side_effect=lambda source, timeout: self.items(source)); patcher.start(); self.addCleanup(patcher.stop)

    def items(self, source):
        return [{'title': f'{source.pk} item {i}', 'description': 'An attributed source summary.', 'article_url': f'{source.url}/story/{i}', 'published_at': timezone.now()-timedelta(minutes=5), 'categories': ['Sport' if i % 2 == 0 else 'Business']} for i in range(10)]

    def plan(self, source=0, limit=3):
        return {'source': self.sources[source].pk, 'category': self.sport.pk, 'limit': limit}

    def queue(self, rules=None, dry_run=False):
        response = self.client.post('/api/admin/publishing/', {'rules': rules or [self.plan()], 'dry_run': dry_run}, format='json')
        self.assertEqual(response.status_code, 202, response.data)
        return response.data['root_id']

    def drain(self):
        for _ in range(30):
            if not CrawlRun.objects.filter(status='queued').exists():
                return
            tick()
        self.fail('One-off queue did not drain')

    def test_paused_source_publishes_exact_quantity_without_changing_schedule(self):
        cfg = config(); cfg.interval_minutes = 1; cfg.publish_per_source = 1
        cfg.publishing_rules = [self.plan(1, 1)]; cfg.save()
        original_due = cfg.next_run
        SourcePublishingWindow.objects.create(source=self.sources[0], counts={'*': 100})
        root = self.queue()
        self.drain()
        self.assertEqual(NewsArticle.objects.count(), 3)
        self.assertEqual(NewsArticle.objects.filter(categories=self.sport).count(), 3)
        self.assertEqual(NewsArticle.objects.filter(categories=self.business).count(), 0)
        cfg.refresh_from_db(); self.assertEqual(cfg.publishing_rules, [self.plan(1, 1)])
        self.assertEqual(cfg.next_run, original_due); self.assertFalse(cfg.enabled)
        self.assertFalse(NewsSource.objects.get(pk=self.sources[0].pk).enabled)
        self.assertEqual(SourcePublishingWindow.objects.get(source=self.sources[0]).counts, {'*': 100})
        result = self.client.get(f'/api/admin/publishing/{root}/').data
        self.assertEqual((result['status'], result['published'], result['remaining']), ('success', 3, 0))
        self.assertEqual(result['sources'][0]['published'], 3)

    def test_multiple_sources_and_category_limits_survive_continuations(self):
        root = self.queue([self.plan(0, 4), self.plan(1, 3)])
        self.drain()
        self.assertEqual(NewsArticle.objects.count(), 7)
        result = self.client.get(f'/api/admin/publishing/{root}/').data
        self.assertGreater(result['batches'], 1)
        self.assertEqual(result['published'], 7)
        self.assertEqual({s['published'] for s in result['sources']}, {3, 4})
        self.assertEqual(CrawlRun.objects.values('request_key').distinct().count(), 1)

    def test_multicategory_article_fills_only_one_requested_slot(self):
        def overlapping(source, timeout):
            return [{**item, 'categories': ['Sport', 'Business']} for item in self.items(source)]
        rules = [self.plan(limit=2), {**self.plan(limit=2), 'category': self.business.pk}]
        with patch('news.automation.source_items', side_effect=overlapping):
            root = self.queue(rules); self.drain()
        self.assertEqual(NewsArticle.objects.count(), 4)
        self.assertEqual(NewsArticle.objects.filter(categories=self.sport).count(), 4)
        self.assertEqual(NewsArticle.objects.filter(categories=self.business).count(), 4)
        result = self.client.get(f'/api/admin/publishing/{root}/').data
        self.assertEqual((result['published'], result['remaining']), (4, 0))

    def test_shortfall_is_reported_instead_of_claiming_success(self):
        root = self.queue([self.plan(limit=10)])
        self.drain()
        result = self.client.get(f'/api/admin/publishing/{root}/').data
        self.assertEqual(result['requested'], 10)
        self.assertEqual(result['published'], 5)
        self.assertEqual(result['remaining'], 5)
        self.assertEqual(result['status'], 'partial')

    def test_duplicate_articles_do_not_count_towards_new_request(self):
        self.queue([self.plan(limit=2)]); self.drain()
        root = self.queue([self.plan(limit=2)]); self.drain()
        self.assertEqual(NewsArticle.objects.count(), 4)
        result = self.client.get(f'/api/admin/publishing/{root}/').data
        self.assertEqual(result['published'], 2)
        self.assertGreater(result['counts']['duplicates'], 0)

    def test_missing_writing_service_fails_visibly_but_preview_is_allowed(self):
        with patch('news.utils.ai_summarizer.client', None):
            response = self.client.post('/api/admin/publishing/', {'rules': [self.plan()]}, format='json')
            self.assertEqual(response.status_code, 503)
            self.assertIn('OPENAI_API_KEY', response.data['detail'])
            self.assertEqual(CrawlRun.objects.count(), 0)
            root = self.queue(dry_run=True)
            self.drain()
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.assertEqual(SourcePublishingWindow.objects.count(), 0)
        self.assertEqual(self.client.get(f'/api/admin/publishing/{root}/').data['counts']['eligible'], 3)

    def test_preview_continuations_never_turn_into_publishing_runs(self):
        rules = []
        for i in range(21):
            source = NewsSource.objects.create(name=str(i), url=f'https://preview{i}.test', feed_url=f'https://preview{i}.test/feed')
            rules.append({'source': source.pk, 'category': self.sport.pk, 'limit': 1})
        root = self.queue(rules, dry_run=True); self.drain()
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.assertFalse(CrawlRun.objects.filter(dry_run=False).exists())
        self.assertEqual(self.client.get(f'/api/admin/publishing/{root}/').data['counts']['eligible'], 21)

    def test_invalid_quantities_sources_and_duplicate_rules_are_rejected(self):
        for rules in [[self.plan(limit=0)], [self.plan(limit=101)], [self.plan(), self.plan()], [{**self.plan(), 'source': 999999}], [self.plan(limit=60), {**self.plan(limit=60), 'category': self.business.pk}]]:
            self.assertEqual(self.client.post('/api/admin/publishing/', {'rules': rules}, format='json').status_code, 400)
        self.sources[0].feed_url = ''; self.sources[0].save()
        self.assertEqual(self.client.post('/api/admin/publishing/', {'rules': [self.plan()]}, format='json').status_code, 400)

    def test_reader_and_guest_cannot_start_or_inspect_requests(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/admin/publishing/').status_code, 401)
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.post('/api/admin/publishing/', {'rules': [self.plan()]}, format='json').status_code, 403)

    def test_conflict_exposes_existing_job_and_cancel_stops_continuation(self):
        root = self.queue(); tick()
        active = CrawlRun.objects.get(status='queued')
        response = self.client.post('/api/admin/publishing/', {'rules': [self.plan()]}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['active_run_id'], active.pk)
        cancel_run(active.pk, self.staff)
        self.assertEqual(self.client.get(f'/api/admin/publishing/{root}/').data['status'], 'cancelled')
        self.assertEqual(NewsArticle.objects.count(), 1)
