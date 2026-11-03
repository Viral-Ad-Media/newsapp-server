import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import TestCase
from rest_framework.test import APIClient
from .models import NewsArticle, NewsSource, ArticleTranslation


class TranslationTests(TestCase):
    def setUp(self):
        self.client=APIClient()
        self.source=NewsSource.objects.create(name='Publisher',url='https://example.org')
        self.article=NewsArticle.objects.create(title='Original report',description='A summary.',content='## Evidence\n\nThe source is https://example.org/report',source=self.source)
        self.ai=Mock()
        self.mock=patch('news.utils.ai_summarizer.client',self.ai); self.mock.start(); self.addCleanup(self.mock.stop)

    def get(self, language='fr', part='brief'):
        return self.client.get(f'/api/translations/{self.article.pk}/',{'language':language,'part':part})

    def result(self,payload):
        self.ai.with_options.return_value.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    def test_brief_translation_is_durable_and_invalidates_when_article_changes(self):
        self.result({'title':'Rapport original','description':'Un résumé.'})
        self.assertEqual(self.get().data['title'],'Rapport original')
        self.assertEqual(self.get().status_code,200)
        self.assertEqual(self.ai.with_options.return_value.chat.completions.create.call_count,1)
        self.article.title='Updated'; self.article.save()
        self.assertEqual(self.get().status_code,200)
        self.assertEqual(self.ai.with_options.return_value.chat.completions.create.call_count,2)

    def test_full_translation_keeps_source_links_and_english_original(self):
        self.result({'title':'Rapport','description':'Résumé','content':'## Preuves\n\nVoir https://example.org/report','why_matters':''})
        response=self.get(part='full'); self.assertEqual(response.status_code,200)
        self.assertIn('Preuves',response.data['content'])
        self.article.refresh_from_db(); self.assertEqual(self.article.title,'Original report')

    def test_unknown_languages_and_archived_reports_do_not_call_ai(self):
        self.assertEqual(self.get(language='invented').status_code,400)
        self.article.archived=True; self.article.save()
        self.assertEqual(self.get().status_code,404)
        self.ai.with_options.assert_not_called()

    def test_invalid_or_unavailable_translation_is_not_cached_as_success(self):
        self.result({'title':'Rapport'})
        self.assertEqual(self.get().status_code,503)
        self.assertEqual(ArticleTranslation.objects.count(),0)
        self.ai.with_options.side_effect=Exception('secret internal failure')
        response=self.get(); self.assertEqual(response.status_code,503)
        self.assertNotIn('secret',str(response.data))

    def test_concurrent_request_waits_and_english_needs_no_provider(self):
        self.result({'title':'Rapport','description':'Résumé'})
        self.get(); cached=ArticleTranslation.objects.get(); cached.payload={}; cached.save()
        self.assertEqual(self.get().status_code,202)
        self.assertEqual(self.get(language='en').data['title'],'Original report')
        self.assertEqual(self.ai.with_options.return_value.chat.completions.create.call_count,1)
