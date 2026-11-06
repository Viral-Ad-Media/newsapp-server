"""All categories is a per-category scheduled quota, not a shared source quota."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from . import test_publishing_limits as helpers
from .automation import queue_run, config, tick
from .models import NewsCategory, NewsArticle, SourcePublishingWindow, CrawlRun
from .publishing_limits import source_limits, has_capacity


class AllCategoryPublishingTests(TestCase):
    setUp = helpers.PublishingLimitTests.setUp
    items = helpers.PublishingLimitTests.items
    save_settings = helpers.PublishingLimitTests.save_settings
    drain = helpers.PublishingLimitTests.drain

    def fixture_feed(self, labels):
        def feed(source, timeout):
            return [dict(title=f'{source.pk} report {i}', description='Verified summary', article_url=f'https://example.org/{source.pk}/all-category/{i}', published_at=timezone.now()-timedelta(minutes=5), categories=[label]) for i,label in enumerate(labels)]
        self.fetch.side_effect = feed

    def test_all_sources_publish_one_of_each_matching_category_without_extra_rules(self):
        world=NewsCategory.objects.create(name='World')
        entertainment=NewsCategory.objects.create(name='Entertainment')
        self.fixture_feed(['Sports','Sports','World','World','Entertainment','Entertainment'])
        response=self.save_settings(publishing_rules=[{'source':0,'category':None,'limit':1}])
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['max_per_source'],100)
        queue_run(); first=tick()
        self.assertEqual(first.counts['published'],1)
        self.assertTrue(CrawlRun.objects.filter(status='queued').exists())
        self.drain()  # Simulates later worker batches loading durable quotas.
        for source in self.sources:
            self.assertEqual(NewsArticle.objects.filter(source=source).count(),3)
            for category in (self.sports,world,entertainment):
                self.assertEqual(NewsArticle.objects.filter(source=source,categories=category).count(),1)
        queue_run(); self.drain()
        self.assertEqual(NewsArticle.objects.count(),6)

    def test_selected_source_and_new_category_are_resolved_dynamically(self):
        source=self.sources[0]
        self.save_settings(publishing_rules=[{'source':source.pk,'category':None,'limit':1}])
        category=NewsCategory.objects.create(name='Entertainment')
        self.fixture_feed(['Sports','Business','Entertainment'])
        queue_run(); self.drain()
        self.assertEqual(NewsArticle.objects.filter(source=source).count(),3)
        self.assertTrue(NewsArticle.objects.filter(source=source,categories=category).exists())
        self.assertFalse(NewsArticle.objects.filter(source=self.sources[1]).exists())

    def test_category_quotas_renew_at_the_configured_interval(self):
        self.fixture_feed(['Sports','Business','Sports','Business'])
        self.save_settings(interval_minutes=7,publishing_rules=[{'source':self.sources[0].pk,'category':None,'limit':1}])
        queue_run(); self.drain()
        self.assertEqual(NewsArticle.objects.count(),2)
        with patch('news.automation.timezone.now',return_value=timezone.now()+timedelta(minutes=8)):
            queue_run(); self.drain()
        self.assertEqual(NewsArticle.objects.count(),4)

    def test_multiple_category_report_does_not_overfill_a_category(self):
        self.fixture_feed(['Sports','Business'])
        self.fetch.side_effect=lambda source,timeout:[dict(title=f'{source.pk} report {i}',description='Summary',article_url=f'https://example.org/{source.pk}/multi/{i}',published_at=timezone.now()-timedelta(minutes=5),categories=labels) for i,labels in enumerate([['Sports','Business'],['Sports'],['Business']])]
        self.save_settings(publishing_rules=[{'source':self.sources[0].pk,'category':None,'limit':1}])
        queue_run(); self.drain()
        self.assertEqual(NewsArticle.objects.count(),2)
        self.assertEqual(NewsArticle.objects.filter(categories=self.sports).count(),1)
        self.assertEqual(NewsArticle.objects.filter(categories=self.business).count(),1)
        window=SourcePublishingWindow.objects.get(source=self.sources[0])
        self.assertEqual(window.counts[str(self.sports.pk)],1)
        self.assertEqual(window.counts[str(self.business.pk)],1)

    def test_uncategorized_items_are_not_relabelled_to_fill_category_slots(self):
        source=self.sources[0];source.default_category=None;source.save()
        self.fixture_feed(['Unsupported subject'])
        self.save_settings(publishing_rules=[{'source':source.pk,'category':None,'limit':1}])
        queue_run(); self.drain()
        self.assertEqual(NewsArticle.objects.count(),0)
        self.writer.assert_not_called()

    def test_each_category_can_reach_its_limit_without_an_unrelated_shared_100_cap(self):
        self.save_settings(publishing_rules=[{'source':self.sources[0].pk,'category':None,'limit':100}])
        limits=source_limits(config(),self.sources[0].pk)
        from types import SimpleNamespace
        window=SimpleNamespace(counts={'*':100,str(self.sports.pk):100})
        self.assertFalse(has_capacity(window,limits,[str(self.sports.pk)]))
        self.assertTrue(has_capacity(window,limits,[str(self.business.pk)]))

    def test_empty_rule_list_retains_the_existing_default_source_total(self):
        self.fixture_feed(['Sports','Business','Sports'])
        self.save_settings(publish_per_source=1,publishing_rules=[])
        queue_run(); self.drain()
        self.assertEqual(NewsArticle.objects.count(),2)
