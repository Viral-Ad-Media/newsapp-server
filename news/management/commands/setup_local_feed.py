"""Prepare a persistent isolated local admin and official RSS source, without publishing."""

import secrets
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from news.automation import config
from news.models import NewsCategory, NewsSource


class Command(BaseCommand):
    help = "Configure the isolated local feed profile. Requires --settings=newsapp.local_settings."

    def handle(self, *args, **options):
        if not getattr(settings, "LOCAL_DEMO", False) or connection.vendor != "sqlite":
            raise CommandError(
                "Local setup refused: use --settings=newsapp.local_settings."
            )
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username="support@viraladmedia.com",
            defaults={
                "email": "support@viraladmedia.com",
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        if not user.is_superuser:
            raise CommandError(
                "Existing local account is not a superuser; no permission changes were made."
            )
        if created or not user.has_usable_password():
            # No default password is shipped; the owner chooses one interactively.
            user.set_password(secrets.token_urlsafe(48))
            user.save(update_fields=["password"])
        category, _ = NewsCategory.objects.get_or_create(
            name="Science", defaults={"slug": "science"}
        )
        source, _ = NewsSource.objects.get_or_create(
            feed_url="https://www.nasa.gov/news-release/feed/",
            defaults={
                "name": "NASA News Releases",
                "url": "https://www.nasa.gov",
                "location": "us",
                "default_category": category,
                "enabled": True,
            },
        )
        cfg = config()
        if created:
            cfg.enabled = False
            cfg.max_per_source = 1
            cfg.save()
        self.stdout.write(f'Local database: {connection.settings_dict["NAME"]}')
        self.stdout.write(
            f"Local source #{source.pk}: {source.name} ({source.location})"
        )
        self.stdout.write(
            "No articles fetched or published by setup. Automation remains at its saved setting."
        )
        self.stdout.write("Set your local password privately:")
        self.stdout.write(
            "python manage.py changepassword support@viraladmedia.com --settings=newsapp.local_settings"
        )
