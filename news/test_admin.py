from datetime import timedelta
from unittest.mock import patch
from .article_test_data import FULL_ARTICLE, PREPARED
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import (
    NewsArticle,
    NewsCategory,
    NewsSource,
    AdminEvent,
    CrawlRun,
    AutomationSettings,
)
from .automation import config, queue_run, tick, Busy
from .safe_fetch import validate_feed_url, fetch_xml

XML = """<rss><channel><title>Publisher</title><link>https://example.org</link><item><title>Real test headline</title><link>https://example.org/story?utm_source=test</link><description>A permitted brief summary.</description><pubDate>Mon, 19 Oct 2026 14:30:00 GMT</pubDate></item></channel></rss>"""


class AdminTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user(
            "operator", password="test-password-only", is_staff=True
        )
        self.reader = get_user_model().objects.create_user(
            "reader", password="test-password-only"
        )
        self.source = NewsSource.objects.create(
            name="Publisher",
            url="https://example.org",
            feed_url="https://example.org/feed",
            enabled=True,
        )
        self.category = NewsCategory.objects.create(name="Science", slug="science")
        self.source.default_category = self.category
        self.source.save()
        self.client.force_authenticate(self.staff)
        for target, value in [('news.article_writing.prepare_article', PREPARED), ('news.article_writing.source_evidence', FULL_ARTICLE)]:
            writer = patch(target, return_value=value)
            writer.start()
            self.addCleanup(writer.stop)
        credential = patch('news.utils.ai_summarizer.client', object())
        credential.start()
        self.addCleanup(credential.stop)

    def article(self, **kwargs):
        return NewsArticle.all_objects.create(
            title="Headline",
            description="Summary",
            content="Body",
            source=self.source,
            **kwargs,
        )

    def test_local_feed_setup_refuses_default_database_profile(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.test import override_settings

        with override_settings(LOCAL_DEMO=False):
            with self.assertRaises(CommandError):
                call_command("setup_local_feed")

    def test_admin_operations_reject_anonymous_and_readers(self):
        for user, expected in [(None, 401), (self.reader, 403)]:
            self.client.force_authenticate(user)
            for url in [
                "me",
                "dashboard",
                "articles",
                "categories",
                "sources",
                "settings",
                "logs",
                "runs",
            ]:
                self.assertEqual(
                    self.client.get(f"/api/admin/{url}/").status_code, expected
                )
                self.assertEqual(
                    self.client.post(
                        f"/api/admin/{url}/", {}, format="json"
                    ).status_code,
                    expected,
                )
            self.assertEqual(
                self.client.put(
                    "/api/admin/settings/", {"enabled": True}, format="json"
                ).status_code,
                expected,
            )

    def test_metrics_and_archive_public_visibility(self):
        self.article(imported=True)
        archived = self.article(archived=True)
        stats = self.client.get("/api/admin/dashboard/").data["stats"]
        self.assertEqual(stats["total_articles"], 2)
        self.assertEqual(stats["published_articles"], 1)
        self.assertEqual(stats["fetched_24h"], 1)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/news/").data["count"], 1)
        self.assertEqual(self.client.get(f"/api/news/{archived.pk}/").status_code, 404)

    def test_editorial_create_rejects_summary_in_place_of_article(self):
        response = self.client.post(
            "/api/admin/articles/",
            {
                "title": "Editorial",
                "description": "A short summary",
                "content": "",
                "source": self.source.pk,
                "categories": [],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("content", response.data)
        self.assertEqual(NewsArticle.objects.count(), 0)

    def test_article_search_filter_pagination_and_sort(self):
        for i in range(4):
            NewsArticle.objects.create(
                title=f"Article {i}",
                description="Science",
                content="Summary",
                source=self.source,
            )
        response = self.client.get(
            "/api/admin/articles/",
            {
                "search": "Article",
                "source": self.source.pk,
                "page_size": 2,
                "page": 2,
                "sort": "title",
            },
        )
        self.assertEqual(response.data["count"], 4)
        self.assertEqual(response.data["results"][0]["title"], "Article 2")
        self.assertEqual(
            self.client.get("/api/admin/articles/", {"source": "bad"}).status_code, 400
        )
        self.assertEqual(
            self.client.get("/api/admin/articles/", {"after": "bad"}).status_code, 400
        )

    def test_category_crud_and_conflicts_preserve_relationships(self):
        res = self.client.post(
            "/api/admin/categories/",
            {"name": "Technology", "slug": "technology"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        pk = res.data["id"]
        self.assertEqual(
            self.client.put(
                f"/api/admin/categories/{pk}/",
                {"name": "Tech", "slug": "tech"},
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/admin/categories/",
                {"name": "TECH", "slug": "other"},
                format="json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/admin/categories/",
                {"name": "Bad", "slug": "Bad Slug"},
                format="json",
            ).status_code,
            400,
        )
        self.staff.is_superuser = True
        self.staff.save()
        a = self.article()
        a.categories.add(self.category)
        self.assertEqual(
            self.client.delete(
                f"/api/admin/categories/{self.category.pk}/"
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.delete(f"/api/admin/categories/{pk}/").status_code, 204
        )
        self.assertTrue(NewsArticle.objects.filter(pk=a.pk).exists())

    @patch(
        "news.safe_fetch.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    def test_source_toggle_validation_and_health(self, _):
        self.assertEqual(
            self.client.put(
                f"/api/admin/sources/{self.source.pk}/",
                {"enabled": False},
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.put(
                f"/api/admin/sources/{self.source.pk}/",
                {"enabled": True},
                format="json",
            ).status_code,
            200,
        )
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_success)
        self.assertEqual(
            self.client.put(
                f"/api/admin/sources/{self.source.pk}/",
                {"feed_url": "http://127.0.0.1"},
                format="json",
            ).status_code,
            400,
        )

    @patch("news.automation.fetch_xml", return_value=XML)
    def test_run_now_real_pipeline_deduplicates_preserves_metadata(self, _):
        response = self.client.post("/api/admin/runs/", {}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(NewsArticle.objects.count(), 0)
        run = tick()
        self.assertEqual(run.status, "success")
        self.assertEqual(run.counts["published"], 1)
        article = NewsArticle.objects.get()
        self.assertEqual(article.article_url, "https://example.org/story")
        self.assertEqual(article.total_sources, 1)
        self.assertNotIn("Full report", article.content)
        self.assertEqual(article.categories.get(), self.category)
        article.seo_title = "Administrator preview"
        article.save()
        queue_run()
        result = tick()
        self.assertEqual(result.counts["duplicates"], 1)
        article.refresh_from_db()
        self.assertEqual(article.seo_title, "Administrator preview")
        self.assertEqual(NewsArticle.objects.count(), 1)

    @patch("news.automation.fetch_xml", return_value=XML)
    def test_dry_run_checks_disabled_source_without_publishing(self, _):
        self.source.enabled = False
        self.source.save()
        response = self.client.post(
            f"/api/admin/sources/{self.source.pk}/test/", {}, format="json"
        )
        self.assertEqual(response.status_code, 202)
        run = tick()
        self.assertEqual(run.counts["published"], 0)
        self.assertEqual(run.counts["created"], 0)
        self.assertEqual(run.counts["eligible"], 1)
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.source.refresh_from_db()
        self.assertIsNotNone(self.source.last_success)

    @patch("news.automation.fetch_xml", return_value=XML)
    def test_hourly_due_time_and_disabled_schedule(self, _):
        cfg = config()
        cfg.enabled = True
        cfg.save()
        run = tick()
        self.assertEqual(run.trigger, "scheduled")
        cfg.refresh_from_db()
        self.assertAlmostEqual(
            (cfg.next_run - timezone.now()).total_seconds(), 3600, delta=5
        )
        self.assertIsNone(tick())
        cfg.enabled = False
        cfg.next_run = timezone.now() - timedelta(days=1)
        cfg.save()
        self.assertIsNone(tick())

    @patch("news.automation.fetch_xml", return_value=XML)
    def test_restart_keeps_durable_queue_and_prevents_overlap(self, _):
        queued = queue_run()
        with self.assertRaises(Busy):
            queue_run()
        # A failed worker transaction rolls back to the queued claim for restart.
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                tick()
                raise RuntimeError("simulated process failure")
        queued.refresh_from_db()
        self.assertEqual(queued.status, "queued")
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.assertEqual(tick().pk, queued.pk)
        self.assertIsNone(tick())

    def test_partial_failure_and_sanitized_logs(self):
        NewsSource.objects.create(
            name="Broken",
            url="https://broken.example",
            feed_url="https://broken.example/feed",
            enabled=True,
        )
        with patch(
            "news.automation.fetch_xml",
            side_effect=[XML, ValueError("secret-token-in-remote-error")],
        ):
            queue_run()
            run = tick()
        self.assertEqual(run.status, "partial")
        self.assertEqual(run.counts["failed_sources"], 1)
        self.assertEqual(run.counts["published"], 1)
        self.assertTrue(AdminEvent.objects.filter(severity="error", run=run).exists())
        self.assertNotIn("secret-token", str(self.client.get("/api/admin/logs/").data))

    @patch(
        "news.automation.fetch_xml",
        return_value="<rss><channel><item><title>Missing fields</title></item></channel></rss>",
    )
    def test_invalid_article_not_published_or_reported_success(self, _):
        queue_run()
        run = tick()
        self.assertEqual(run.counts["invalid"], 1)
        self.assertEqual(run.status, "failed")
        self.assertEqual(NewsArticle.objects.count(), 0)

    def test_settings_validation_optimistic_revision_and_audit(self):
        cfg = config()
        self.assertEqual(
            self.client.put(
                "/api/admin/settings/",
                {"revision": cfg.revision, "interval_minutes": 1},
                format="json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                "/api/admin/settings/",
                {"revision": cfg.revision, "secret": "no"},
                format="json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                "/api/admin/settings/",
                {"revision": cfg.revision, "enabled": True},
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.put(
                "/api/admin/settings/",
                {"revision": cfg.revision, "enabled": False},
                format="json",
            ).status_code,
            409,
        )
        self.assertTrue(AdminEvent.objects.filter(kind="audit").exists())

    def test_editable_metadata_and_imported_field_protection(self):
        article = self.article(imported=True)
        url = f"/api/admin/articles/{article.pk}/"
        self.assertEqual(
            self.client.put(
                url, {"revision": 1, "title": "Override"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                url, {"revision": 1, "seo_title": "New preview"}, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.put(
                url, {"revision": 1, "seo_title": "Old write"}, format="json"
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.put(
                f"/api/superadmin/articles/{article.pk}/",
                {"title": "Bypass"},
                format="json",
            ).status_code,
            400,
        )

    def test_staff_cannot_escalate_or_permanently_delete(self):
        article = self.article()
        self.assertEqual(
            self.client.delete(f"/api/admin/articles/{article.pk}/").status_code, 403
        )
        self.assertEqual(
            self.client.delete(f"/api/superadmin/articles/{article.pk}/").status_code,
            403,
        )
        self.assertEqual(
            self.client.put(
                f"/api/superadmin/users/{self.reader.pk}/",
                {"is_superuser": True},
                format="json",
            ).status_code,
            403,
        )

    def test_all_failed_and_no_sources_fail_honestly(self):
        with patch(
            "news.automation.fetch_xml", side_effect=ValueError("remote secret")
        ):
            queue_run()
            self.assertEqual(tick().status, "failed")
        self.source.enabled = False
        self.source.save()
        queue_run()
        self.assertEqual(tick().status, "failed")

    def test_ssrf_and_dns_mixed_addresses_blocked(self):
        for url in [
            "http://example.org",
            "https://user:pass@example.org",
            "https://example.org:444/",
            "https://example.org/?key=secret",
            "file:///etc/passwd",
        ]:
            with self.assertRaises(ValueError):
                validate_feed_url(url)
        for address in [
            "127.0.0.1",
            "169.254.169.254",
            "10.0.0.1",
            "::1",
            "::ffff:127.0.0.1",
        ]:
            with patch(
                "news.safe_fetch.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", (address, 443))],
            ):
                with self.assertRaises(ValueError):
                    validate_feed_url("https://example.org")

    @patch(
        "news.safe_fetch.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    def test_safe_transport_pins_dns_and_rejects_redirect_and_large_body(self, _):
        from unittest.mock import MagicMock

        pool = MagicMock()
        response = pool.urlopen.return_value
        response.status = 302
        with patch(
            "news.safe_fetch.urllib3.HTTPSConnectionPool", return_value=pool
        ) as factory:
            with self.assertRaises(ValueError):
                fetch_xml("https://example.org/feed")
            self.assertEqual(factory.call_args.args[0], "93.184.216.34")
            self.assertEqual(factory.call_args.kwargs["cert_reqs"], "CERT_REQUIRED")
            self.assertTrue(factory.call_args.kwargs["ca_certs"])
            self.assertEqual(factory.call_args.kwargs["assert_hostname"], "example.org")
            self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])
            response.status = 200
            response.headers = {}
            response.read.return_value = b"x" * 32768
            with self.assertRaises(ValueError):
                fetch_xml("https://example.org/feed")
