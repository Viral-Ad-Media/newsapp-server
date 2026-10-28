from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from .models import NewsArticle, NewsSource, NewsCategory, ReaderProfile
from .serializers import NewsArticleSerializer

class CountryEditionTests(APITestCase):
    def setUp(self):
        self.source = NewsSource.objects.create(name="Region fixture", url="https://example.org")
        self.category = NewsCategory.objects.create(name="Regional fixture")
        self.articles = {}
        for location in ["ng", "us", "gb", "uk", "world", "russia", "ng, us", "united states", "ca", "germany"]:
            article = NewsArticle.objects.create(title=f"Fixture {location}", description="Original reporting", content="Source text", location=location, source=self.source)
            article.categories.add(self.category)
            self.articles[location] = article

    def ids(self, path, country):
        response = self.client.get(path, {"location": country})
        self.assertEqual(response.status_code, 200)
        return {item["id"] for item in response.data["results"]}

    def test_us_feed_matches_complete_country_tokens_and_aliases(self):
        expected = {self.articles[key].id for key in ["us", "ng, us", "united states"]}
        for path in ["/api/news/", "/api/lopsided/"]:
            self.assertEqual(self.ids(path, "us"), expected)

    def test_uk_does_not_include_world_or_other_countries(self):
        self.assertEqual(self.ids("/api/news/", "gb"), {self.articles[key].id for key in ["gb", "uk"]})

    def test_new_country_alias_and_empty_country_edition(self):
        self.assertEqual(self.ids("/api/news/", "de"), {self.articles["germany"].id})
        self.assertEqual(self.ids("/api/news/", "jp"), set())

    def test_private_feeds_and_saved_stories_stay_regional(self):
        user = get_user_model().objects.create_user(username="reader-regions", password="test-only-password")
        profile, _ = ReaderProfile.objects.get_or_create(user=user)
        profile.saved_articles.add(*self.articles.values())
        self.client.force_authenticate(user)
        for path in ["/api/feed/", "/api/saved/"]:
            self.assertEqual(self.ids(path, "ca"), {self.articles["ca"].id})

    def test_ai_sources_never_fall_back_to_another_country(self):
        response = self.client.post("/api/ask/", {"question": "Latest headlines", "location": "ca"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([source["id"] for source in response.data["sources"]], [self.articles["ca"].id])
        response = self.client.post("/api/ask/", {"question": "Latest headlines", "location": "jp"}, format="json")
        self.assertEqual(response.data["sources"], [])

    def test_related_stories_keep_the_original_articles_country(self):
        response = self.client.get(f'/api/news/{self.articles["ca"].id}/related/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_category_counts_and_rss_are_regional(self):
        response = self.client.get("/api/categories/", {"location": "ca"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["article_count"], 1)
        response = self.client.get("/api/rss/", {"location": "ca"})
        self.assertContains(response, "Fixture ca")
        self.assertNotContains(response, "Fixture ng")
        self.assertNotContains(response, "Fixture world")

    def test_title_cards_are_excluded_and_real_source_photos_are_preserved(self):
        article = self.articles["ng"]
        base = "https://res.cloudinary.com/dz58pgz41/image/upload/v123/abokinews/starter/"
        article.remote_image_url = base + "story-13.webp"
        self.assertEqual(NewsArticleSerializer(article).data["image_url"], "")
        article.save(update_fields=["remote_image_url"])
        rss = self.client.get("/api/rss/", {"location": "ng"})
        self.assertNotContains(rss, "story-13.webp")
        article.remote_image_url = base + "story-4.webp"
        self.assertEqual(NewsArticleSerializer(article).data["image_url"], article.remote_image_url)
        self.assertEqual(NewsArticleSerializer(article).data["remote_image_url"], article.remote_image_url)
