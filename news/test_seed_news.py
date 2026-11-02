from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.contrib.auth import get_user_model

from news.models import NewsArticle, NewsCategory, NewsSource


class SeedNewsTests(TestCase):
    def test_content_seed_never_provisions_or_changes_privileged_accounts(self):
        User = get_user_model()
        self.assertEqual(User.objects.count(), 0)
        call_command("seed_news", stdout=StringIO())
        self.assertEqual(User.objects.count(), 0)
        user = User.objects.create_user(
            "seed-protection",
            email="viraladmediacontent@gmail.com",
            password="isolated-test-password-only",
            is_active=False,
        )
        original_password = user.password
        call_command("seed_news", stdout=StringIO())
        user.refresh_from_db()
        self.assertEqual(user.password, original_password)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_active)

    def test_import_populates_ten_source_linked_stories_and_categories(self):
        call_command("seed_news", stdout=StringIO())
        self.assertEqual(NewsArticle.objects.count(), 20)
        self.assertEqual(NewsSource.objects.count(), 2)
        self.assertEqual(NewsArticle.objects.filter(location="ng").count(), 14)
        for article in NewsArticle.objects.all():
            self.assertTrue(article.article_url.startswith("https://"))
            self.assertTrue(article.categories.exists())
            self.assertEqual(str(article.published_at.date()), "2026-10-19")
            self.assertEqual(article.total_sources, 1)
            self.assertIn("Original summary by AbokiNews", article.content)

    def test_repeat_import_keeps_existing_editorial_changes(self):
        call_command("seed_news", stdout=StringIO())
        article = NewsArticle.objects.first()
        article.title = "Editorially updated title"
        article.save()
        output = StringIO()
        call_command("seed_news", stdout=output)
        article.refresh_from_db()
        self.assertEqual(article.title, "Editorially updated title")
        self.assertEqual(NewsArticle.objects.count(), 20)
        self.assertIn("Created: 0; skipped existing URLs: 20", output.getvalue())

    def test_dry_run_does_not_write_any_objects(self):
        output = StringIO()
        call_command("seed_news", dry_run=True, stdout=output)
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.assertEqual(NewsSource.objects.count(), 0)
        self.assertEqual(NewsCategory.objects.count(), 0)
        self.assertIn("Would create: 20", output.getvalue())
