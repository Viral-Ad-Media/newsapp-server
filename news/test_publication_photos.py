from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from .article_test_data import FULL_ARTICLE, PREPARED
from .article_writing import ArticlePreparationError
from .automation import store_item
from .models import NewsArticle, NewsSource, SourcePhotoCheck
from .photo_hosting import normalized_photo, mirror_photo
from .photo_library import process_photo
from .publishing_retries import record_failure


class PublicationPhotoTests(TestCase):
    def setUp(self):
        self.source = NewsSource.objects.create(name='World publisher', url='https://publisher.test', location='world')
        self.item = dict(title='Solar guidance report', description='Source reporting', article_url='https://publisher.test/story', published_at=timezone.now()-timedelta(days=7), source_text=str(FULL_ARTICLE), categories=['Technology'])

    @patch('news.utils.ai_summarizer.client', new=object())
    @patch('news.article_writing.prepare_article', return_value=PREPARED)
    @patch('news.photo_library.resolve_photo', return_value=None)
    @patch('news.photo_library.cached_fallback', return_value=None)
    def test_missing_photo_rolls_back_and_retry_reuses_paid_preparation(self, fallback, resolve, prepare):
        with self.assertRaises(ArticlePreparationError) as error:
            store_item(self.item, self.source)
        self.assertEqual(error.exception.details['code'], 'photo_unavailable')
        self.assertFalse(NewsArticle.all_objects.exists())
        retry = record_failure(self.source, self.item, None, error.exception)
        self.assertEqual(retry.status, 'retry_wait')
        self.assertEqual(retry.item['_prepared_article'], PREPARED)
        retry.item['published_at'] = self.item['published_at']
        retry.item['remote_image_url'] = 'https://publisher.test/solar.jpg'
        self.assertEqual(store_item(retry.item, self.source), 'created')
        prepare.assert_called_once()

    @patch('news.utils.ai_summarizer.client', new=object())
    @patch('news.article_writing.prepare_article', return_value=PREPARED)
    def test_world_worker_publication_is_in_public_latest_live_and_today_with_dates(self, prepare):
        self.item['remote_image_url'] = 'https://publisher.test/solar.jpg'
        store_item(self.item, self.source)
        article = NewsArticle.objects.get()
        client = APIClient()
        for query in ['', '&filter=last_day']:
            data = client.get('/api/news/?location=world'+query).data['results'][0]
            self.assertEqual(data['id'], article.pk)
            self.assertTrue(data['image_url'])
            self.assertTrue(data['published_at'])
            self.assertTrue(data['source_published_at'])
        self.assertEqual(client.get('/api/edition/?location=world').data['latest'][0]['id'], article.pk)
        self.assertEqual(client.get('/api/news/?location=ng').data['results'], [])
        client.force_authenticate(get_user_model().objects.create_user('world-reader'))
        self.assertEqual(client.get('/api/live/?location=world').data['results'][0]['id'], article.pk)
        article.archived = True
        article.save()
        self.assertEqual(client.get('/api/live/?location=world').data['results'], [])

    @patch('news.photo_hosting.mirror_photo', return_value='https://res.cloudinary.com/test/image/upload/solar.webp')
    def test_hosting_preserves_publication_date_and_text(self, mirror):
        article = NewsArticle.all_objects.create(title='Solar report', description='Report', content='Original source grounded content', source=self.source, remote_image_url='https://publisher.test/solar.jpg')
        before = (article.published_at, article.content, article.title)
        process_photo(article)
        article.refresh_from_db()
        self.assertEqual((article.published_at, article.content, article.title), before)
        self.assertIn('res.cloudinary.com', article.remote_image_url)
        self.assertTrue(SourcePhotoCheck.objects.get(article=article).hosted)

    @override_settings(CLOUDINARY_API_KEY='', CLOUDINARY_API_SECRET='')
    def test_missing_hosting_credentials_back_off_without_losing_source_photo(self):
        article = NewsArticle.all_objects.create(title='Solar report', source=self.source, remote_image_url='https://publisher.test/solar.jpg')
        process_photo(article)
        check = SourcePhotoCheck.objects.get(article=article)
        self.assertEqual(check.status, 'retry')
        self.assertGreater(check.next_attempt, timezone.now()+timedelta(minutes=50))
        article.refresh_from_db()
        self.assertEqual(article.remote_image_url, 'https://publisher.test/solar.jpg')

    @patch('news.photo_library.cached_fallback')
    @patch('news.photo_hosting.mirror_photo', side_effect=RuntimeError('CDN temporarily unavailable'))
    def test_cdn_failure_keeps_available_publisher_photo(self, mirror, fallback):
        article = NewsArticle.all_objects.create(title='Solar report', source=self.source, remote_image_url='https://publisher.test/solar.jpg')
        process_photo(article)
        fallback.assert_not_called()
        article.refresh_from_db()
        self.assertEqual(article.remote_image_url, 'https://publisher.test/solar.jpg')
        self.assertEqual(SourcePhotoCheck.objects.get(article=article).status, 'retry')


class PhotographValidationTests(SimpleTestCase):
    def test_decodes_photo_and_rejects_html_and_tracking_pixels(self):
        data = BytesIO()
        Image.new('RGB', (640,360), 'blue').save(data, format='JPEG')
        self.assertEqual(Image.open(BytesIO(normalized_photo(data.getvalue()))).format, 'WEBP')
        tiny = BytesIO()
        Image.new('RGB', (1,1)).save(tiny, format='PNG')
        for invalid in [b'<html>Blocked</html>', tiny.getvalue()]:
            with self.assertRaises((ValueError, OSError)):
                normalized_photo(invalid)

    @override_settings(CLOUDINARY_CLOUD_NAME='test', CLOUDINARY_API_KEY='test-only', CLOUDINARY_API_SECRET='test-only')
    @patch('cloudinary.uploader.upload', return_value={'secure_url':'https://res.cloudinary.com/test/image/upload/photo.webp'})
    @patch('news.photo_hosting.photo_bytes', return_value=b'verified-photo-bytes')
    def test_uploads_verified_bytes_and_keeps_own_hosted_photo(self, download, upload):
        from types import SimpleNamespace
        article = SimpleNamespace(pk=42)
        hosted = mirror_photo(article, {'image_url':'https://publisher.test/photo.jpg'})
        self.assertEqual(hosted, 'https://res.cloudinary.com/test/image/upload/photo.webp')
        self.assertEqual(upload.call_args.args[0].read(), b'verified-photo-bytes')
        mirror_photo(article, {'image_url':hosted})
        download.assert_called_once()
        upload.assert_called_once()
