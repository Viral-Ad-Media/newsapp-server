from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from django.db import DatabaseError
from . import test_publishing_limits as helpers
from .models import PublishingRetry, WritingServiceHealth, NewsArticle, CrawlRun
from .automation import config,queue_run,tick
from .publishing_retries import tick_retries,record_failure,requeue_failed,writing_health
from .publishing_failures import failure_info
from .article_writing import ArticlePreparationError
from .safe_fetch import DocumentHTTPError
from .article_test_data import FULL_ARTICLE


class DurableRetryTests(TestCase):
    setUp=helpers.PublishingLimitTests.setUp
    items=helpers.PublishingLimitTests.items
    save_settings=helpers.PublishingLimitTests.save_settings
    drain=helpers.PublishingLimitTests.drain

    def enable(self):
        cfg=config();cfg.enabled=True;cfg.next_run=timezone.now()+timedelta(hours=1);cfg.save()
        return cfg

    def stage(self,exc=None):
        cfg=self.enable()
        source=self.sources[0]
        run=CrawlRun.objects.create(trigger='scheduled',status='failed',cycle_started_at=timezone.now())
        item=self.items(source,1)[0]
        return record_failure(source,item,run,exc or TimeoutError('do not expose this URL or secret'))

    def test_transient_failure_is_durable_and_retried_only_when_due(self):
        job=self.stage();self.assertEqual(job.status,'retry_wait')
        self.assertEqual(job.reason_code,'network_timeout')
        self.assertEqual(job.attempts,1)
        self.assertIsNone(tick_retries());self.writer.assert_not_called()
        with patch('news.publishing_retries.timezone.now',return_value=timezone.now()+timedelta(minutes=2)):
            tick_retries()
        job.refresh_from_db();self.assertEqual(job.status,'published')
        self.assertEqual(NewsArticle.objects.count(),1)
        self.assertIsNone(tick_retries())
        self.assertNotIn('secret',job.reason)

    def test_backoff_increases_then_stops_and_manual_retry_is_idempotent(self):
        job=self.stage(); delays=[]
        for i in range(3):
            before=timezone.now()
            job=record_failure(job.source,job.item,job.run,TimeoutError())
            delays.append((job.next_attempt-before).total_seconds())
        self.assertTrue(delays[0]<delays[1]<delays[2])
        job=record_failure(job.source,job.item,job.run,TimeoutError())
        self.assertEqual(job.status,'exhausted');self.assertIsNone(job.next_attempt)
        self.assertEqual(requeue_failed(),1)
        self.assertEqual(requeue_failed(),0)
        job.refresh_from_db();self.assertEqual(job.status,'queued');self.assertEqual(job.attempts,0)

    def test_retry_pause_and_explicit_manual_authorization(self):
        job=self.stage();cfg=config();cfg.enabled=False;cfg.save()
        PublishingRetry.objects.filter(pk=job.pk).update(next_attempt=timezone.now())
        self.assertIsNone(tick_retries())
        self.assertEqual(self.client.post('/api/admin/retries/retry/',{},format='json').status_code,202)
        tick_retries();job.refresh_from_db();self.assertEqual(job.status,'published')

    def test_blocked_publisher_needs_review_instead_of_automatic_retry(self):
        job=self.stage(DocumentHTTPError(403))
        self.assertEqual(job.status,'blocked');self.assertEqual(job.reason_code,'publisher_blocked')
        self.assertIsNone(job.next_attempt);self.assertIsNone(tick_retries())

    def test_original_article_survives_a_later_interrupted_publication(self):
        self.save_settings(publishing_rules=[{'source':self.sources[0].pk,'category':None,'limit':2}])
        queue_run();first=tick();self.assertEqual(first.counts['published'],1)
        self.assertEqual(NewsArticle.objects.count(),1)
        self.writer.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):tick()
        self.assertEqual(NewsArticle.objects.count(),1)
        self.writer.side_effect=None;self.drain()
        self.assertEqual(NewsArticle.objects.count(),4)

    def test_rich_feed_text_is_preferred_without_fetching_source_page(self):
        self.save_settings(publish_per_source=1)
        source=self.sources[0]
        entries=self.items(source,2);entries[1]['source_text']=FULL_ARTICLE
        self.fetch.return_value=entries;self.fetch.side_effect=None
        with patch('news.article_writing.source_evidence') as evidence:
            queue_run(source=source);tick()
        evidence.assert_not_called()
        self.assertEqual(NewsArticle.objects.get().title,entries[1]['title'])
        source.refresh_from_db();self.assertEqual(source.feed_quality['full_text_entries'],1)

    def test_writing_outage_has_safe_reason_and_manual_retry_restores_health(self):
        error=ArticlePreparationError('internal message',details={'code':'writing_balance'})
        job=self.stage(error)
        self.assertEqual(writing_health()['code'],'writing_balance')
        self.assertIsNone(tick_retries())
        self.assertEqual(self.client.post('/api/admin/retries/retry/',{},format='json').data['queued'],1)
        tick_retries()
        self.assertEqual(writing_health()['code'],'ok')
        self.assertIsNotNone(writing_health()['last_success'])

    def test_retry_respects_category_quotas_and_resolves_duplicates(self):
        job=self.stage();self.save_settings(publishing_rules=[{'source':job.source_id,'category':self.business.pk,'limit':1}])
        PublishingRetry.objects.filter(pk=job.pk).update(next_attempt=timezone.now())
        tick_retries();job.refresh_from_db()
        self.assertEqual(job.status,'skipped');self.assertEqual(job.reason_code,'category_mismatch')
        self.assertEqual(NewsArticle.objects.count(),0)

    def test_feed_failure_retries_with_backoff_without_moving_saved_schedule(self):
        cfg=self.enable();due=cfg.next_run
        run=CrawlRun.objects.create(trigger='scheduled',status='failed')
        job=record_failure(self.sources[0],{},run,TimeoutError(),kind='feed')
        self.assertIsNone(tick_retries())
        PublishingRetry.objects.filter(pk=job.pk).update(next_attempt=timezone.now())
        tick_retries();self.assertTrue(CrawlRun.objects.filter(trigger='feed_retry',status='queued').exists())
        tick();job.refresh_from_db();self.assertEqual(job.status,'resolved')
        self.assertEqual(config().next_run,due)

    def test_pausing_after_a_feed_retry_is_queued_stops_automatic_publication(self):
        self.enable()
        run=CrawlRun.objects.create(trigger='scheduled',status='failed')
        job=record_failure(self.sources[0],{},run,TimeoutError(),kind='feed')
        PublishingRetry.objects.filter(pk=job.pk).update(next_attempt=timezone.now())
        tick_retries()
        cfg=config();cfg.enabled=False;cfg.save()
        self.assertEqual(tick().status,'cancelled')
        self.fetch.assert_not_called()
        job.refresh_from_db();self.assertEqual(job.status,'retry_wait')
        self.assertEqual(NewsArticle.objects.count(),0)

    def test_continuations_reuse_the_feed_without_repeating_publisher_requests(self):
        self.save_settings(publishing_rules=[{'source':self.sources[0].pk,'category':None,'limit':2}])
        queue_run();self.drain()
        self.assertEqual(NewsArticle.objects.count(),4)
        self.assertEqual(self.fetch.call_count,1)

    def test_retry_api_does_not_expose_evidence_and_is_staff_only(self):
        job=self.stage();data=self.client.get('/api/admin/retries/').data
        self.assertEqual(data['count'],1)
        self.assertNotIn('item',data['results'][0])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/admin/retries/').status_code,401)
        self.assertEqual(self.client.post('/api/admin/retries/retry/').status_code,401)
        from django.contrib.auth import get_user_model
        reader=get_user_model().objects.create_user('retry-reader',password='test-reader-only')
        self.client.force_authenticate(reader)
        self.assertEqual(self.client.get('/api/admin/retries/').status_code,403)
        self.assertEqual(self.client.post('/api/admin/retries/retry/').status_code,403)


