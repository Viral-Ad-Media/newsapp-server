"""Import the October 20 editorial batch without changing existing posts or users."""

import json
from datetime import date, datetime, time, timezone
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from news.models import NewsArticle, NewsCategory, NewsSource

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "blog_posts_2026_10_20.json"


class Command(BaseCommand):
    help = "Import five original, reference-linked AbokiNews explainers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Preview without writing."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        created = skipped = 0
        for item in json.loads(DATA_FILE.read_text(encoding="utf-8")):
            # References are stable and unique within this batch. Preserve all
            # editorial changes on repeat deployments, including title edits.
            if NewsArticle.objects.filter(article_url=item["reference_url"]).exists():
                skipped += 1
                continue
            if options["dry_run"]:
                self.stdout.write(f'Would add: {item["title"]}')
                created += 1
                continue
            source, _ = NewsSource.objects.get_or_create(
                name=item["reference_name"],
                defaults={"url": item["reference_url"]},
            )
            article = NewsArticle.objects.create(
                title=item["title"],
                description=item["description"],
                content=item["content"] + "\n\nReference: " + item["reference_url"],
                author="AbokiNews editorial desk",
                source=source,
                article_url=item["reference_url"],
                location=item["location"],
                total_sources=1,
                # This is our editorial publication day, not the reference's date.
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
            article.tags.add(*item["tags"])
            created += 1
        label = "Would create" if options["dry_run"] else "Created"
        self.stdout.write(f"{label}: {created}; skipped existing URLs: {skipped}.")
