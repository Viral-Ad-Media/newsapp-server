import json
from datetime import date, datetime, time, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from news.models import NewsArticle, NewsCategory, NewsSource

DATA_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "starter_news_2026_10_19.json"
)
SUPERADMIN_EMAIL = "viraladmediacontent@gmail.com"
SUPERADMIN_PASSWORD = "Viraladmedia2018**"


def ensure_superadmin_user():
    User = get_user_model()
    user = (
        User.objects.filter(email__iexact=SUPERADMIN_EMAIL).first()
        or User.objects.filter(username__iexact=SUPERADMIN_EMAIL).first()
    )
    if not user:
        user = User(
            username=SUPERADMIN_EMAIL,
            email=SUPERADMIN_EMAIL,
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
    else:
        user.email = SUPERADMIN_EMAIL
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
    user.set_password(SUPERADMIN_PASSWORD)
    user.save()
    return user


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
                existing = NewsArticle.objects.filter(
                    article_url=item["article_url"]
                ).first()
                if existing:
                    if (
                        not options["dry_run"]
                        and not existing.remote_image_url
                        and item.get("remote_image_url")
                    ):
                        existing.remote_image_url = item["remote_image_url"]
                        if item.get("cloudinary_public_id") and not existing.image:
                            existing.image = item["cloudinary_public_id"]
                        existing.save(update_fields=["remote_image_url", "image"])
                    skipped += 1
                    continue
                if options["dry_run"]:
                    self.stdout.write(f'Would add: {item["title"]}')
                    created += 1
                    continue
                source, _ = NewsSource.objects.get_or_create(
                    name=item["source"], defaults={"url": item["source_url"]}
                )
                dominant_bias = item.get("dominant_bias", "balanced")
                article = NewsArticle.objects.create(
                    title=item["title"],
                    author="AbokiNews summary desk",
                    description=item["description"],
                    content=item["content"],
                    article_url=item["article_url"],
                    remote_image_url=item.get("remote_image_url", ""),
                    image=item.get("cloudinary_public_id") or None,
                    source=source,
                    location=item["location"],
                    total_sources=1,
                    left_coverage=item.get("left_coverage", 33),
                    center_coverage=item.get("center_coverage", 34),
                    right_coverage=item.get("right_coverage", 33),
                    dominant_bias=dominant_bias,
                    perspectives=item.get("perspectives", []),
                    sentiment=item.get("sentiment", "neutral"),
                    sentiment_positive=item.get(
                        "sentiment_positive",
                        24.0 if dominant_bias == "polarized" else 34.0,
                    ),
                    sentiment_neutral=item.get(
                        "sentiment_neutral",
                        40.0 if dominant_bias == "polarized" else 48.0,
                    ),
                    sentiment_negative=item.get(
                        "sentiment_negative",
                        36.0 if dominant_bias == "polarized" else 18.0,
                    ),
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
            if not options["dry_run"]:
                ensure_superadmin_user()
        label = "Would create" if options["dry_run"] else "Created"
        self.stdout.write(f"{label}: {created}; skipped existing URLs: {skipped}.")
