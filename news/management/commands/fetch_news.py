import requests
from decouple import config
from django.core.management.base import BaseCommand

from news.models import NewsArticle, NewsSource

REQUEST_TIMEOUT_SECONDS = 15
FALLBACK_SOURCE_URL = "https://newsapi.org"

class Command(BaseCommand):
    help = 'Fetch news articles from multiple sources and save them to the database'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting news fetch...'))

        api_key = config("NEWS_API_KEY", default="")
        if not api_key:
            self.stdout.write(self.style.ERROR("NEWS_API_KEY is not configured."))
            return

        url = "https://newsapi.org/v2/top-headlines"
        params = {"country": "us", "apiKey": api_key}
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.stdout.write(self.style.ERROR(f"Failed to fetch news: {exc}"))
            return

        articles = response.json().get("articles", [])
        if not articles:
            self.stdout.write(self.style.WARNING("No articles returned from upstream API."))
            return

        created_count, updated_count = self.save_articles(articles)
        self.stdout.write(
            self.style.SUCCESS(
                f"News fetch completed. Created: {created_count}, Updated: {updated_count}"
            )
        )

    def save_articles(self, articles):
        created_count = 0
        updated_count = 0

        for article in articles:
            source_data = article.get("source") or {}
            source_name = (source_data.get("name") or "Unknown").strip()
            source_url = (source_data.get("url") or FALLBACK_SOURCE_URL).strip()
            title = (article.get("title") or "").strip()
            description = article.get("description") or ""
            content = article.get("content") or description

            if not title or not content:
                continue

            source, _ = NewsSource.objects.get_or_create(
                name=source_name,
                defaults={"url": source_url},
            )
            if source_url and source.url != source_url:
                source.url = source_url
                source.save(update_fields=["url"])

            article_obj, created = NewsArticle.objects.update_or_create(
                title=title,
                source=source,
                defaults={
                    "description": description,
                    "content": content,
                    "author": article.get("author") or "",
                    "location": article.get("country", ""),
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if created else 'Updated'} article: {article_obj.title}"
                )
            )

        return created_count, updated_count
