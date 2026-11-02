"""Add the shared topic directory and checked RSS catalogue without overwriting admin edits."""

import json
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
from news.category_taxonomy import CATEGORIES, canonical_category
from news.models import NewsCategory, NewsSource
from news.source_coverage import CATALOGUE_PATH


class Command(BaseCommand):
    help = "Add missing categories and checked sources, disabled by default. Existing configuration is preserved."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview additions without database changes.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        category_count = source_count = 0
        categories = list(NewsCategory.objects.order_by("id"))
        by_name = {}
        for name in CATEGORIES:
            existing = next(
                (
                    category
                    for category in categories
                    if canonical_category(category.name) == name
                    or category.slug == slugify(name)
                ),
                None,
            )
            if existing is None:
                slug = slugify(name)
                existing = NewsCategory.objects.create(name=name, slug=slug)
                categories.append(existing)
                category_count += 1
            by_name[name] = existing
        catalogue = json.loads(CATALOGUE_PATH.read_text())
        for entry in catalogue["sources"]:
            _, created = NewsSource.objects.get_or_create(
                feed_url=entry["feed_url"],
                defaults={
                    "name": entry["name"],
                    "url": entry["url"],
                    "location": entry["location"],
                    "default_category": by_name[entry["category"]],
                    "enabled": False,
                },
            )
            source_count += int(created)
        if options["dry_run"]:
            transaction.set_rollback(True)
        self.stdout.write(
            f"{'Would add' if options['dry_run'] else 'Added'} {category_count} categories and {source_count} disabled RSS sources. Existing settings preserved; no articles fetched or published."
        )
