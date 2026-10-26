"""Transparent editorial heuristics, not a search-engine ranking prediction."""

import re
from html import unescape
from urllib.parse import urlsplit

from rest_framework import serializers

SEO_FIELDS = (
    "focus_keyword",
    "seo_title",
    "seo_description",
    "canonical_url",
    "og_title",
    "og_description",
    "og_image_url",
    "image_alt",
    "seo_noindex",
    "seo_nofollow",
    "schema_type",
)


class SeoInputSerializer(serializers.Serializer):
    focus_keyword = serializers.CharField(
        max_length=100, allow_blank=True, required=False
    )
    seo_title = serializers.CharField(max_length=120, allow_blank=True, required=False)
    seo_description = serializers.CharField(
        max_length=320, allow_blank=True, required=False
    )
    canonical_url = serializers.URLField(
        max_length=2000, allow_blank=True, required=False
    )
    og_title = serializers.CharField(max_length=120, allow_blank=True, required=False)
    og_description = serializers.CharField(
        max_length=320, allow_blank=True, required=False
    )
    og_image_url = serializers.URLField(
        max_length=2000, allow_blank=True, required=False
    )
    image_alt = serializers.CharField(max_length=255, allow_blank=True, required=False)
    seo_noindex = serializers.BooleanField(required=False)
    seo_nofollow = serializers.BooleanField(required=False)
    schema_type = serializers.ChoiceField(
        choices=["Article", "NewsArticle", "BlogPosting"], required=False
    )

    def validate(self, attrs):
        for key in ("canonical_url", "og_image_url"):
            if attrs.get(key) and urlsplit(attrs[key]).scheme not in ("http", "https"):
                raise serializers.ValidationError(
                    {key: "Use an absolute HTTP or HTTPS URL."}
                )
        return attrs


def plain_text(value):
    return unescape(re.sub(r"<[^>]+>", " ", str(value or "")))


def words(value):
    return re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", plain_text(value), flags=re.UNICODE)


def contains_phrase(text, phrase):
    haystack = " ".join(words(text)).casefold()
    needle = " ".join(words(phrase)).casefold()
    return bool(needle) and f" {needle} " in f" {haystack} "


def analyze_seo(data):
    title = str(data.get("seo_title") or data.get("title") or "").strip()
    description = str(
        data.get("seo_description") or data.get("description") or ""
    ).strip()
    content = str(data.get("content") or "")
    keyword = str(data.get("focus_keyword") or "").strip()
    body_words = words(content)
    paragraphs = [p for p in re.split(r"\n\s*\n", plain_text(content)) if p.strip()]
    headings = re.findall(r"(?m)^#{2,3}\s+(.+)$", content)
    # Only links our reader renders (Markdown and explicit HTTP URLs) are counted.
    markdown_links = re.findall(r"(?<!!)\[[^\]]+\]\(([^\s)]+)\)", content)
    urls = set(markdown_links + re.findall(r"https?://[^\s<>\])]+", content))
    site = urlsplit(str(data.get("site_url") or "")).netloc.casefold()
    internal = external = 0
    for url in urls:
        parsed = urlsplit(url)
        if url.startswith("/") and not url.startswith("//"):
            internal += 1
        elif parsed.scheme in ("http", "https") and parsed.netloc:
            if site and parsed.netloc.casefold() == site:
                internal += 1
            else:
                external += 1
    checks = []

    def check(key, label, passed, points, advice):
        checks.append(
            dict(id=key, label=label, passed=bool(passed), points=points, advice=advice)
        )

    check(
        "keyword",
        "Focus keyword",
        bool(words(keyword)),
        5,
        "Choose one specific phrase that describes this post.",
    )
    check(
        "title",
        "Descriptive search title",
        30 <= len(title) <= 60,
        10,
        "Aim for a clear title around 30–60 characters; this is an editorial guideline, not a Google limit.",
    )
    check(
        "description",
        "Useful search description",
        120 <= len(description) <= 160,
        10,
        "Write a distinct 120–160 character summary as a starting point; search snippets can vary.",
    )
    check(
        "keyword_title",
        "Keyword in search title",
        contains_phrase(title, keyword),
        10,
        "Use the focus phrase naturally in the title if it accurately describes the post.",
    )
    check(
        "keyword_description",
        "Keyword in description",
        contains_phrase(description, keyword),
        10,
        "Describe the topic naturally in the search description.",
    )
    check(
        "intro",
        "Topic clear in the introduction",
        contains_phrase(" ".join(body_words[:100]), keyword),
        10,
        "Explain the focus topic within the opening 100 words.",
    )
    check(
        "heading",
        "Helpful subheading",
        any(contains_phrase(h, keyword) for h in headings),
        5,
        "Add a useful ## or ### subheading about the focus topic; do not repeat keywords mechanically.",
    )
    check(
        "internal_links",
        "Related internal link",
        internal > 0,
        5,
        "Link to a genuinely related AbokiNews post with [clear text](/news/123).",
    )
    check(
        "external_links",
        "Supporting reference link",
        external > 0,
        5,
        "Link to the primary evidence or a reliable reference with descriptive text.",
    )
    has_image = bool(
        data.get("remote_image_url") or data.get("image_url") or data.get("image")
    )
    check(
        "image",
        "Descriptive image alternative",
        has_image and bool(str(data.get("image_alt") or "").strip()),
        10,
        "If a relevant image helps, add it and describe its content. Do not add unrelated imagery for points.",
    )
    check(
        "paragraphs",
        "Readable paragraphs",
        bool(paragraphs) and all(len(words(p)) <= 150 for p in paragraphs),
        10,
        "Break paragraphs over 150 words where it helps the reader.",
    )
    check(
        "substance",
        "Substantive body",
        len(body_words) >= 300,
        10,
        "Review whether the topic needs more explanation. 300 words is our review prompt, not a ranking requirement.",
    )
    score = sum(c["points"] for c in checks if c["passed"])
    return dict(
        version="aboki-editorial-v1",
        score=score,
        possible=100,
        checks=checks,
        word_count=len(body_words),
        title_length=len(title),
        description_length=len(description),
        internal_links=internal,
        external_links=external,
        indexing="noindex" if data.get("seo_noindex") else "index",
        note="Editorial checks only. Not a Google, Rank Math or AIOSEO score. Search engines may rewrite previews; quality matters more than reaching 100.",
    )
