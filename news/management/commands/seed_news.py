import json
from datetime import date, datetime, time, timezone
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from news.models import NewsArticle, NewsCategory, NewsSource

DATA_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "starter_news_2026_10_19.json"
)


class Command(BaseCommand):
    help = "Import 10 source-linked news summaries published on October 19, 2026."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Preview without writing."
        )

    def handle(self, *args, **options):
        articles = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        # One transaction avoids leaving a half-imported batch after a failure.
        with transaction.atomic():
            created = skipped = 0
            for item in articles:
                if NewsArticle.objects.filter(article_url=item["article_url"]).exists():
                    skipped += 1
                    continue
                if options["dry_run"]:
                    self.stdout.write(f'Would add: {item["title"]}')
                    created += 1
                    continue
                source, _ = NewsSource.objects.get_or_create(
                    name=item["source"], defaults={"url": item["source_url"]}
                )
                article = NewsArticle.objects.create(
                    title=item["title"],
                    author="AbokiNews summary desk",
                    description=item["description"],
                    content=item["content"],
                    article_url=item["article_url"],
                    source=source,
                    location=item["location"],
                    total_sources=1,
                    # Source pages expose dates; midnight UTC is a documented
                    # normalization, not a claim about their publication time.
                    published_at=datetime.combine(
                        date.fromisoformat(item["publication_date"]),
                        time.min,
                        tzinfo=timezone.utc,
                    ),
                )
                article.categories.set(
                    [
                        NewsCategory.objects.get_or_create(name=name)[0]
                        for name in item["categories"]
                    ]
                )
                article.tags.add("starter-batch-2026-10-19")
                created += 1
        label = "Would create" if options["dry_run"] else "Created"
        self.stdout.write(f"{label}: {created}; skipped existing URLs: {skipped}.")
