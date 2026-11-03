from io import StringIO
import json
from unittest.mock import patch
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import NewsArticle, NewsCategory, NewsSource
from .automation import canonical_url, queue_run, tick, store_item
from .admin_api import SourceInput
from .category_taxonomy import item_categories
from .regions import item_location, COUNTRY_NAMES
from .article_test_data import FULL_ARTICLE, PREPARED
from .source_coverage import CATALOGUE_PATH, source_coverage


class SourceConfigurationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user(
            "source-operator", is_staff=True
        )

    def test_rss_and_coverage_require_staff_while_public_news_stays_public(self):
        reader = get_user_model().objects.create_user("source-reader")
        for user, expected in [(None, 401), (reader, 403), (self.staff, 200)]:
            self.client.force_authenticate(user)
            for path in ["/api/rss/", "/api/admin/source-coverage/"]:
                self.assertEqual(self.client.get(path).status_code, expected)
            self.assertEqual(self.client.get("/api/news/").status_code, 200)
        rss = self.client.get("/api/rss/")
        self.assertNotIn("public", rss.get("Cache-Control", ""))

    def test_configurator_is_idempotent_preserves_edits_and_category_ids(self):
        gaming = NewsCategory.objects.create(name="Gaming", slug="gaming")
        finance = NewsCategory.objects.create(name="Editorial Money", slug="finance")
        call_command("configure_news_sources", stdout=StringIO())
        count = NewsSource.objects.count()
        self.assertGreater(count, 50)
        self.assertFalse(NewsSource.objects.filter(enabled=True).exists())
        self.assertFalse(NewsCategory.objects.filter(name="Games").exists())
        self.assertFalse(NewsCategory.objects.filter(name="Finance").exists())
        self.assertTrue(
            NewsCategory.objects.filter(pk=finance.pk, name="Editorial Money").exists()
        )
        source = NewsSource.objects.first()
        source.name, source.location, source.enabled = "Admin edited source", "ng", True
        source.save()
        call_command("configure_news_sources", stdout=StringIO())
        source.refresh_from_db()
        self.assertEqual(NewsSource.objects.count(), count)
        self.assertEqual(
            (source.name, source.location, source.enabled),
            ("Admin edited source", "ng", True),
        )
        self.assertTrue(
            NewsCategory.objects.filter(pk=gaming.pk, name="Gaming").exists()
        )
        self.assertEqual(NewsArticle.all_objects.count(), 0)

    def test_dry_run_does_not_save_configuration(self):
        call_command("configure_news_sources", dry_run=True, stdout=StringIO())
        self.assertEqual(NewsCategory.objects.count(), 0)
        self.assertEqual(NewsSource.objects.count(), 0)

    @patch("news.management.commands.verify_news_source.source_items")
    @patch(
        "news.management.commands.verify_news_source.source_evidence",
        return_value=FULL_ARTICLE,
    )
    @patch("news.article_writing.prepare_article")
    def test_evidence_verification_does_not_publish_write_health_or_call_ai(
        self, writer, evidence, fetch
    ):
        source = NewsSource.objects.create(
            name="Read-only verification", url="https://example.org"
        )
        fetch.return_value = [
            dict(
                title="Report",
                article_url="https://example.org/report",
                description="Summary",
            )
        ]
        output = StringIO()
        call_command("verify_news_source", source.pk, stdout=output)
        self.assertTrue(json.loads(output.getvalue())["samples"][0]["evidence_ready"])
        writer.assert_not_called()
        source.refresh_from_db()
        self.assertIsNone(source.last_attempt)
        self.assertIsNone(source.last_success)
        self.assertEqual(NewsArticle.all_objects.count(), 0)

    def test_catalogue_uses_supported_regions_and_verified_evidence_for_every_topic(
        self,
    ):
        from .category_taxonomy import CATEGORIES

        entries = json.loads(CATALOGUE_PATH.read_text())["sources"]
        self.assertEqual(
            {entry["location"] for entry in entries} - {"world"}, set(COUNTRY_NAMES)
        )
        self.assertEqual({entry["category"] for entry in entries}, set(CATEGORIES))
        self.assertEqual(len({entry["feed_url"] for entry in entries}), len(entries))
        for entry in entries:
            self.assertIn(entry["location"], {"world", *COUNTRY_NAMES})
            self.assertGreater(entry["verification"]["feed_entries"], 0)
            self.assertTrue(
                any(
                    sample.get("words", 0) >= 400
                    for sample in entry["verification"]["article_samples"]
                )
            )

    def test_feed_topics_resolve_saved_categories_without_creating_publisher_tags(self):
        sports = NewsCategory.objects.create(name="Sports")
        games = NewsCategory.objects.create(name="Gaming")
        science = NewsCategory.objects.create(name="Science")
        self.assertEqual(
            item_categories(["Football", "Unrecognized publisher tag"]), [sports]
        )
        self.assertEqual(item_categories(["video games"]), [games])
        self.assertEqual(item_categories(["Unrecognized"], science), [science])
        self.assertEqual(NewsCategory.objects.count(), 3)

    @patch("news.article_writing.prepare_article", return_value=PREPARED)
    @patch("news.article_writing.source_evidence", return_value=FULL_ARTICLE)
    @patch("news.utils.ai_summarizer.client", object())
    def test_import_uses_feed_topic_and_explicit_country_tags(self, *_):
        games = NewsCategory.objects.create(name="Games")
        business = NewsCategory.objects.create(name="Business")
        source = NewsSource.objects.create(
            name="Global publisher",
            url="https://example.org",
            location="world",
            default_category=business,
        )
        item = dict(
            title="A games report",
            description="A permitted source summary.",
            article_url="https://example.org/games",
            published_at=timezone.now(),
            categories=["Gaming", "Canada"],
        )
        self.assertEqual(store_item(item, source), "created")
        article = NewsArticle.objects.get()
        self.assertEqual(list(article.categories.all()), [games])
        self.assertEqual(article.location, "ca")
        self.assertEqual(item_location(["Technology"], "world"), "world")

    def test_tracking_removed_without_dropping_meaningful_query(self):
        self.assertEqual(
            canonical_url(
                "https://example.org/story?id=2&at_medium=RSS&at_campaign=test&cmp=rss&traffic_source=rss&utm_source=x#top"
            ),
            "https://example.org/story?id=2",
        )

    @patch("news.article_writing.source_evidence")
    @patch("news.utils.ai_summarizer.client", object())
    def test_blocked_evidence_leaves_model_budget_for_an_alternative(self, evidence):
        from .article_writing import ArticlePreparationError

        evidence.side_effect = ArticlePreparationError(
            "Publisher denied source access."
        )
        source = NewsSource.objects.create(
            name="Blocked publisher", url="https://example.org"
        )
        item = dict(
            title="Report",
            description="Summary",
            article_url="https://example.org/report",
            published_at=timezone.now(),
        )
        budget = [2, 6, 2]
        with self.assertRaises(ArticlePreparationError):
            store_item(item, source, budget=budget)
        self.assertEqual(budget, [2, 5, 1])

    @patch("news.article_writing.prepare_article", return_value=PREPARED)
    @patch("news.article_writing.source_evidence")
    @patch("news.automation.source_items")
    @patch("news.utils.ai_summarizer.client", object())
    def test_blocked_publisher_falls_through_and_unvisited_sources_get_next_turn(
        self, fetch, evidence, writer
    ):
        from .article_writing import ArticlePreparationError

        sources = [
            NewsSource.objects.create(
                name=f"Fallback {index}",
                url="https://example.org",
                feed_url=f"https://example.org/feed/{index}",
                enabled=True,
            )
            for index in range(3)
        ]

        def items(source, _):
            return [
                dict(
                    title=f"Report {source.pk} {index}",
                    description="Summary",
                    article_url=f"https://example.org/{source.pk}/{index}",
                    published_at=timezone.now(),
                )
                for index in range(3)
            ]

        fetch.side_effect = items

        def retrieve(url, _):
            if f"/{sources[0].pk}/" in url:
                raise ArticlePreparationError("Publisher denied source access.")
            return FULL_ARTICLE

        evidence.side_effect = retrieve
        queue_run()
        run = tick()
        self.assertEqual(run.counts["created"], 1)
        self.assertEqual(run.counts["invalid"], 2)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(evidence.call_count, 3)
        sources[2].refresh_from_db()
        self.assertIsNone(sources[2].last_attempt)
        # A durable continuation now owns the next batch of this interval.
        tick()
        sources[2].refresh_from_db()
        self.assertIsNotNone(sources[2].last_attempt)

    def test_source_edition_validation_matches_frontend_regions(self):
        for value, expected in [("UK", "gb"), ("Nigeria", "ng"), ("world", "world")]:
            self.assertEqual(SourceInput().validate_location(value), expected)
        from rest_framework.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            SourceInput().validate_location("not-a-country")

    def test_coverage_reports_actual_saved_counts_and_gaps_without_global_fallback(
        self,
    ):
        category = NewsCategory.objects.create(name="Science")
        source = NewsSource.objects.create(
            name="Country publisher",
            url="https://example.org",
            feed_url="https://example.org/rss",
            location="ca",
            enabled=True,
            last_success=timezone.now(),
        )
        for location, archived in [("ca", False), ("world", False), ("ca", True)]:
            article = NewsArticle.all_objects.create(
                title=location,
                description="Summary",
                content="Text",
                source=source,
                location=location,
                archived=archived,
            )
            article.categories.add(category)
        report = source_coverage()
        countries = {country["code"]: country for country in report["countries"]}
        self.assertEqual(countries["ca"]["published_categories"][str(category.pk)], 1)
        self.assertEqual(
            countries["world"]["published_categories"][str(category.pk)], 2
        )
        self.assertEqual(countries["ca"]["healthy_sources"], 1)
        self.assertEqual(countries["jp"]["configured_sources"], 0)
        self.assertEqual(countries["jp"]["published_categories"], {})
        source.last_success = timezone.now() - timedelta(days=2)
        source.save()
        self.assertEqual(
            next(c for c in source_coverage()["countries"] if c["code"] == "ca")[
                "healthy_sources"
            ],
            0,
        )

    @patch("news.automation.source_items")
    def test_batches_rotate_to_previously_unchecked_sources(self, fetch):
        fetch.return_value = [
            dict(
                title="Report",
                description="Summary",
                article_url="https://example.org/story",
                published_at=timezone.now(),
            )
        ]
        sources = [
            NewsSource.objects.create(
                name=f"Publisher {index}",
                url="https://example.org",
                feed_url=f"https://example.org/feed/{index}",
                enabled=True,
            )
            for index in range(21)
        ]
        queue_run(dry_run=True)
        first = tick()
        self.assertEqual(first.counts["sources"], 20)
        sources[-1].refresh_from_db()
        self.assertIsNone(sources[-1].last_attempt)
        queue_run(dry_run=True)
        tick()
        sources[-1].refresh_from_db()
        self.assertIsNotNone(sources[-1].last_attempt)
