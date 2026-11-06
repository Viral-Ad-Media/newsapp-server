from unittest.mock import patch, Mock
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from .models import NewsSource, NewsCategory, NewsArticle, SourcePhotoCheck, TopicPhoto
from .source_photos import photo_url, article_photo, feed_photo
from .photo_library import resolve_photo, commons_photos
from .source_feeds import OFFICIAL_FEEDS, source_group_ids


class PhotoExtractionTests(SimpleTestCase):
    def test_publisher_metadata_and_article_only_image(self):
        self.assertEqual(article_photo('<meta property="og:image" content="/report.jpg">','https://publisher.test/report')['url'],'https://publisher.test/report.jpg')
        self.assertEqual(article_photo('<img src="/sidebar.jpg"><article><img src="/story.jpg" alt="Flood rescue"></article>','https://publisher.test/report')['alt'],'Flood rescue')
        self.assertFalse(article_photo('<img src="/unrelated.jpg">','https://publisher.test/report')['url'])
        self.assertEqual(feed_photo('<p><img src="https://cdn.test/photo.jpg"></p>','https://publisher.test/report')['url'],'https://cdn.test/photo.jpg')

    def test_unsafe_and_legacy_images_rejected(self):
        for value in ['', 'http://cdn.test/a.jpg','https://127.0.0.1/a.jpg','https://localhost/a.jpg','https://cdn.test/logo.png','https://site.test/api/news-illustration?x=1','https://site.test/images/editorial/news.webp']:
            self.assertEqual(photo_url(value),'')

    def test_jsonld_caption_credit(self):
        photo=article_photo('<script type="application/ld+json">{"@type":"NewsArticle","image":{"url":"https://cdn.test/photo.jpg","caption":"Rescue team","creditText":"Reporter"}}</script>','https://publisher.test/report')
        self.assertEqual(photo['credit'],'Reporter')