class FailureReasonTests(SimpleTestCase):
    def test_provider_errors_distinguish_key_balance_rate_limit_timeout(self):
        for status,body,code in [(401,{},'writing_auth'),(429,{'code':'insufficient_quota'},'writing_balance'),(429,{},'writing_rate_limit'),(503,{},'writing_unavailable')]:
            exc=Exception('private provider response');exc.status_code=status;exc.body=body
            info=failure_info(exc,'writing');self.assertEqual(info['code'],code)
            self.assertNotIn('private',info['message'])
        self.assertEqual(failure_info(TimeoutError(),'writing')['code'],'writing_timeout')
        self.assertEqual(failure_info(DocumentHTTPError(429))['code'],'publisher_rate_limit')

    def test_publisher_retry_after_is_respected(self):
        from .publishing_failures import retry_time
        before=timezone.now();info=failure_info(DocumentHTTPError(429,'600'))
        self.assertGreaterEqual((retry_time(1,info)-before).total_seconds(),600)


class RequestRetryIsolationTests(TestCase):
    setUp=helpers.PublishingLimitTests.setUp
    items=helpers.PublishingLimitTests.items
    save_settings=helpers.PublishingLimitTests.save_settings
    drain=helpers.PublishingLimitTests.drain

    def test_request_retries_wait_for_continuations_then_preserve_actual_totals(self):
        from .on_demand import run_progress
        source=self.sources[0]
        rules=[{'source':source.pk,'category':self.sports.pk,'limit':3}]
        root=queue_run(publishing_rules=rules)
        job=record_failure(source,self.items(source,1)[0],root,TimeoutError())
        PublishingRetry.objects.filter(pk=job.pk).update(next_attempt=timezone.now())
        self.assertIsNone(tick_retries())
        self.drain()  # Other eligible articles fulfill the request without duplicates.
        self.assertEqual(run_progress(root)['published'],3)
        tick_retries()
        self.assertEqual(NewsArticle.objects.count(),3)
        job.refresh_from_db();self.assertEqual(job.reason_code,'quota_reached')

    def test_request_retry_counts_a_recovered_story_only_once(self):
        from .on_demand import run_progress
        source=self.sources[0]
        entries=self.items(source,2);self.fetch.side_effect=lambda source,timeout:entries
        root=queue_run(publishing_rules=[{'source':source.pk,'category':self.sports.pk,'limit':1}])
        with patch('news.article_writing.source_evidence',side_effect=TimeoutError()):
            self.drain()
        self.assertEqual(NewsArticle.objects.count(),0)
        self.assertEqual(run_progress(root)['status'],'retry_wait')
        PublishingRetry.objects.update(next_attempt=timezone.now())
        tick_retries()
        self.assertEqual(run_progress(root)['published'],1)
        self.assertEqual(run_progress(root)['sources'][0]['published'],1)
        self.assertEqual(NewsArticle.objects.count(),1)

    def test_cancelled_request_cannot_be_revived_by_retry_button(self):
        source=self.sources[0]
        root=CrawlRun.objects.create(trigger='on_demand',status='cancelled',request_key=__import__('uuid').uuid4(),publishing_rules=[{'source':source.pk,'category':self.sports.pk,'limit':1}])
        job=record_failure(source,self.items(source,1)[0],root,TimeoutError())
        requeue_failed();tick_retries()
        job.refresh_from_db();self.assertEqual(job.status,'skipped')
        self.assertEqual(NewsArticle.objects.count(),0)


class RichFeedParsingTests(SimpleTestCase):
    def test_atom_xhtml_full_text_is_preserved_as_evidence(self):
        from .utils.rss_ingestor import parse_rss_or_atom_xml
        body=' '.join(['Verified source evidence and factual reporting.']*100)
        xml=f'<feed xmlns="http://www.w3.org/2005/Atom"><title>Publisher</title><entry><title>Report</title><link href="https://publisher.test/report"/><published>2026-11-03T12:00:00Z</published><summary>Short summary.</summary><content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>{body}</p></div></content></entry></feed>'
        items=parse_rss_or_atom_xml(xml,strict=True)
        self.assertEqual(len(items),1)
        self.assertGreater(len(items[0]['source_text'].split()),400)
        self.assertEqual(items[0]['description'],'Short summary.')
