import html as htmllib
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

ALLSIDES_BASE_URL = "https://www.allsides.com"
ALLSIDES_ROUNDUPS_URL = "https://www.allsides.com/unbiased-balanced-news"
ALLSIDES_FALLBACK_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "allsides_roundups_2026_10_19.json"
)


def unwrap_astro(val):
    """Unwrap Astro island serialized tuple arrays ([0, val] / [1, list])."""
    if isinstance(val, list) and len(val) == 2 and isinstance(val[0], int):
        tag, inner = val
        if tag == 0:
            if isinstance(inner, dict):
                return {k: unwrap_astro(v) for k, v in inner.items()}
            return inner
        if tag == 1 and isinstance(inner, list):
            return [unwrap_astro(x) for x in inner]
    if isinstance(val, dict):
        return {k: unwrap_astro(v) for k, v in val.items()}
    return val


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = htmllib.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_bias_bucket(bias_title="", global_bias_name=""):
    combined = f"{bias_title} {global_bias_name}".lower()
    if "left" in combined:
        return "left"
    if "right" in combined:
        return "right"
    return "center"


def bucket_label(bucket):
    if bucket == "left":
        return "From the Left"
    if bucket == "right":
        return "From the Right"
    return "From the Center"


def compute_coverage_breakdown(perspectives, description_html=""):
    """
    Calculate Left / Center / Right coverage percentages and dominant bias
    from both explicit newsItems perspectives and inline source mentions.
    """
    counts = {"left": 0, "center": 0, "right": 0}
    seen_sources = set()

    for item in perspectives:
        bucket = item.get("bucket", "center")
        src_key = (item.get("source") or "").strip().lower()
        if bucket in counts:
            counts[bucket] += 2  # Weight primary roundup cards
        if src_key:
            seen_sources.add(src_key)

    # Also count inline AllSides bias citations in the roundup narrative
    for match in re.finditer(
        r'<a href="/news-source/[^"]+">([^<]+)</a>', description_html or ""
    ):
        label = htmllib.unescape(match.group(1)).strip()
        src_name = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip().lower()
        bias_part = label.lower()
        bucket = None
        if "left" in bias_part:
            bucket = "left"
        elif "right" in bias_part:
            bucket = "right"
        elif "center" in bias_part:
            bucket = "center"
        if bucket:
            counts[bucket] += 1
            if src_name:
                seen_sources.add(src_name)

    total_weight = sum(counts.values())
    if total_weight <= 0:
        return {
            "left_coverage": 33,
            "center_coverage": 34,
            "right_coverage": 33,
            "dominant_bias": "balanced",
            "total_sources": max(len(seen_sources), len(perspectives), 1),
        }

    left_pct = round((counts["left"] / total_weight) * 100)
    center_pct = round((counts["center"] / total_weight) * 100)
    right_pct = max(0, 100 - left_pct - center_pct)

    # Determine dominant bias / lopsided status
    if center_pct <= 22 and left_pct >= 35 and right_pct >= 35:
        dominant_bias = "polarized"
    elif left_pct >= right_pct + 12 or left_pct >= 44:
        dominant_bias = "left"
    elif right_pct >= left_pct + 12 or right_pct >= 44:
        dominant_bias = "right"
    elif center_pct >= 45:
        dominant_bias = "center"
    else:
        dominant_bias = "balanced"

    return {
        "left_coverage": left_pct,
        "center_coverage": center_pct,
        "right_coverage": right_pct,
        "dominant_bias": dominant_bias,
        "total_sources": max(len(seen_sources), len(perspectives), 3),
    }


