from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from news.models import NewsArticle, NewsCategory, NewsSource


class SeedBlogPostsTests(TestCase):
    def test_import_is_repeat_safe_and_preserves_editorial_changes(self):
        call_command("seed_blog_posts", stdout=StringIO())
        self.assertEqual(NewsArticle.objects.count(), 5)
        self.assertEqual(get_user_model().objects.count(), 0)
        for article in NewsArticle.objects.all():
            self.assertEqual(article.author, "AbokiNews editorial desk")
            self.assertEqual(str(article.published_at.date()), "2026-10-20")
            self.assertTrue(article.categories.exists())
            self.assertTrue(article.tags.filter(name="explainer").exists())
            self.assertIn(article.article_url, article.content)
            self.assertEqual(article.perspectives, [])
            self.assertFalse(article.remote_image_url)
        article = NewsArticle.objects.first()
        article.title = "Edited by the newsroom"
        article.save()
        output = StringIO()
        call_command("seed_blog_posts", stdout=output)
        article.refresh_from_db()
        self.assertEqual(article.title, "Edited by the newsroom")
        self.assertEqual(NewsArticle.objects.count(), 5)
        self.assertIn("Created: 0; skipped existing URLs: 5", output.getvalue())

    def test_preview_does_not_write_content_or_users(self):
        output = StringIO()
        call_command("seed_blog_posts", dry_run=True, stdout=output)
        for model in (NewsArticle, NewsCategory, NewsSource, get_user_model()):
            self.assertFalse(model.objects.exists())
        self.assertIn("Would create: 5", output.getvalue())

    def test_import_coexists_with_existing_news(self):
        call_command("seed_news", stdout=StringIO())
        original_ids = set(NewsArticle.objects.values_list("id", flat=True))
        call_command("seed_blog_posts", stdout=StringIO())
        self.assertEqual(NewsArticle.objects.count(), len(original_ids) + 5)
        self.assertEqual(
            NewsArticle.objects.filter(id__in=original_ids).count(), len(original_ids)
        )
