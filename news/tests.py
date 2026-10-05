from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import NewsArticle, NewsCategory, NewsSource


class NewsFixtures(APITestCase):
    def setUp(self):
        self.source = NewsSource.objects.create(
            name="Example Source",
            url="https://example.com",
        )

    def create_article(self, title, categories=None, location=""):
        article = NewsArticle.objects.create(
            title=title,
            description="Description",
            content="Content body",
            source=self.source,
            location=location,
        )
        if categories:
            article.categories.set(categories)
        return article


class NewsApiTests(NewsFixtures):
    def test_category_detail_returns_200(self):
        category = NewsCategory.objects.create(name="Politics")
        url = reverse("category_articles", kwargs={"category_id": category.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], category.id)
        self.assertEqual(response.data["name"], "Politics")

    def test_category_detail_missing_returns_404(self):
        url = reverse("category_articles", kwargs={"category_id": 999999})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_news_list_category_filter_returns_distinct_articles(self):
        category_one = NewsCategory.objects.create(name="Tech")
        category_two = NewsCategory.objects.create(name="Tech Policy")
        article = self.create_article(
            title="AI Regulation",
            categories=[category_one, category_two],
        )
        url = reverse("news-list")

        response = self.client.get(url, {"category": "Tech"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(result_ids.count(article.id), 1)

    def test_related_news_fallback_excludes_main_article(self):
        politics = NewsCategory.objects.create(name="Politics")
        sports = NewsCategory.objects.create(name="Sports")
        main_article = self.create_article("Main Story", categories=[politics])
        other_article = self.create_article("Other Story", categories=[sports])
        another_article = self.create_article("Another Story", categories=[sports])
        url = reverse("related_news", kwargs={"article_id": main_article.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item["id"] for item in response.data}
        self.assertNotIn(main_article.id, returned_ids)
        self.assertIn(other_article.id, returned_ids)
        self.assertIn(another_article.id, returned_ids)


class ReaderFixtures(NewsFixtures):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            username="reader", password="S3cure-reader-password"
        )
        self.other_user = get_user_model().objects.create_user(
            username="other", password="S3cure-other-password"
        )


class ReaderWorkflowTests(ReaderFixtures):
    def test_private_routes_require_authentication(self):
        for url in ("/api/feed/", "/api/saved/", "/api/preferences/", "/api/saved/1/"):
            self.assertEqual(self.client.get(url).status_code, 401)

    def test_preferences_filter_feed_and_do_not_affect_other_reader(self):
        topic = NewsCategory.objects.create(name="Science")
        selected = self.create_article("Science story", categories=[topic])
        other = self.create_article("Other story")
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.put(
                "/api/preferences/", {"categories": [topic.id]}, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(
            [a["id"] for a in self.client.get("/api/feed/").data["results"]],
            [selected.id],
        )
        self.client.force_authenticate(self.other_user)
        ids = {a["id"] for a in self.client.get("/api/feed/").data["results"]}
        self.assertEqual(ids, {selected.id, other.id})

    def test_save_unsave_and_reader_isolation(self):
        article = self.create_article("Saved story")
        self.client.force_authenticate(self.user)
        url = f"/api/saved/{article.id}/"
        self.assertEqual(self.client.put(url, {}, format="json").status_code, 200)
        self.assertEqual(self.client.put(url, {}, format="json").status_code, 200)
        self.assertTrue(self.client.get(url).data["saved"])
        self.assertEqual(self.client.get("/api/saved/").data["count"], 1)
        self.client.force_authenticate(self.other_user)
        self.assertFalse(self.client.get(url).data["saved"])
        self.assertEqual(self.client.get("/api/saved/").data["count"], 0)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get("/api/saved/").data["count"], 0)

    def test_preferences_reject_unknown_categories(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.put(
                "/api/preferences/", {"categories": [999999]}, format="json"
            ).status_code,
            400,
        )

    def test_news_pagination_is_bounded_and_stable(self):
        for i in range(25):
            self.create_article(f"Story {i}")
        first = self.client.get("/api/news/")
        self.assertEqual(first.data["count"], 25)
        self.assertEqual(len(first.data["results"]), 20)
        self.assertTrue(first.data["next"])
        second = self.client.get("/api/news/?page=2")
        self.assertEqual(len(second.data["results"]), 5)
        self.assertFalse(
            {a["id"] for a in first.data["results"]}
            & {a["id"] for a in second.data["results"]}
        )

    def test_exact_category_id_does_not_match_similarly_named_category(self):
        topic = NewsCategory.objects.create(name="Tech")
        other = NewsCategory.objects.create(name="Tech Policy")
        article = self.create_article("Tech story", categories=[topic])
        self.create_article("Policy story", categories=[other])
        response = self.client.get("/api/news/", {"category_id": topic.id})
        self.assertEqual([a["id"] for a in response.data["results"]], [article.id])

    def test_coverage_contract_and_summary(self):
        article = self.create_article("Coverage story")
        article.article_url = "https://example.com/story"
        article.remote_image_url = "https://example.com/story.jpg"
        article.save()
        data = self.client.get(f"/api/news/{article.id}/coverage/").data
        self.assertIsInstance(data["articles"], list)
        self.assertEqual(data["articles"][0]["url"], article.article_url)
        data = self.client.get(f"/api/news/{article.id}/").data
        self.assertEqual(data["summary"], article.description)
        self.assertEqual(data["image_url"], article.remote_image_url)

    def test_login_issues_jwt_and_jwt_authorizes_private_feed(self):
        response = self.client.post(
            "/api/token/",
            {"username": "reader", "password": "S3cure-reader-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {response.data["access"]}')
        self.assertEqual(self.client.get("/api/feed/").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/user/").status_code, 200)
        refresh = self.client.post(
            "/api/token/refresh/", {"refresh": response.data["refresh"]}, format="json"
        )
        self.assertEqual(refresh.status_code, 200)

    def test_registration_and_login(self):
        response = self.client.post(
            "/api/auth/registration/",
            {
                "username": "newreader",
                "password1": "S3cure-new-reader-password!",
                "password2": "S3cure-new-reader-password!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("access", response.data)

    def test_ask_validates_question_and_handles_unconfigured_ai(self):
        from unittest.mock import patch

        self.create_article("News headline")
        self.assertEqual(
            self.client.post("/api/ask/", {}, format="json").status_code, 400
        )
        with patch("news.utils.ai_summarizer.client", None):
            response = self.client.post(
                "/api/ask/", {"question": "What happened?"}, format="json"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["sources"]), 1)
        self.assertIn("unavailable", response.data["answer"])


class NewsIngestionTests(NewsFixtures):
    def test_command_is_registered_and_normalizes_both_providers(self):
        from django.core.management import get_commands
        from news.management.commands.fetch_news import Command
        from django.utils.dateparse import parse_datetime

        self.assertEqual(get_commands()["fetch_news"], "news")
        articles = [
            {
                "title": "Imported",
                "description": "Body",
                "content": None,
                "source": {"name": "Provider"},
                "author": None,
                "publishedAt": "2024-10-01T10:00:00Z",
                "url": "https://provider.test/story",
                "urlToImage": "https://provider.test/image.jpg",
            },
            {
                "title": "NewsData",
                "description": "Body",
                "creator": None,
                "source_name": "Data",
                "country": ["nigeria"],
                "category": ["science"],
                "pubDate": "2024-10-01 11:00:00",
                "keywords": ["news"],
            },
        ]
        command = Command()
        self.assertEqual(command.save_articles(articles), (2, 0))
        self.assertEqual(command.save_articles(articles), (0, 2))
        imported = NewsArticle.objects.get(title="Imported")
        self.assertEqual(imported.published_at, parse_datetime("2024-10-01T10:00:00Z"))
        self.assertEqual(imported.content, "Body")
        self.assertEqual(imported.location, "ng")
        data = NewsArticle.objects.get(title="NewsData")
        self.assertEqual(data.location, "ng")
        self.assertEqual(
            list(data.categories.values_list("name", flat=True)), ["science"]
        )

    def test_ingestion_skips_missing_text_and_does_not_log_provider_key(self):
        from news.management.commands.fetch_news import Command
        from django.core.management.base import CommandError
        from django.test import override_settings
        from unittest.mock import patch
        import requests

        self.assertEqual(
            Command().save_articles(
                [{}, {"title": "[Removed]", "content": "gone"}, None]
            ),
            (0, 0),
        )
        with override_settings(
            NEWS_API_KEY="must-not-be-logged", NEWS_DATA_IO_API_KEY=""
        ):
            with patch(
                "news.management.commands.fetch_news.requests.get",
                side_effect=requests.RequestException("must-not-be-logged"),
            ):
                with self.assertRaises(CommandError) as caught:
                    Command().handle(country="ng")
        self.assertNotIn("must-not-be-logged", str(caught.exception))


class SavedStatusTests(ReaderFixtures):
    def test_batch_status_is_bounded_and_scoped_to_reader(self):
        from news.models import ReaderProfile

        article = self.create_article("Stored story")
        profile = ReaderProfile.objects.create(user=self.user)
        profile.saved_articles.add(article)
        self.client.force_authenticate(self.user)
        self.assertEqual(
            self.client.get("/api/saved-status/", {"ids": str(article.id)}).data[
                "saved_ids"
            ],
            [article.id],
        )
        self.assertEqual(
            self.client.get(
                "/api/saved-status/", {"ids": ",".join([str(article.id)] * 101)}
            ).status_code,
            400,
        )
        self.client.force_authenticate(self.other_user)
        self.assertEqual(
            self.client.get("/api/saved-status/", {"ids": str(article.id)}).data[
                "saved_ids"
            ],
            [],
        )
