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
        from django.core.management.base import CommandError
        from news.automation import store_item
        from news.models import AutomationWorkerLock
        offline = bool(options.get('offline'))
        dry_run = bool(options.get('dry_run'))
        category = options.get('category') or 'Business'
        location = options.get('location') or 'ng'
        feeds = [dict(name='RSS Syndication', feed_url=options['url'], source_url=options['url'])] if options.get('url') else DEFAULT_RSS_SOURCES
        created = duplicates = errors = 0
        AutomationWorkerLock.objects.get_or_create(pk=1)
        with transaction.atomic():
            AutomationWorkerLock.objects.select_for_update().get(pk=1)
            if offline:
                # Explicit test fixture only; never used as a network fallback.
                items = parse_rss_or_atom_xml(OFFLINE_RSS_SAMPLE_XML)
                feeds = [dict(name='Offline test fixture', source_url='https://example.org', items=items)]
            for feed in feeds:
                try:
                    items = feed.get('items') if offline else fetch_and_parse_rss_feed(
                        feed['feed_url'], default_source=feed['name'], default_source_url=feed['source_url'],
                        default_location=location, default_category=category)
                    if not items:
                        raise ValueError('No eligible feed entries')
                    source = None
                    if not dry_run:
                        source, _ = NewsSource.objects.get_or_create(name=feed['name'], defaults={'url': feed['source_url']})
                        cat, _ = NewsCategory.objects.get_or_create(name=category)
                        source.default_category = cat
                        source.location = location
                    for item in items[:100]:
                        if dry_run:
                            self.stdout.write(f"Would validate: {item['title']}")
                            continue
                        with transaction.atomic():
                            result = store_item(item, source)
                        created += result == 'created'
                        duplicates += result == 'duplicates'
                except Exception:
                    errors += 1
            self.stdout.write(f'RSS: created {created}, duplicates {duplicates}, failed sources {errors}.')
        if errors:
            raise CommandError('Some feeds failed. No sample fallback was imported.')
