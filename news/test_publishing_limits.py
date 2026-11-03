from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import (
    NewsSource,
    NewsCategory,
    NewsArticle,
    SourcePublishingWindow,
    CrawlRun,
)
from .automation import config, queue_run, tick
from .article_test_data import FULL_ARTICLE, PREPARED


class PublishingLimitTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            get_user_model().objects.create_user(
                "policy-editor", is_staff=True, is_superuser=True
            )
        )
        self.sports = NewsCategory.objects.create(name="Sports")
        self.business = NewsCategory.objects.create(name="Business")
        self.sources = [
            NewsSource.objects.create(
                name=name,
                url="https://example.org",
                feed_url=f"https://example.org/{index}/rss",
                enabled=True,
                default_category=self.business,
            )
            for index, name in enumerate(["Nairametrics", "Second publisher"])
        ]
        for target, value in [
            ("news.article_writing.prepare_article", PREPARED),
            ("news.article_writing.source_evidence", FULL_ARTICLE),
            ("news.utils.ai_summarizer.client", object()),
        ]:
            mocked = (
                patch(target, return_value=value)
                if target != "news.utils.ai_summarizer.client"
                else patch(target, value)
            )
            handle = mocked.start()
            if target.endswith("prepare_article"):
                self.writer = handle
            self.addCleanup(mocked.stop)
        self.fetch = patch("news.automation.source_items").start()
        self.addCleanup(patch.stopall)
        self.fetch.side_effect = lambda source, timeout: self.items(source, 8)

    def items(self, source, count):
        return [
            dict(
                title=f"{source.pk} report {index}",
                description="A verified source summary.",
                article_url=f"https://example.org/{source.pk}/story/{index}",
                published_at=timezone.now() - timedelta(minutes=5),
                categories=["Sport" if index % 2 == 0 else "Business"],
            )
            for index in range(count)
        ]

    def save_settings(self, **payload):
        return self.client.put(
            "/api/admin/settings/",
            {"revision": config().revision, **payload},
            format="json",
        )

    def drain(self, maximum=120):
        runs = []
        while CrawlRun.objects.filter(status="queued").exists():
            runs.append(tick())
            if len(runs) >= maximum:
                self.fail(
                    "Continuation queue did not drain within its bounded workload."
                )
        return runs

    def test_intervals_and_publishing_limits_accept_edges_and_reject_invalid_values(
        self,
    ):
        for interval in [1, 1000]:
            result = self.save_settings(interval_minutes=interval)
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data["interval_minutes"], interval)
        for interval in [0, 1001, -1, 1.5]:
            self.assertEqual(
                self.save_settings(interval_minutes=interval).status_code, 400
            )
        for limit in [1, 100]:
            response = self.save_settings(publish_per_source=limit)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertGreaterEqual(response.data["max_per_source"], limit)
        for limit in [0, 101, -5]:
            self.assertEqual(
                self.save_settings(publish_per_source=limit).status_code, 400
            )

    def test_rules_validate_references_duplicates_and_combined_source_limit(self):
        valid = {"source": self.sources[0].pk, "category": self.sports.pk, "limit": 1}
        bad_lists = [
            [{**valid, "source": 999999}],
            [{**valid, "category": 999999}],
            [{**valid, "limit": 0}],
            [{**valid, "limit": 101}],
            [valid, valid],
            [
                {**valid, "limit": 60},
                {**valid, "category": self.business.pk, "limit": 60},
            ],
            [valid, {**valid, "category": None}],
        ]
        for rules in bad_lists:
            self.assertEqual(
                self.save_settings(publishing_rules=rules).status_code, 400
            )
        rules = [
            valid,
            {"source": self.sources[1].pk, "category": self.business.pk, "limit": 100},
        ]
        result = self.save_settings(publishing_rules=rules)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["publishing_rules"], rules)
        self.assertEqual(result.data["max_per_source"], 100)

    def test_one_sports_story_from_selected_source_per_hour_and_other_source_quota(
        self,
    ):
        response = self.save_settings(
            interval_minutes=60,
            publishing_rules=[
                {"source": self.sources[0].pk, "category": self.sports.pk, "limit": 1},
                {
                    "source": self.sources[1].pk,
                    "category": self.business.pk,
                    "limit": 3,
                },
            ],
        )
        self.assertEqual(response.status_code, 200)
        queue_run()
        runs = self.drain()
        self.assertGreater(len(runs), 1)
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[0]).count(), 1)
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[1]).count(), 3)
        self.assertFalse(
            NewsArticle.objects.filter(
                source=self.sources[0], categories=self.business
            ).exists()
        )
        self.assertEqual(self.writer.call_count, 4)
        queue_run()
        self.drain()
        self.assertEqual(self.writer.call_count, 4)
        self.assertEqual(NewsArticle.objects.count(), 4)

    def test_one_hundred_target_drains_across_small_batches_and_multiple_sources(self):
        self.fetch.side_effect = lambda source, timeout: self.items(source, 100)
        response = self.save_settings(
            publishing_rules=[
                {"source": self.sources[0].pk, "category": None, "limit": 100},
                {"source": self.sources[1].pk, "category": None, "limit": 2},
            ]
        )
        self.assertEqual(response.status_code, 200)
        queue_run()
        first = tick()
        self.assertLessEqual(first.counts["published"], 2)
        self.assertTrue(CrawlRun.objects.filter(status="queued", parent=first).exists())
        self.drain()
        self.assertEqual(
            NewsArticle.objects.filter(source=self.sources[0]).count(), 100
        )
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[1]).count(), 4)
        self.assertEqual(self.writer.call_count, 104)

        self.assertTrue(
            all(run.counts.get("published", 0) <= 2 for run in CrawlRun.objects.all())
        )
        self.save_settings(
            publishing_rules=[
                {
                    "source": self.sources[0].pk,
                    "category": self.sports.pk,
                    "limit": 100,
                },
                {"source": self.sources[1].pk, "category": None, "limit": 2},
            ]
        )
        queue_run()
        self.drain()
        self.assertEqual(self.writer.call_count, 104)

    def test_rules_select_only_listed_sources_and_clearing_them_restores_default(self):
        self.save_settings(
            publish_per_source=1,
            publishing_rules=[
                {"source": self.sources[0].pk, "category": self.sports.pk, "limit": 1}
            ],
        )
        queue_run()
        self.drain()
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[0]).count(), 1)
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[1]).count(), 0)
        self.save_settings(publishing_rules=[])
        queue_run()
        self.drain()
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[0]).count(), 1)
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[1]).count(), 1)

    def test_migration_normalizes_historical_intervals_above_new_range(self):
        import importlib
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection

        cfg = config()
        cfg.interval_minutes = 1440
        cfg.save()
        original_revision = cfg.revision
        migration = importlib.import_module("news.migrations.0020_publishing_limits")
        migration.normalize_interval(apps, SimpleNamespace(connection=connection))
        cfg.refresh_from_db()
        self.assertEqual(cfg.interval_minutes, 1000)
        self.assertEqual(cfg.revision, original_revision + 1)
        self.assertAlmostEqual(
            (cfg.next_run - timezone.now()).total_seconds(), 1000 * 60, delta=5
        )

    def test_interrupted_batch_rolls_back_both_articles_and_quota_then_retries(self):
        queue_run()
        self.writer.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            tick()
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.assertEqual(SourcePublishingWindow.objects.count(), 0)
        self.assertEqual(CrawlRun.objects.get().status, "queued")
        self.writer.side_effect = None
        self.drain()
        self.assertEqual(NewsArticle.objects.count(), 4)

    def test_manual_runs_do_not_postpone_the_saved_schedule(self):
        self.save_settings(enabled=True)
        due = config().next_run
        queue_run()
        self.drain()
        self.assertEqual(config().next_run, due)

    def test_source_category_rule_survives_a_category_rename_with_stable_slug(self):
        self.sports.name, self.sports.slug = "Match reports", "sports"
        self.sports.save()
        self.save_settings(
            publishing_rules=[
                {"source": self.sources[0].pk, "category": self.sports.pk, "limit": 1}
            ]
        )
        queue_run()
        self.drain()
        self.assertEqual(
            NewsArticle.objects.filter(
                source=self.sources[0], categories=self.sports
            ).count(),
            1,
        )

    def test_duplicates_do_not_use_quota_and_a_new_interval_renews_it(self):
        self.sources[1].enabled = False
        self.sources[1].save()
        self.save_settings(publish_per_source=1)
        queue_run()
        self.drain()
        self.assertEqual(NewsArticle.objects.count(), 1)
        window = SourcePublishingWindow.objects.get(source=self.sources[0])
        self.assertEqual(window.counts["*"], 1)
        queue_run()
        self.drain()
        self.assertEqual(self.writer.call_count, 1)
        future = timezone.now() + timedelta(minutes=61)
        with patch("news.automation.timezone.now", return_value=future):
            queue_run()
            self.drain()
        self.assertEqual(NewsArticle.objects.count(), 2)
        self.assertEqual(self.writer.call_count, 2)

    def test_rule_increase_does_not_reset_already_published_count(self):
        self.sources[1].enabled = False
        self.sources[1].save()
        rule = {"source": self.sources[0].pk, "category": self.sports.pk, "limit": 1}
        self.save_settings(publishing_rules=[rule])
        queue_run()
        self.drain()
        self.save_settings(publishing_rules=[{**rule, "limit": 3}])
        queue_run()
        self.drain()
        self.assertEqual(NewsArticle.objects.count(), 3)

    def test_paused_sources_never_publish_and_dry_runs_do_not_spend_quotas(self):
        self.sources[0].enabled = False
        self.sources[0].save()
        self.save_settings(
            publishing_rules=[
                {"source": self.sources[0].pk, "category": None, "limit": 100}
            ],
            publish_per_source=1,
        )
        queue_run(dry_run=True)
        self.drain()
        self.assertEqual(SourcePublishingWindow.objects.count(), 0)
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.writer.assert_not_called()
        queue_run()
        self.drain()
        self.assertFalse(NewsArticle.objects.filter(source=self.sources[0]).exists())
        self.assertEqual(NewsArticle.objects.filter(source=self.sources[1]).count(), 0)
        self.writer.assert_not_called()

    def test_disabling_schedule_cancels_queued_continuation(self):
        self.save_settings(enabled=True, publish_per_source=5)
        cfg = config()
        cfg.next_run = timezone.now() - timedelta(minutes=1)
        cfg.save()
        first = tick()
        self.assertEqual(first.trigger, "scheduled")
        self.assertTrue(CrawlRun.objects.filter(status="queued").exists())
        calls = self.writer.call_count
        self.save_settings(enabled=False)
        cancelled = tick()
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self.writer.call_count, calls)

    def test_expired_continuation_does_not_publish_in_a_new_interval(self):
        self.save_settings(interval_minutes=1, publish_per_source=5)
        queue_run()
        tick()
        child = CrawlRun.objects.get(status="queued")
        calls = self.writer.call_count
        with patch(
            "news.automation.timezone.now",
            return_value=child.cycle_started_at + timedelta(minutes=2),
        ):
            self.assertEqual(tick().status, "cancelled")
        self.assertEqual(self.writer.call_count, calls)

    def test_category_used_by_a_rule_cannot_be_deleted(self):
        self.save_settings(
            publishing_rules=[
                {"source": self.sources[0].pk, "category": self.sports.pk, "limit": 1}
            ]
        )
        self.assertEqual(
            self.client.delete(f"/api/admin/categories/{self.sports.pk}/").status_code,
            409,
        )
