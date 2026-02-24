import requests
from decouple import config
from django.core.management.base import BaseCommand

from news.models import NewsArticle, NewsCategory, NewsSource

REQUEST_TIMEOUT_SECONDS = 15
FALLBACK_SOURCE_URL = "https://newsapi.org"

class Command(BaseCommand):
    help = "Fetch news articles from external APIs"

    def handle(self, *args, **kwargs):
        # API keys and base URLs
        newsdata_api_key = config("NEWS_DATA_IO_API_KEY", default="")
        newsapi_api_key = config("NEWS_API_KEY", default="")
        newsdata_url = "https://newsdata.io/api/1/news"
        newsapi_url = "https://newsapi.org/v2/top-headlines"

        if not newsdata_api_key and not newsapi_api_key:
            self.stderr.write("No upstream API keys configured. Nothing to fetch.")
            return

        # Fetch data from both APIs
        self.stdout.write("Fetching news from NewsData API...")
        newsdata_articles = self.fetch_newsdata(
            newsdata_url,
            {"country": "ng", "apikey": newsdata_api_key},
        ) if newsdata_api_key else []

        self.stdout.write("Fetching news from NewsAPI...")
        newsapi_articles = self.fetch_newsapi(
            newsapi_url,
            {"country": "ng", "apiKey": newsapi_api_key},
        ) if newsapi_api_key else []

        # Combine articles
        combined_articles = newsdata_articles + newsapi_articles

        # Process and save articles
        self.stdout.write("Processing and saving articles...")
        self.process_articles(combined_articles)
        self.stdout.write("News fetching completed.")

    def fetch_newsdata(self, url, params):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except requests.exceptions.RequestException as e:
            self.stderr.write(f"Error fetching from NewsData API: {e}")
            return []

    def fetch_newsapi(self, url, params):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            return data.get("articles", [])
        except requests.exceptions.RequestException as e:
            self.stderr.write(f"Error fetching from NewsAPI: {e}")
            return []

    def process_articles(self, articles):
        for article in articles:
            # Extract data
            title = article.get("title", "No Title")
            description = article.get("description", "")
            content = article.get("content", "")
            author = ", ".join(article.get("creator", [])) if "creator" in article else "Unknown"
            source_name = article.get("source_name") or (article.get("source") or {}).get("name") or "Unknown"
            source_url = article.get("source_url") or (article.get("source") or {}).get("url") or FALLBACK_SOURCE_URL
            categories = article.get("ai_tag") or article.get("category") or ["Uncategorized"]
            if isinstance(categories, str):
                categories = [categories]
            sentiment = article.get("sentiment", "neutral")
            sentiment_stats = article.get("sentiment_stats", {})
            positive = sentiment_stats.get("positive", 0.0)
            neutral = sentiment_stats.get("neutral", 0.0)
            negative = sentiment_stats.get("negative", 0.0)

            # Handle NewsSource
            source, _ = NewsSource.objects.get_or_create(
                name=source_name,
                defaults={"url": source_url},
            )
            if source.url != source_url and source_url:
                source.url = source_url
                source.save(update_fields=["url"])

            # Handle NewsCategories
            category_objects = []
            for category_name in categories:
                category, _ = NewsCategory.objects.get_or_create(name=category_name)
                category_objects.append(category)

            # Save or update the NewsArticle
            article_obj, created = NewsArticle.objects.update_or_create(
                title=title,
                source=source,
                defaults={
                    "description": description,
                    "content": content,
                    "author": author,
                    "sentiment": sentiment,
                    "sentiment_positive": positive,
                    "sentiment_neutral": neutral,
                    "sentiment_negative": negative,
                },
            )

            # Assign categories
            if created or article_obj.categories.count() == 0:
                article_obj.categories.set(category_objects)

            self.stdout.write(f"{'Created' if created else 'Updated'}: {title}")
