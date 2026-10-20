from datetime import datetime, timezone
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_datetime

from news.models import NewsArticle, NewsCategory, NewsSource
from news.utils.allsides import (
    fetch_live_allsides_roundups,
    load_fallback_allsides_roundups,
)


class Command(BaseCommand):
    help = "Fetch AllSides Headline Roundups (From the Left / Center / Right) with curated offline fallback."

    def add_arguments(self, parser):
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Load directly from the local curated AllSides snapshot without making network requests.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview roundups without writing to the database.",
        )

    def handle(self, *args, **options):
        stories = []
        used_fallback = False

        if not options.get("offline"):
            try:
                stories = fetch_live_allsides_roundups()
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"Live AllSides fetch unavailable ({exc}); using curated AllSides snapshot."
                    )
                )
                stories = load_fallback_allsides_roundups()
                used_fallback = True
        else:
            stories = load_fallback_allsides_roundups()
            used_fallback = True

        with transaction.atomic():
            created = updated = 0
            for item in stories:
                if options.get("dry_run"):
                    self.stdout.write(f'Would import: {item["title"]}')
                    created += 1
                    continue

                source, _ = NewsSource.objects.get_or_create(
                    name=item.get("source", "AllSides Headline Roundup"),
                    defaults={
                        "url": item.get(
                            "source_url",
                            "https://www.allsides.com/unbiased-balanced-news",
                        ),
                        "bias": item.get("source_bias", "center"),
                    },
                )
                published_at = parse_datetime(item.get("published_at") or "")
                if not published_at:
                    published_at = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

                dominant_bias = item.get("dominant_bias", "balanced")
                defaults = {
                    "title": item["title"][:255],
                    "author": item.get("author", "AllSides Editorial Desk"),
                    "description": item.get("description", ""),
                    "content": item.get("content", item.get("description", "")),
                    "source": source,
                    "location": item.get("location", "us"),
                    "remote_image_url": item.get("remote_image_url", ""),
                    "image": item.get("cloudinary_public_id") or None,
                    "published_at": published_at,
                    "total_sources": item.get("total_sources", 3),
                    "left_coverage": item.get("left_coverage", 33),
                    "center_coverage": item.get("center_coverage", 34),
                    "right_coverage": item.get("right_coverage", 33),
                    "dominant_bias": dominant_bias,
                    "perspectives": item.get("perspectives", []),
                    "sentiment": item.get("sentiment", "neutral"),
                    "sentiment_positive": item.get(
                        "sentiment_positive",
                        22.0 if dominant_bias == "polarized" else 30.0,
                    ),
                    "sentiment_neutral": item.get(
                        "sentiment_neutral",
                        40.0 if dominant_bias == "polarized" else 45.0,
                    ),
                    "sentiment_negative": item.get(
                        "sentiment_negative",
                        38.0 if dominant_bias == "polarized" else 25.0,
                    ),
                }

                article, was_created = NewsArticle.objects.update_or_create(
                    article_url=item["article_url"],
                    defaults=defaults,
                )
                categories = [
                    NewsCategory.objects.get_or_create(name=name)[0]
                    for name in item.get("categories", ["Politics"])
                ]
                article.categories.set(categories)
                tags = item.get("tags") or ["allsides-roundup"]
                article.tags.add(*tags)

                if was_created:
                    created += 1
                else:
                    updated += 1

        mode = "snapshot" if used_fallback else "live"
        self.stdout.write(
            f"AllSides ({mode}): created {created}, updated {updated} headline roundups."
        )
