"""Saved configuration and actual published coverage, without synthetic region fallback."""

import json
import re
from pathlib import Path
from datetime import timedelta
from django.db.models import Count
from django.utils import timezone
from .models import NewsArticle, NewsCategory, NewsSource
from .regions import COUNTRY_NAMES, normalize_country

CATALOGUE_PATH = Path(__file__).parent / "data" / "rss_sources.json"


def source_coverage():
    catalogue = json.loads(CATALOGUE_PATH.read_text())
    countries = {
        code: {
            "code": code,
            "name": name,
            "configured_sources": 0,
            "enabled_sources": 0,
            "healthy_sources": 0,
            "published_categories": {},
        }
        for code, name in {"world": "All countries", **COUNTRY_NAMES}.items()
    }
    cutoff = timezone.now() - timedelta(hours=24)
    for source in NewsSource.objects.exclude(feed_url__isnull=True).exclude(
        feed_url=""
    ):
        code = normalize_country(source.location)
        for key in {"world", code} - {""}:
            countries[key]["configured_sources"] += 1
            countries[key]["enabled_sources"] += int(source.enabled)
            countries[key]["healthy_sources"] += int(
                bool(
                    source.last_success
                    and source.last_success >= cutoff
                    and not source.last_error
                )
            )
    groups = NewsArticle.objects.values("location", "categories").annotate(
        total=Count("id", distinct=True)
    )
    for group in groups:
        category = group["categories"]
        if category is None:
            continue
        codes = {
            normalize_country(token)
            for token in re.split(r"[,;|]", group["location"] or "")
        }
        for code in {"world", *codes} - {""}:
            counts = countries[code]["published_categories"]
            counts[str(category)] = counts.get(str(category), 0) + group["total"]
    return {
        "catalogue_checked_on": catalogue["checked_on"],
        "countries": list(countries.values()),
        "categories": list(
            NewsCategory.objects.order_by("name", "id").values("id", "name")
        ),
    }
