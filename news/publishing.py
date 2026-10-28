"""Automatic, non-blocking metadata preparation for crawled news.

The supplied n8n exports are design references, not a long-form news policy.
Article content, source attribution, author and publication time are preserved.
"""

import re
from html import unescape

METADATA_FIELDS = (
    "seo_title", "seo_description", "og_title", "og_description", "image_alt",
)


def metadata_text(value):
    """Extract plain preview text without modifying the stored source story."""
    value = re.sub(r"<!--.*?-->|<(script|style)\b[^>]*>.*?</\1\s*>", " ", str(value or ""), flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"!?\[([^\]]*)\]\([^\s)]+\)", r"\1", value)
    value = unescape(value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def preview_text(value, limit):
    text = metadata_text(value)
    if len(text) <= limit:
        return text
    shortened = text[:limit - 1]
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(" ,;:-") + "…"


def metadata_defaults(article):
    headline = preview_text(article.title, 60)
    summary = next((preview_text(value, 160) for value in (article.description, article.content, article.title) if metadata_text(value)), "")
    return {
        "seo_title": headline,
        "seo_description": summary,
        "og_title": preview_text(article.seo_title or headline, 120),
        "og_description": preview_text(article.seo_description or summary, 320),
        "image_alt": preview_text(article.title, 255) if article.remote_image_url or article.image else "",
    }


def populate_news_metadata(article, previous=None):
    """Fill missing previews and refresh automatic previews on provider updates.

    Source text, dates, bylines, taxonomy, attribution and coverage are untouched.
    Custom editorial metadata is retained. No image or author is invented.
    """
    old_defaults = metadata_defaults(previous) if previous is not None else {}
    changed = []
    # Prepare search fields before social fields so social fallbacks see the
    # updated search preview rather than a stale provider summary.
    for field in METADATA_FIELDS:
        value = metadata_defaults(article)[field]
        current = str(getattr(article, field) or "")
        was_automatic = previous is not None and current == str(getattr(previous, field) or "") and current == old_defaults[field]
        if (not current.strip() or was_automatic) and current != value:
            setattr(article, field, value)
            changed.append(field)
    return changed