@patch('news.photo_library.curated_photos', return_value=[])
@override_settings(CLOUDINARY_API_KEY='', CLOUDINARY_API_SECRET='')
class PhotoRepairTests(TestCase):
    def setUp(self):
        self.source=NewsSource.objects.create(name='Publisher',url='https://publisher.test')
        self.category=NewsCategory.objects.create(name='Sports',slug='sports')
    def article(self, title='Football report'):
        article=NewsArticle.all_objects.create(title=title,description='Report',content='Original text',source=self.source,article_url='https://publisher.test/story')
        article.categories.add(self.category)
        return article
    def asset(self, key='photo-one'):
        return dict(key=key,topic='association football match',url=f'https://upload.wikimedia.org/{key}.jpg',reference_url=f'https://commons.wikimedia.org/wiki/File:{key}.jpg',credit='Photographer · CC BY 4.0 · Wikimedia Commons',caption='Football match')
    @patch('news.photo_library.commons_photos')
    @patch('news.article_writing.source_document')
    def test_original_page_photo_precedes_topic_and_preserves_story(self, page, library, curated):
        page.return_value='<meta property="og:image" content="https://publisher.test/news.jpg">'
        article=self.article();before=(article.title,article.content,article.published_at,article.revision)
        result=resolve_photo(article)
        self.assertEqual(result['image_url'],'https://publisher.test/news.jpg');library.assert_not_called()
        article.refresh_from_db();self.assertEqual((article.title,article.content,article.published_at,article.revision),before)
    @patch('news.photo_library.commons_photos')
    def test_unique_assignment_and_persisted_attribution(self, library, curated):
        library.return_value=[self.asset(),self.asset('photo-two')]
        first,second=self.article(),self.article()
        a=resolve_photo(first,source_first=False);b=resolve_photo(second,source_first=False)
        self.assertNotEqual(a['image_url'],b['image_url']);self.assertEqual(a['image_origin'],'topic')
        self.assertEqual(SourcePhotoCheck.objects.exclude(asset=None).count(),2)
        self.assertEqual(library.call_count,1)
        self.assertEqual(resolve_photo(first)['image_url'],a['image_url'])
    @patch('news.photo_library.commons_photos')
    def test_provider_outage_is_bounded_and_retries_without_fake_image(self, library, curated):
        library.return_value=[]
        article=self.article()
        self.assertIsNone(resolve_photo(article,source_first=False))
        self.assertIsNone(resolve_photo(article,source_first=False));library.assert_called_once()
        article.photo_check.refresh_from_db();self.assertEqual(article.photo_check.status,'retry')
    @patch('news.photo_library.commons_photos')
    def test_browser_failure_retains_original_but_has_unique_fallback(self, library, curated):
        library.return_value=[self.asset()]
        article=self.article();article.remote_image_url='https://publisher.test/report.jpg';article.save()
        response=APIClient().get(f'/api/article-photos/{article.pk}/?fallback=1')
        self.assertEqual(response.status_code,202);library.assert_not_called()
        from .photo_library import tick_images
        tick_images()
        response=APIClient().get(f'/api/article-photos/{article.pk}/?fallback=1')
        self.assertEqual(response.status_code,200);self.assertEqual(response.data['image_origin'],'topic')
        article.refresh_from_db();self.assertEqual(article.remote_image_url,'https://publisher.test/report.jpg')
    def test_archived_article_not_exposed(self, curated):
        article=self.article();article.archived=True;article.save()
        self.assertEqual(APIClient().get(f'/api/article-photos/{article.pk}/').status_code,404)

    def test_grid_interconnector_report_gets_a_distinct_cached_real_grid_photo(self, curated):
        from .photo_library import topic_for
        first=self.article('Electricity transmission report')
        second=self.article('UK Treasury outlines green gilt issuance and North Sea grid interconnectors for 2027')
        values=self.asset('grid-one');values['topic']='electricity transmission'
        asset=TopicPhoto.objects.create(**values)
        SourcePhotoCheck.objects.create(article=first,asset=asset,status='topic')
        values=self.asset('grid-two');values['topic']='electricity transmission'
        spare=TopicPhoto.objects.create(**values)
        self.assertEqual(topic_for(second),'electricity transmission')
        response=APIClient().get(f'/api/article-photos/{second.pk}/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['image_url'],spare.url)
        self.assertEqual(SourcePhotoCheck.objects.get(article=second).asset_id,spare.pk)


class LegacySourceEnableTests(TestCase):
    def setUp(self):
        self.client=APIClient();self.client.force_authenticate(get_user_model().objects.create_user('source-editor',is_staff=True))
    def test_all_five_reference_records_get_feeds_and_correct_location(self):
        rows=[]
        for host in OFFICIAL_FEEDS:
            rows.append(NewsSource.objects.create(name=host,url=f'https://{host}/old-reference',location='ng'))
        response=self.client.post('/api/admin/sources/enable-all/')
        self.assertEqual(response.status_code,200)
        for source in rows:
            source.refresh_from_db();self.assertTrue(source.enabled);self.assertEqual(source.feed_url,OFFICIAL_FEEDS[source.url.split('//')[1].removeprefix('www.')]['feed'])
        self.assertEqual(response.data['without_feed'],0)
    def test_existing_feed_owner_shares_enable_disable_preserving_source_ids(self):
        owner=NewsSource.objects.create(name='Nairametrics feed',url='https://nairametrics.com',feed_url='https://nairametrics.com/feed/')
        old=NewsSource.objects.create(name='Nairametrics',url='https://nairametrics.com/reference')
        self.client.post('/api/admin/sources/enable-all/')
        old.refresh_from_db();owner.refresh_from_db()
        self.assertEqual(old.feed_owner_id,owner.pk);self.assertTrue(old.enabled and owner.enabled)
        self.assertEqual(source_group_ids([old.pk]),{old.pk,owner.pk})
        response=self.client.put(f'/api/admin/sources/{old.pk}/',{'enabled':False},format='json')
        self.assertEqual(response.status_code,200);owner.refresh_from_db();self.assertFalse(owner.enabled)
        self.assertEqual(NewsSource.objects.count(),2)
        from .publishing_limits import source_limits
        from types import SimpleNamespace
        cfg = SimpleNamespace(publishing_rules=[{'source':old.pk,'category':None,'limit':2}],publish_per_source=1)
        self.assertEqual(source_limits(cfg,owner.pk,category_ids=[5,6]),{'5':2,'6':2})

class CommonsEligibilityTests(SimpleTestCase):
    def page(self, **changes):
        info = {'url':'https://upload.wikimedia.org/photo.jpg','thumburl':'https://thumb.wikimedia.org/photo.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:photo.jpg','mime':'image/jpeg','sha1':'unique-content-hash','width':1400,'height':900,'metadata':[{'name':'Make','value':'Camera'}],'extmetadata':{'LicenseShortName':{'value':'CC BY-SA 4.0'},'Artist':{'value':'<a href="/">Photographer</a>'},'ImageDescription':{'value':'Football match photograph'}}}
        info.update(changes)
        return {'title':'File:Football photograph.jpg','imageinfo':[info]}
    @patch('news.photo_library.requests.Session')
    def test_thumbnail_host_and_licence_attribution(self, session):
        response=session.return_value.__enter__.return_value.get.return_value
        response.content=b'{}';response.json.return_value={'query':{'pages':[self.page()]}}
        result=commons_photos('football match')
        self.assertEqual(len(result),1);self.assertIn('Photographer · CC BY-SA 4.0',result[0]['credit'])
        self.assertEqual(result[0]['url'],'https://thumb.wikimedia.org/photo.jpg')
    @patch('news.photo_library.requests.Session')
    def test_rejects_unknown_licences_graphics_and_non_provider_image_hosts(self, session):
        bad=self.page();bad['imageinfo'][0]['extmetadata']['LicenseShortName']['value']='All rights reserved'
        drawing=self.page();drawing['imageinfo'][0]['extmetadata']['ImageDescription']['value']='Football diagram illustration'
        response=session.return_value.__enter__.return_value.get.return_value
        response.content=b'{}';response.json.return_value={'query':{'pages':[bad,drawing,self.page(thumburl='https://untrusted.test/photo.jpg')]}}
        self.assertEqual(commons_photos('football match'),[])
