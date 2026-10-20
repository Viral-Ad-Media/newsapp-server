"""
RSS 2.0 & Atom 1.0 feed parser and outbound RSS 2.0 XML builder for AbokiNews.
"""

from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
import html
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import requests
from django.utils.dateparse import parse_datetime

DEFAULT_RSS_SOURCES = [
    {
        "name": "BBC News World RSS",
        "feed_url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "source_url": "https://www.bbc.com/news",
        "location": "world",
        "category": "World",
        "bias": "center",
    },
    {
        "name": "Nairametrics RSS",
        "feed_url": "https://nairametrics.com/feed/",
        "source_url": "https://nairametrics.com",
        "location": "ng",
        "category": "Business",
        "bias": "center",
    },
    {
        "name": "NASA Breaking News RSS",
        "feed_url": "https://www.nasa.gov/news-release/feed/",
        "source_url": "https://www.nasa.gov",
        "location": "us",
        "category": "Science",
        "bias": "center",
    },
]


def strip_html_tags(text: str) -> str:
    if not text:
        return ""
    unescaped = html.unescape(str(text))
    cleaned = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_rss_date(raw_date: str) -> datetime:
    if not raw_date:
        return datetime.now(timezone.utc)
    raw = str(raw_date).strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    dt = parse_datetime(raw)
    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime.now(timezone.utc)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_rss_or_atom_xml(
    xml_text: str,
    default_source: str = "RSS Syndication",
    default_source_url: str = "https://nairametrics.com",
    default_location: str = "ng",
    default_category: str = "Business",
) -> list[dict]:
    """
    Parse either an RSS 2.0 (<rss><channel><item>) or Atom (<feed><entry>) XML document
    into normalized article dictionaries ready for database ingestion.
    """
    if not xml_text or not xml_text.strip():
        return []

    root = ET.fromstring(xml_text.strip())
    root_name = _local_name(root.tag).lower()

    channel_title = default_source
    channel_link = default_source_url
    entries = []

    if root_name == "rss" or root_name == "rdf":
        channel = None
        for child in root:
            if _local_name(child.tag).lower() == "channel":
                channel = child
                break
        target = channel if channel is not None else root
        for child in target:
            cname = _local_name(child.tag).lower()
            if cname == "title" and (child.text or "").strip():
                channel_title = child.text.strip()
            elif cname == "link" and (child.text or "").strip():
                channel_link = child.text.strip()
            elif cname == "item":
                entries.append(child)
    elif root_name == "feed":
        for child in root:
            cname = _local_name(child.tag).lower()
            if cname == "title" and (child.text or "").strip():
                channel_title = child.text.strip()
            elif cname == "link":
                href = child.attrib.get("href") or (child.text or "").strip()
                if href:
                    channel_link = href
            elif cname == "entry":
                entries.append(child)

    items = []
    for entry in entries:
        title = ""
        link = ""
        description = ""
        content = ""
        pub_date = ""
        author = ""
        image_url = ""
        categories = []

        for node in entry:
            nname = _local_name(node.tag).lower()
            text_val = (node.text or "").strip()

            if nname == "title" and text_val:
                title = strip_html_tags(text_val)
            elif nname == "link":
                href = node.attrib.get("href", "").strip()
                rel = node.attrib.get("rel", "alternate").strip()
                if href and (rel == "alternate" or not link):
                    link = href
                elif text_val and not link:
                    link = text_val
            elif nname in ("description", "summary") and text_val:
                description = strip_html_tags(text_val)
            elif nname in ("encoded", "content") and text_val:
                content = strip_html_tags(text_val)
            elif nname in ("pubdate", "published", "updated", "date") and text_val:
                pub_date = text_val
            elif nname in ("creator", "author"):
                if text_val:
                    author = strip_html_tags(text_val)
                else:
                    for sub in node:
                        if _local_name(sub.tag).lower() == "name" and (sub.text or "").strip():
                            author = sub.text.strip()
            elif nname == "category":
                cat_term = node.attrib.get("term") or text_val
                if cat_term:
                    categories.append(strip_html_tags(cat_term)[:50])
            elif nname in ("enclosure", "content", "thumbnail"):
                media_url = node.attrib.get("url", "").strip()
                media_type = node.attrib.get("type", "").lower()
                if media_url and (
                    not media_type
                    or media_type.startswith("image/")
                    or any(
                        media_url.lower().endswith(ext)
                        for ext in (".jpg", ".jpeg", ".png", ".webp")
                    )
                ):
                    image_url = media_url

        if not title or not (description or content):
            continue

        summary_text = description or content[:320]
        full_content = content if len(content) > len(summary_text) else summary_text
        if "Original summary by AbokiNews" not in full_content:
            full_content = (
                f"{full_content}\n\n"
                f"Syndicated via RSS from {channel_title}. Read the linked source for the full report."
            )

        items.append(
            {
                "title": title[:255],
                "article_url": link or channel_link,
                "source": channel_title[:100],
                "source_url": channel_link,
                "author": author[:150] or f"{channel_title} RSS Desk",
                "description": summary_text,
                "content": full_content,
                "published_at": parse_rss_date(pub_date),
                "remote_image_url": image_url,
                "location": default_location,
                "categories": categories[:3] if categories else [default_category],
            }
        )

    return items


