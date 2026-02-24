from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import NewsArticle, NewsCategory, NewsSource


class NewsApiTests(APITestCase):
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
        result_ids = [item["id"] for item in response.data]
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
