from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from .models import NewsArticle, NewsCategory, NewsSource, ReaderProfile

class LiveNewsTests(APITestCase):
    def setUp(self):
        self.source = NewsSource.objects.create(name="Live test source", url="https://example.org")
        self.sports = NewsCategory.objects.create(name="Sports")
        self.culture = NewsCategory.objects.create(name="Culture")
        self.user = get_user_model().objects.create_user(username="live-reader", password="test-only-password")
        profile, _ = ReaderProfile.objects.get_or_create(user=self.user)
        profile.categories.add(self.sports)
        self.articles = []
        for region, category in [("us", self.sports), ("us", self.culture), ("ng", self.sports)]:
            article = NewsArticle.objects.create(title=f"Live fixture {region} {category.name}", description="Test report", content="Source text", source=self.source, location=region)
            article.categories.add(category)
            self.articles.append(article)

    def test_anonymous_requests_cannot_access_live_api(self):
        response = self.client.get("/api/live/", {"location": "us"})
        self.assertEqual(response.status_code, 401)

    def test_members_receive_all_categories_in_their_country_not_only_subscriptions(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/live/", {"location": "us"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["id"] for item in response.data["results"]}, {article.id for article in self.articles[:2]})
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertTrue(all(item["categories"] for item in response.data["results"]))

    def test_live_category_filter_and_new_articles(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/live/", {"location": "us", "category": "Culture"})
        self.assertEqual([item["id"] for item in response.data["results"]], [self.articles[1].id])
        newest = NewsArticle.objects.create(title="New culture fixture", description="A new report", content="Source text", source=self.source, location="us")
        newest.categories.add(self.culture)
        response = self.client.get("/api/live/", {"location": "us", "category": "Culture"})
        self.assertEqual(response.data["results"][0]["id"], newest.id)

    def test_games_filter_includes_gaming_category(self):
        self.client.force_authenticate(self.user)
        gaming = NewsCategory.objects.create(name="Gaming")
        article = NewsArticle.objects.create(title="Gaming fixture", description="Gaming news", content="Source text", source=self.source, location="us")
        article.categories.add(gaming)
        response = self.client.get("/api/live/", {"location": "us", "category": "Games"})
        self.assertEqual([item["id"] for item in response.data["results"]], [article.id])
