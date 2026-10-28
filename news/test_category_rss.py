import xml.etree.ElementTree as ET
from django.test import TestCase
from rest_framework.test import APIClient
from .models import NewsArticle, NewsCategory, NewsSource


class CategoryRssTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        source = NewsSource.objects.create(name="Publisher", url="https://example.org")
        for index, name in enumerate(
            [
                "Sport",
                "Entertainment",
                "Gaming",
                "Financial",
                "Art",
                "Markets",
                "Housing",
            ]
        ):
            category = NewsCategory.objects.create(name=name)
            article = NewsArticle.objects.create(
                title=f"{name} report",
                description="A real stored test summary.",
                content="Summary",
                source=source,
                article_url=f"https://example.org/{index}",
                location="ng",
            )
            article.categories.add(category)

    def test_category_rss_agrees_with_website_including_aliases(self):
        for category, expected in [
            ("Sports", "Sport report"),
            ("Entertainment", "Entertainment report"),
            ("Games", "Gaming report"),
            ("Finance", "Financial report"),
            ("Arts", "Art report"),
            ("Markets", "Markets report"),
            ("Housing", "Housing report"),
        ]:
            with self.subTest(category=category):
                news = self.client.get(
                    "/api/news/", {"category": category, "location": "ng"}
                )
                self.assertEqual([a["title"] for a in news.data["results"]], [expected])
                rss = self.client.get(
                    "/api/rss/", {"category": category, "location": "ng"}
                )
                self.assertEqual(rss.status_code, 200)
                root = ET.fromstring(rss.content)
                self.assertEqual(
                    [node.text for node in root.findall("./channel/item/title")],
                    [expected],
                )

    def test_empty_feed_is_valid_and_archived_articles_stay_private(self):
        article = NewsArticle.objects.get(title="Entertainment report")
        article.archived = True
        article.save()
        for category in ["Entertainment", "Food"]:
            response = self.client.get("/api/rss/", {"category": category})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                ET.fromstring(response.content).findall("./channel/item"), []
            )
