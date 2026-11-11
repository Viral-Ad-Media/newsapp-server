from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import NewsArticle, NewsSource, NewsCategory, PublicResponseCache, PublicContentRevision, TrendSnapshot, PublishingRetry, ArticlePreparation
from .automation import config
from .worker_schedule import work_plan, idle_delay
from .trends import parse_trends, rank_items, tick_trends


class PublicCacheTests(TestCase):
    def setUp(self):
        self.client=APIClient()
        self.source=NewsSource.objects.create(name='Cache publisher',url='https://example.org')
        self.article=NewsArticle.all_objects.create(title='First',description='Summary',content='Original article',source=self.source,location='ng')
    def get(self, query='location=ng'):
        return self.client.get('/api/news/?'+query)
    def test_public_response_hit_and_new_publication_invalidates(self):
        first=self.get();self.assertIn('s-maxage=',first['Cache-Control'])
        self.assertEqual(PublicResponseCache.objects.count(),1)
        with patch('news.views.NewsList.get_queryset',side_effect=AssertionError('cache miss')):
            self.assertEqual(self.get().data['count'],1)
        NewsArticle.all_objects.create(title='New',description='Summary',content='Full report',source=self.source,location='ng')
        self.assertEqual(self.get().data['count'],2)
    def test_archives_and_category_membership_invalidate(self):
        self.get()
        NewsArticle.all_objects.filter(pk=self.article.pk).update(archived=True)
        self.assertEqual(self.get().data['count'],0)
        NewsArticle.all_objects.filter(pk=self.article.pk).update(archived=False)
        category=NewsCategory.objects.create(name='Sports',slug='sports')
        self.assertEqual(self.get(f'location=ng&category_id={category.pk}').data['count'],0)
        self.article.categories.add(category)
        self.assertEqual(self.get(f'location=ng&category_id={category.pk}').data['count'],1)
    def test_country_filter_host_and_query_variants_are_isolated(self):
        self.assertEqual(self.get().data['count'],1)
        self.assertEqual(self.get('location=us').data['count'],0)
        self.assertEqual(self.get('location=ng&search=absent').data['count'],0)
    def test_private_feeds_are_never_shared(self):
        self.get()
        reader=get_user_model().objects.create_user('cache-reader')
        self.client.force_authenticate(reader)
        for path in ['/api/news/?location=ng','/api/live/?location=ng','/api/feed/?location=ng','/api/saved/?location=ng','/api/edition/?location=ng']:
            response=self.client.get(path)
            self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(PublicResponseCache.objects.count(),1)
    def test_expiry_and_rollback_do_not_leave_new_generation(self):
        self.get();PublicResponseCache.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        from .public_cache import current_generation
        generation=current_generation()
        from django.db import transaction
        try:
            with transaction.atomic():
                self.article.title='Uncommitted';self.article.save()
                raise RuntimeError('rollback')
        except RuntimeError:pass
        self.assertEqual(current_generation(),generation)
        self.assertEqual(self.get().data['results'][0]['title'],'First')


class WorkerPlanTests(TestCase):
    def test_idle_worker_keeps_enabled_schedule_and_due_manual_requests(self):
        cfg=config();cfg.enabled=True;cfg.next_run=timezone.now()+timedelta(seconds=8);cfg.save()
        plan=work_plan();self.assertFalse(plan['publish']);self.assertLessEqual(plan['wait'],8)
        self.assertEqual(idle_delay(10,8),8)
        cfg.enabled=False;cfg.save()
        from .automation import queue_run
        queue_run(dry_run=True)
        self.assertTrue(work_plan()['publish'])
    def test_paused_scheduled_retries_do_not_wake_but_manual_retries_do(self):
        config()
        source=NewsSource.objects.create(name='Retry publisher',url='https://example.org',enabled=True)
        job=PublishingRetry.objects.create(source=source,key='retry',next_attempt=timezone.now()-timedelta(seconds=1))
        self.assertFalse(work_plan()['retry'])
        job.manual=True;job.save();self.assertTrue(work_plan()['retry'])
    def test_idle_probe_does_not_touch_heartbeat_or_execution_fence(self):
        cfg=config();before=cfg.worker_seen
        work_plan();cfg.refresh_from_db();self.assertEqual(cfg.worker_seen,before)
        self.assertEqual([idle_delay(i) for i in range(5)],[5,10,20,40,60])


class TrendsTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.source=NewsSource.objects.create(name='Publisher',url='https://example.org',location='ng')
        self.topic={'query':'Super Eagles','country':'NG','traffic':'20K+','volume':20000,'started_at':timezone.now().isoformat()}
    def item(self,title):
        return {'title':title,'description':'Verified source reporting','published_at':timezone.now(),'content':' '.join(['evidence']*450)}
    def test_recent_xml_parsed_and_old_or_entity_xml_rejected(self):
        date=timezone.now().strftime('%a, %d %b %Y %H:%M:%S +0000')
        xml=f'<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item><title>Super Eagles</title><pubDate>{date}</pubDate><ht:approx_traffic>20K+</ht:approx_traffic></item></channel></rss>'
        rows=parse_trends(xml,'NG');self.assertEqual(rows[0]['volume'],20000)
        self.assertEqual(parse_trends(xml.replace(date,'Mon, 01 Jan 2001 00:00:00 +0000'),'NG'),[])
        with self.assertRaises(ValueError):parse_trends('<!DOCTYPE rss><rss/>','NG')
    @patch('news.trends.available_topics')
    def test_trending_report_wins_and_does_not_mutate_country_or_categories(self, available):
        available.return_value=[self.topic]
        old=self.item('Routine football roundup');trend=self.item('Super Eagles team announced')
        trend.update(categories=['Sports'],location='ng')
        items=rank_items([old,trend],self.source)
        self.assertIs(items[0],trend);self.assertEqual(trend['trend']['query'],'Super Eagles')
        self.assertEqual(trend['categories'],['Sports']);self.assertEqual(trend['location'],'ng')
    @patch('news.trends.available_topics')
    def test_whole_token_matches_and_single_foreign_trends_do_not_boost(self, available):
        available.return_value=[{**self.topic,'query':'art'},{**self.topic,'country':'US'}]
        item=self.item('Cartography report');self.assertNotIn('trend',rank_items([item],self.source)[0])
        item=self.item('Super Eagles news');self.assertNotIn('trend',rank_items([item],self.source)[0])
        available.return_value=[self.topic];rank_items([item],self.source,enabled=False);self.assertNotIn('trend',item)
    @patch('news.trends.fetch_document')
    def test_failure_backoff_preserves_previous_snapshot(self, fetch):
        fetch.side_effect=RuntimeError('provider unavailable')
        old=timezone.now()-timedelta(minutes=31)
        row=TrendSnapshot.objects.create(country='NG',topics=[self.topic],fetched_at=old)
        tick_trends();row.refresh_from_db()
        self.assertEqual(row.topics,[self.topic]);self.assertEqual(row.fetched_at,old)
        self.assertGreater(row.next_attempt,timezone.now()+timedelta(minutes=25))
        self.assertIsNone(tick_trends());fetch.assert_called_once()