def fetch_and_parse_rss_feed(
    feed_url: str,
    default_source: str = "RSS Syndication",
    default_source_url: str = "https://nairametrics.com",
    default_location: str = "ng",
    default_category: str = "Business",
    timeout: int = 12,
) -> list[dict]:
    response = requests.get(
        feed_url,
        headers={
            "User-Agent": "AbokiNews-RSS-Reader/1.0 (+https://abokinews.com)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_rss_or_atom_xml(
        response.text,
        default_source=default_source,
        default_source_url=default_source_url,
        default_location=default_location,
        default_category=default_category,
    )


def build_rss_20_xml(
    articles,
    *,
    site_title: str = "AbokiNews — Multi-Perspective News Feed",
    site_link: str = "http://localhost:3000",
    feed_url: str = "http://localhost:8000/api/rss/",
    description: str = "Balanced and source-linked news stories across Nigeria, the United States, the UK, and the world.",
) -> str:
    """
    Build a standards-compliant RSS 2.0 XML document from a queryset or list of NewsArticle objects.
    """
    now_rfc2822 = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">',
        "  <channel>",
        f"    <title>{escape(site_title)}</title>",
        f"    <link>{escape(site_link)}</link>",
        f"    <description>{escape(description)}</description>",
        "    <language>en</language>",
        f"    <lastBuildDate>{escape(now_rfc2822)}</lastBuildDate>",
        f'    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml" />',
    ]

    for article in articles:
        art_link = (
            article.article_url
            if getattr(article, "article_url", "")
            else f"{site_link.rstrip('/')}/news/{article.id}"
        )
        guid = f"{site_link.rstrip('/')}/news/{article.id}"
        pub_dt = getattr(article, "published_at", None) or datetime.now(timezone.utc)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        pub_rfc2822 = format_datetime(pub_dt)
        author_name = (
            getattr(article, "author", None)
            or getattr(getattr(article, "source", None), "name", None)
            or "AbokiNews Desk"
        )
        desc_text = getattr(article, "description", "") or ""
        img_url = getattr(article, "remote_image_url", "") or ""

        lines.append("    <item>")
        lines.append(f"      <title>{escape(str(article.title or ''))}</title>")
        lines.append(f"      <link>{escape(str(art_link))}</link>")
        lines.append(f'      <guid isPermaLink="false">{escape(str(guid))}</guid>')
        lines.append(f"      <pubDate>{escape(pub_rfc2822)}</pubDate>")
        lines.append(f"      <dc:creator>{escape(str(author_name))}</dc:creator>")
        lines.append(f"      <description>{escape(str(desc_text))}</description>")

        if hasattr(article, "categories"):
            for cat in article.categories.all():
                lines.append(f"      <category>{escape(str(cat.name))}</category>")

        if img_url and str(img_url).startswith(("http://", "https://")):
            mime = "image/webp" if str(img_url).endswith(".webp") else "image/jpeg"
            lines.append(
                f'      <enclosure url="{escape(str(img_url))}" length="0" type="{mime}" />'
            )

        lines.append("    </item>")

    lines.append("  </channel>")
    lines.append("</rss>")
    return "\n".join(lines) + "\n"