def parse_allsides_html(raw_html):
    """Extract AllSides Headline Roundups from raw HTML."""
    stories = []
    seen_paths = set()

    for match in re.finditer(
        r'<astro-island[^>]*component-url="([^"]+)"[^>]*props="([^"]+)"', raw_html
    ):
        comp_url = match.group(1)
        if "headline-roundup" not in comp_url:
            continue
        try:
            props = unwrap_astro(json.loads(htmllib.unescape(match.group(2))))
        except (ValueError, TypeError):
            continue

        story_data = props.get("data") or {}
        if not isinstance(story_data, dict) or story_data.get("__typename") != "TermStory":
            continue

        path = story_data.get("path") or ""
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)

        title = (story_data.get("name") or "").strip()
        if not title:
            continue

        desc_html = ((story_data.get("description") or {}).get("value")) or ""
        raw_paragraphs = re.findall(r"<p>(.*?)</p>", desc_html, re.S)
        paragraphs = []
        for p in raw_paragraphs:
            cleaned = strip_html(p)
            if cleaned and "Written by the AllSides staff" not in cleaned:
                paragraphs.append(cleaned)

        description = paragraphs[0] if paragraphs else strip_html(desc_html)[:320]
        content = "\n\n".join(paragraphs) if paragraphs else description

        perspectives = []
        story_image = ""
        for item in story_data.get("newsItems") or []:
            if not isinstance(item, dict):
                continue
            src = item.get("newsSource") or {}
            bias = src.get("newsBias") or {}
            global_bias = bias.get("globalBias") or {}
            bias_title = (bias.get("title") or "Center").strip()
            bucket = normalize_bias_bucket(bias_title, global_bias.get("name") or "")

            img_obj = item.get("imageOfNews") or {}
            if not story_image and img_obj.get("url"):
                story_image = img_obj.get("url")

            item_path = item.get("path") or ""
            item_url = (
                f"{ALLSIDES_BASE_URL}{item_path}"
                if item_path.startswith("/")
                else (item_path or f"{ALLSIDES_BASE_URL}{path}")
            )
            src_path = src.get("path") or ""
            src_url = (
                f"{ALLSIDES_BASE_URL}{src_path}"
                if src_path.startswith("/")
                else (src_path or ALLSIDES_BASE_URL)
            )
            body_text = strip_html(((item.get("body") or {}).get("value")) or "")

            perspectives.append(
                {
                    "bucket": bucket,
                    "label": bucket_label(bucket),
                    "bias_rating": bias_title,
                    "source": (src.get("title") or "AllSides Source").strip(),
                    "source_url": src_url,
                    "title": (item.get("title") or title).strip(),
                    "summary": body_text,
                    "url": item_url,
                }
            )

        # Sort perspectives Left -> Center -> Right for consistent AllSides presentation
        bucket_order = {"left": 0, "center": 1, "right": 2}
        perspectives.sort(key=lambda p: bucket_order.get(p.get("bucket"), 1))

        breakdown = compute_coverage_breakdown(perspectives, desc_html)
        topic_name = ((story_data.get("storyTopic") or {}).get("name") or "Politics").strip()
        tags = [
            (t.get("name") or "").strip()
            for t in (story_data.get("storyTags") or [])
            if isinstance(t, dict) and t.get("name")
        ]
        if "allsides-roundup" not in tags:
            tags.append("allsides-roundup")

        published_at = ((story_data.get("storyDate") or {}).get("time")) or "2026-10-19T12:00:00+00:00"
        location = "world" if topic_name.lower() in {"world", "middle east", "russia"} else "us"

        stories.append(
            {
                "title": title,
                "article_url": f"{ALLSIDES_BASE_URL}{path}",
                "source": "AllSides Headline Roundup",
                "source_url": ALLSIDES_ROUNDUPS_URL,
                "source_bias": "center",
                "author": "AllSides Editorial Desk",
                "published_at": published_at,
                "categories": [topic_name],
                "tags": tags[:8],
                "location": location,
                "remote_image_url": story_image,
                "description": description,
                "content": content,
                "left_coverage": breakdown["left_coverage"],
                "center_coverage": breakdown["center_coverage"],
                "right_coverage": breakdown["right_coverage"],
                "dominant_bias": breakdown["dominant_bias"],
                "total_sources": breakdown["total_sources"],
                "perspectives": perspectives,
            }
        )

    return stories


def fetch_live_allsides_roundups(timeout=12):
    """Fetch live AllSides Headline Roundups or fall back to curated snapshot."""
    req = Request(
        ALLSIDES_ROUNDUPS_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw_html = resp.read().decode("utf-8", errors="ignore")
    stories = parse_allsides_html(raw_html)
    if not stories:
        raise ValueError("No headline roundups found in live AllSides response")
    return stories


def load_fallback_allsides_roundups():
    if ALLSIDES_FALLBACK_FILE.exists():
        return json.loads(ALLSIDES_FALLBACK_FILE.read_text(encoding="utf-8"))
    return []
