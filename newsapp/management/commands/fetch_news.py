import requests
from django.core.management.base import BaseCommand
from news.models import NewsSource, NewsCategory, NewsArticle
from decouple import config

class Command(BaseCommand):
    help = "Fetch news articles from external APIs"

    def handle(self, *args, **kwargs):
        # API keys and base URLs
        newsdata_api_key = config('NEWS_DATA_IO_API_KEY')
        newsdata_url = f"https://newsdata.io/api/1/sources?country=ng&apikey={newsdata_api_key}"
        
        newsapi_api_key = config('NEWSAPI_API_KEY')
        newsapi_url = f"https://newsapi.org/v2/top-headlines?country=ng&apiKey={newsapi_api_key}"
        
        # Fetch data from both APIs
        self.stdout.write("Fetching news from NewsData API...")
        newsdata_articles = self.fetch_newsdata(newsdata_url)
        
        self.stdout.write("Fetching news from NewsAPI...")
        newsapi_articles = self.fetch_newsapi(newsapi_url)
        
        # Combine articles
        combined_articles = newsdata_articles + newsapi_articles

        # Process and save articles
        self.stdout.write("Processing and saving articles...")
        self.process_articles(combined_articles)
        self.stdout.write("News fetching completed.")

    def fetch_newsdata(self, url):
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except requests.exceptions.RequestException as e:
            self.stderr.write(f"Error fetching from NewsData API: {e}")
            return []

    def fetch_newsapi(self, url):
        try:
            response = requests.get(url)
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
            source_name = article.get("source_name", "Unknown")
            source_url = article.get("source_url", "")
            categories = article.get("ai_tag", ["Uncategorized"])
            image_url = article.get("image_url", "")
            sentiment = article.get("sentiment", "neutral")
            sentiment_stats = article.get("sentiment_stats", {})
            positive = sentiment_stats.get("positive", 0.0)
            neutral = sentiment_stats.get("neutral", 0.0)
            negative = sentiment_stats.get("negative", 0.0)

            # Handle NewsSource
            source, _ = NewsSource.objects.get_or_create(name=source_name, url=source_url)

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
                    "image": image_url,
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
