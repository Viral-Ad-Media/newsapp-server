from datetime import datetime, timezone
from django.core.management.base import BaseCommand
from django.db import transaction

from news.models import NewsArticle, NewsCategory, NewsSource
from news.utils.rss_ingestor import (
    DEFAULT_RSS_SOURCES,
    fetch_and_parse_rss_feed,
    parse_rss_or_atom_xml,
)

OFFLINE_RSS_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>AbokiNews RSS Syndication Wire</title>
    <link>https://nairametrics.com</link>
    <description>Curated RSS syndication wire for offline and resilient ingestion.</description>
    <item>
      <title>West African central banks advance cross-border instant payment interoperability</title>
      <link>https://nairametrics.com/2026/10/19/ecowas-central-banks-advance-papss-instant-payment-settlement/</link>
      <dc:creator>AbokiNews RSS Desk</dc:creator>
      <pubDate>Mon, 19 Oct 2026 14:30:00 GMT</pubDate>
      <category>Markets</category>
      <category>Technology</category>
      <description>Regional central banks and commercial switches expanded Pan-African Payment and Settlement System routing to cut cross-border trade settlement times in local currencies.</description>
      <enclosure url="https://res.cloudinary.com/dz58pgz41/image/upload/v1792483613/abokinews/starter/story-11.webp" length="0" type="image/webp" />
    </item>
    <item>
      <title>African Development Bank backs regional agri-processing zones with $350m facility</title>
      <link>https://nairametrics.com/2026/10/19/afdb-approves-350m-agro-industrial-processing-facility/</link>
      <dc:creator>AbokiNews RSS Desk</dc:creator>
      <pubDate>Mon, 19 Oct 2026 15:15:00 GMT</pubDate>
      <category>Business</category>
      <description>The financing package targets rural cold-storage logistics, feeder roads, and outgrower credit across special agro-industrial processing zones.</description>
      <enclosure url="https://res.cloudinary.com/dz58pgz41/image/upload/v1792480242/abokinews/starter/story-1.webp" length="0" type="image/webp" />
    </item>
  </channel>
</rss>
"""


class Command(BaseCommand):
    help = "Ingest news articles from RSS 2.0 or Atom 1.0 syndication feeds."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            type=str,
            default="",
            help="Specific RSS/Atom feed URL to ingest.",
        )
        parser.add_argument(
            "--category",
            type=str,
            default="Business",
            help="Default category name for ingested RSS items.",
        )
        parser.add_argument(
            "--location",
            type=str,
            default="ng",
            help="Default regional desk code (ng, us, gb, world).",
        )
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Use curated offline RSS XML sample without network requests.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview RSS items without writing to the database.",
        )

    def handle(self, *args, **options):
        feed_url = (options.get("url") or "").strip()
        default_category = (options.get("category") or "Business").strip()
        default_location = (options.get("location") or "ng").strip().lower()
        offline = bool(options.get("offline"))
        dry_run = bool(options.get("dry_run"))

        parsed_items = []
        used_fallback = False

        if offline:
            parsed_items = parse_rss_or_atom_xml(
                OFFLINE_RSS_SAMPLE_XML,
                default_source="Nairametrics",
                default_source_url="https://nairametrics.com",
                default_location=default_location,
                default_category=default_category,
            )
            used_fallback = True
        elif feed_url:
            try:
                parsed_items = fetch_and_parse_rss_feed(
                    feed_url,
                    default_source="RSS Syndication",
                    default_source_url=feed_url,
                    default_location=default_location,
                    default_category=default_category,
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"Live RSS fetch failed ({exc}); falling back to offline RSS sample."
                    )
                )
                parsed_items = parse_rss_or_atom_xml(
                    OFFLINE_RSS_SAMPLE_XML,
                    default_source="Nairametrics",
                    default_source_url="https://nairametrics.com",
                    default_location=default_location,
                    default_category=default_category,
                )
                used_fallback = True
        else:
            for src in DEFAULT_RSS_SOURCES:
                try:
                    batch = fetch_and_parse_rss_feed(
                        src["feed_url"],
                        default_source=src["name"],
                        default_source_url=src["source_url"],
                        default_location=src["location"],
                        default_category=src["category"],
                    )
                    parsed_items.extend(batch[:5])
                except Exception:
                    continue

            if not parsed_items:
                parsed_items = parse_rss_or_atom_xml(
                    OFFLINE_RSS_SAMPLE_XML,
                    default_source="Nairametrics",
                    default_source_url="https://nairametrics.com",
                    default_location=default_location,
                    default_category=default_category,
                )
                used_fallback = True

        created = updated = 0
        with transaction.atomic():
            for item in parsed_items:
                if dry_run:
                    self.stdout.write(f'Would import RSS item: {item["title"]}')
                    created += 1
                    continue

                source, _ = NewsSource.objects.get_or_create(
                    name=item["source"][:100],
                    defaults={"url": item["source_url"], "bias": "center"},
                )

                pub_at = item.get("published_at") or datetime.now(timezone.utc)
                article_url = item.get("article_url") or ""

                defaults = {
                    "title": item["title"][:255],
                    "author": item.get("author", "RSS Syndication Desk")[:150],
                    "description": item["description"],
                    "content": item["content"],
                    "source": source,
                    "location": item.get("location", default_location),
                    "remote_image_url": item.get("remote_image_url", ""),
                    "published_at": pub_at,
                    "total_sources": 3,
                    "left_coverage": 32,
                    "center_coverage": 36,
                    "right_coverage": 32,
                    "dominant_bias": "balanced",
                    "perspectives": [
                        {
                            "bucket": "left",
                            "label": "From the Left",
                            "bias_rating": "Lean Left",
                            "source": "Public Interest Wire",
                            "source_url": item["source_url"],
                            "title": f"{item['title']} — social & developmental lens",
                            "summary": item["description"],
                            "url": article_url or item["source_url"],
                        },
                        {
                            "bucket": "center",
                            "label": "From the Center",
                            "bias_rating": "Center",
                            "source": item["source"],
                            "source_url": item["source_url"],
                            "title": item["title"],
                            "summary": item["description"],
                            "url": article_url or item["source_url"],
                        },
                        {
                            "bucket": "right",
                            "label": "From the Right",
                            "bias_rating": "Lean Right",
                            "source": "Market & Enterprise Wire",
                            "source_url": item["source_url"],
                            "title": f"{item['title']} — private capital & market view",
                            "summary": item["description"],
                            "url": article_url or item["source_url"],
                        },
                    ],
                    "sentiment": "neutral",
                    "sentiment_positive": 33.0,
                    "sentiment_neutral": 47.0,
                    "sentiment_negative": 20.0,
                }

                if article_url:
                    article, was_created = NewsArticle.objects.update_or_create(
                        article_url=article_url,
                        defaults=defaults,
                    )
                else:
                    article, was_created = NewsArticle.objects.update_or_create(
                        title=item["title"][:255],
                        defaults=defaults,
                    )

                cat_objs = [
                    NewsCategory.objects.get_or_create(name=c[:50])[0]
                    for c in (item.get("categories") or [default_category])
                    if c
                ]
                article.categories.set(cat_objs)
                article.tags.add("rss-syndicated")

                if was_created:
                    created += 1
                else:
                    updated += 1

        mode = "offline-sample" if used_fallback else "live-rss"
        self.stdout.write(
            f"RSS ({mode}): created {created}, updated {updated} articles."
        )
