from io import StringIO
from unittest.mock import patch
from .article_test_data import FULL_ARTICLE, PREPARED

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import NewsArticle, NewsSource
from .publishing import metadata_text, preview_text


class MetadataPreparationTests(SimpleTestCase):
    def test_preview_text_removes_hidden_markup_without_inventing_content(self):
        value = '<script>hidden claim</script><p>A source-backed summary &amp; update.</p>'
        self.assertEqual(metadata_text(value), 'A source-backed summary & update.')
        self.assertNotIn('hidden claim', preview_text(value, 160))
        self.assertLessEqual(len(preview_text('A factual headline ' * 30, 60)), 60)


class AutomaticNewsPublishingTests(TestCase):
    def setUp(self):
        self.source = NewsSource.objects.create(name='Original publisher', url='https://example.org')
        self.client = APIClient()

    def create_news(self, **changes):
        values = dict(title='A short breaking news headline', description='A short source-provided summary.', content='The original short article body.', source=self.source, article_url='https://example.org/story')
        values.update(changes)
        return NewsArticle.objects.create(**values)

    def test_legacy_short_articles_remain_visible_until_source_upgrade(self):
        published_at = timezone.now() - timezone.timedelta(days=3)
        article = self.create_news(author='Publisher byline', published_at=published_at)
        self.assertFalse(hasattr(article, 'publication_status'))
        self.assertFalse(hasattr(article, 'reviewed_fingerprint'))
        public = self.client.get(f'/api/news/{article.pk}/')
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.data['title'], article.title)
        self.assertEqual(public.data['content'], 'The original short article body.')
        self.assertEqual(article.published_at, published_at)
        self.assertEqual(article.author, 'Publisher byline')
        self.assertEqual(article.seo_title, article.title)
        self.assertEqual(article.seo_description, article.description)
        self.assertEqual(article.og_title, article.seo_title)
        self.assertEqual(self.client.get('/api/news/').data['count'], 1)
        self.assertIn(article.title, self.client.get('/api/rss/').content.decode())
        self.assertIn(f'/news/{article.pk}', self.client.get('/api/sitemap/').content.decode())

    def test_import_updates_refresh_automatic_metadata_and_remain_visible(self):
        article = self.create_news()
        original_date = article.published_at
        article.title = 'A revised breaking news headline'
        article.description = 'The provider has updated its source summary.'
        article.content = 'Updated source text, still short.'
        article.save(update_fields=['title', 'description', 'content'])
        article.refresh_from_db()
        self.assertEqual(article.seo_title, article.title)
        self.assertEqual(article.seo_description, article.description)
        self.assertEqual(article.og_description, article.description)
        self.assertEqual(article.published_at, original_date)
        self.assertEqual(self.client.get(f'/api/news/{article.pk}/').status_code, 200)

    def test_custom_metadata_is_preserved_and_missing_images_do_not_block_news(self):
        article = self.create_news(seo_title='Custom search headline', seo_description='Custom search summary', og_title='Custom sharing title')
        article.title = 'Updated provider headline'
        article.description = 'New provider summary'
        article.save()
        article.refresh_from_db()
        self.assertEqual(article.seo_title, 'Custom search headline')
        self.assertEqual(article.seo_description, 'Custom search summary')
        self.assertEqual(article.og_title, 'Custom sharing title')
        self.assertFalse(article.remote_image_url)
        self.assertFalse(article.image_alt)
        self.assertEqual(self.client.get(f'/api/news/{article.pk}/').status_code, 200)

    def test_taxonomy_changes_do_not_hide_or_unpublish_news(self):
        from .models import NewsCategory
        article = self.create_news()
        article.categories.add(NewsCategory.objects.create(name='Technology'))
        article.tags.add('technology')
        self.assertEqual(self.client.get(f'/api/news/{article.pk}/').status_code, 200)
        self.assertEqual(self.client.get('/api/categories/').data['results'][0]['article_count'], 1)

    def test_editor_written_full_articles_publish_with_metadata(self):
        editor = get_user_model().objects.create_user(username='editor', is_staff=True)
        self.client.force_authenticate(editor)
        response = self.client.post('/api/superadmin/articles/', {'title': 'A short local update', 'description': 'Source-based short summary.', 'content': FULL_ARTICLE}, format='json')
        self.assertEqual(response.status_code, 201)
        article_id = response.data['id']
        self.assertEqual(response.data['seo_title'], 'A short local update')
        self.assertNotIn('workflow_report', response.data)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/news/{article_id}/').status_code, 200)

    def test_rss_and_allsides_imports_are_public_without_operator_actions(self):
        with patch('news.article_writing.prepare_article', return_value=PREPARED), patch('news.article_writing.source_evidence', return_value=FULL_ARTICLE), patch('news.utils.ai_summarizer.client', object()):
            call_command('fetch_rss', offline=True, stdout=StringIO())
        call_command('fetch_allsides', offline=True, stdout=StringIO())
        self.assertEqual(self.client.get('/api/news/').data['count'], NewsArticle.objects.count())
        self.assertGreaterEqual(NewsArticle.objects.count(), 9)
        for article in NewsArticle.objects.all():
            self.assertTrue(article.seo_title)
            self.assertTrue(article.seo_description)
            self.assertEqual(self.client.get(f'/api/news/{article.pk}/').status_code, 200)

    def test_noindex_only_changes_sitemap_visibility_not_publication(self):
        article = self.create_news(seo_noindex=True)
        self.assertEqual(self.client.get(f'/api/news/{article.pk}/').status_code, 200)
        self.assertNotIn(f'/news/{article.pk}', self.client.get('/api/sitemap/').content.decode())
