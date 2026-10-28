from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .models import NewsArticle


class AutomaticPublishingMigrationTests(TransactionTestCase):
    def test_previous_drafts_and_published_stories_become_normal_public_news(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('news', '0015_verified_sources')])
        try:
            apps = executor.loader.project_state([('news', '0015_verified_sources')]).apps
            Source = apps.get_model('news', 'NewsSource')
            Article = apps.get_model('news', 'NewsArticle')
            source = Source.objects.create(name='Original publisher', url='https://example.org')
            ids = []
            for status in ('draft', 'review', 'published'):
                article = Article.objects.create(title=f'Existing {status} story', content='Original source body.', description='Original short summary.', author='Original author', source=source, publication_status=status)
                ids.append((article.pk, article.published_at))
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        for pk, original_date in ids:
            article = NewsArticle.objects.get(pk=pk)
            self.assertEqual(article.content, 'Original source body.')
            self.assertEqual(article.author, 'Original author')
            self.assertEqual(article.published_at, original_date)
            self.assertEqual(article.seo_title, article.title)
            self.assertFalse(hasattr(article, 'publication_status'))
        self.assertEqual(NewsArticle.objects.count(), 3)
