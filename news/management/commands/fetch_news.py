from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from news.models import NewsArticle, NewsCategory, NewsSource


def web_url(value):
    value = value.strip() if isinstance(value, str) else ""
    return (
        value
        if urlparse(value).scheme in ("http", "https") and urlparse(value).netloc
        else ""
    )


class Command(BaseCommand):
    help = "Fetch and normalize news from NewsAPI and NewsData (default country: ng)"

    def add_arguments(self, parser):
        parser.add_argument("--country", default="ng")

    def handle(self, *args, **options):
        providers = []
        if settings.NEWS_API_KEY:
            providers.append(
                (
                    "https://newsapi.org/v2/top-headlines",
                    {
                        "country": options["country"],
                        "apiKey": settings.NEWS_API_KEY,
                    },
                    "articles",
                )
            )
        if settings.NEWS_DATA_IO_API_KEY:
            providers.append(
                (
                    "https://newsdata.io/api/1/news",
                    {
                        "country": options["country"],
                        "apikey": settings.NEWS_DATA_IO_API_KEY,
                    },
                    "results",
                )
            )
        if not providers:
            raise CommandError("Configure NEWS_API_KEY or NEWS_DATA_IO_API_KEY.")
        errors = []
        for url, params, key in providers:
            try:
                response = requests.get(url, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
                if data.get("status") not in ("ok", "success"):
                    raise ValueError("Provider returned an error status")
                self.save_articles(data.get(key, []), options["country"])
            except (requests.RequestException, ValueError):
                # Do not log request URLs: they contain provider credentials.
                errors.append(urlparse(url).hostname)
        if errors:
            raise CommandError("News fetch failed for: " + ", ".join(errors))

    def save_articles(self, articles, country="ng"):
        created_count = updated_count = skipped_count = 0
        for article in articles:
            if not isinstance(article, dict):
                skipped_count += 1
                continue
            title = str(article.get("title") or "").strip()
            description = str(article.get("description") or "")
            content = str(article.get("content") or description)
            if not title or title == "[Removed]" or not content:
                skipped_count += 1
                continue
            source_data = article.get("source") or {}
            if not isinstance(source_data, dict):
                source_data = {}
            source_name = str(
                article.get("source_name") or source_data.get("name") or "Unknown"
            )[:255]
            article_url = web_url(article.get("url") or article.get("link"))[:2000]
            origin = urlparse(article_url)
            source_url = web_url(
                article.get("source_url") or source_data.get("url")
            ) or (
                f"{origin.scheme}://{origin.netloc}"
                if article_url
                else "https://newsapi.org"
            )
            creator = article.get("creator") or article.get("author") or ""
            author = (
                ", ".join(str(x) for x in creator if x)
                if isinstance(creator, list)
                else str(creator)
            )
            locations = article.get("country") or country
            location_values = locations if isinstance(locations, list) else [locations]
            country_codes = {
                "nigeria": "ng",
                "united states of america": "us",
                "united states": "us",
                "united kingdom": "gb",
            }
            location = ", ".join(
                country_codes.get(str(x).lower(), str(x).lower())
                for x in location_values
            )
            published = article.get("publishedAt") or article.get("pubDate")
            try:
                published = (
                    parse_datetime(published) if isinstance(published, str) else None
                )
            except ValueError:
                published = None
            if published and timezone.is_naive(published):
                published = timezone.make_aware(
                    published, timezone.get_default_timezone()
                )
            categories = article.get("ai_tag") or article.get("category") or []
            if isinstance(categories, str):
                categories = [categories]
            if not isinstance(categories, list):
                categories = []
            tags = article.get("keywords") or []
            if not isinstance(tags, list):
                tags = []
            defaults = {
                "description": description,
                "content": content,
                "author": author[:255],
                "location": location[:100],
                "article_url": article_url,
                "remote_image_url": web_url(
                    article.get("urlToImage") or article.get("image_url")
                )[:2000],
            }
            if published:
                defaults["published_at"] = published
            stats = article.get("sentiment_stats")
            if isinstance(stats, dict):
                for label in ("positive", "neutral", "negative"):
                    try:
                        value = float(stats.get(label, 0))
                        if 0 <= value <= 100:
                            defaults[f"sentiment_{label}"] = value
                    except (TypeError, ValueError):
                        pass
            if article.get("sentiment") in ("positive", "neutral", "negative"):
                defaults["sentiment"] = article["sentiment"]
            with transaction.atomic():
                source, _ = NewsSource.objects.get_or_create(
                    name=source_name, defaults={"url": source_url}
                )
                obj, created = NewsArticle.objects.update_or_create(
                    title=title[:255],
                    source=source,
                    defaults=defaults,
                )
                if categories:
                    obj.categories.set(
                        [
                            NewsCategory.objects.get_or_create(name=str(name)[:100])[0]
                            for name in dict.fromkeys(
                                str(c).strip() for c in categories if c
                            )
                            if name
                        ]
                    )
                if tags:
                    obj.tags.set([str(t) for t in tags if t])
            created_count += int(created)
            updated_count += int(not created)
        self.stdout.write(
            f"Created: {created_count}, updated: {updated_count}, skipped: {skipped_count}"
        )
        return created_count, updated_count
