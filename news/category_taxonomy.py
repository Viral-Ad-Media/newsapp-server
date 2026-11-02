"""Map publisher topic labels to existing editorial categories without inventing topics."""

import re

CATEGORIES = {
    "Sports": [
        "sport",
        "football",
        "soccer",
        "cricket",
        "tennis",
        "rugby",
        "basketball",
        "athletics",
        "formula one",
    ],
    "Culture": [],
    "Arts": ["art", "art and design"],
    "Finance": ["financial", "personal finance", "money"],
    "Games": ["game", "gaming", "video games"],
    "Entertainment": ["film", "movies", "music", "television"],
    "Business": ["economy", "economics"],
    "Politics": ["political news"],
    "Technology": ["tech"],
    "Science": [],
    "Health": ["healthcare", "health care"],
    "Education": [],
    "Environment": ["climate", "climate change"],
    "Travel": ["tourism"],
    "Food": ["food and drink"],
    "Lifestyle": ["life and style"],
    "World": ["world news", "international"],
}


def canonical_category(value):
    label = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    for name, aliases in CATEGORIES.items():
        if label in [name.casefold(), *aliases]:
            return name
    return None


def item_categories(labels, fallback=None):
    from .models import NewsCategory

    existing = list(NewsCategory.objects.all())
    selected = []
    for label in labels or []:
        canonical = canonical_category(label)
        match = next(
            (
                category
                for category in existing
                if (
                    category.name.casefold() == str(label).strip().casefold()
                    or canonical
                    and (
                        canonical_category(category.name) == canonical
                        or canonical_category((category.slug or "").replace("-", " "))
                        == canonical
                    )
                )
            ),
            None,
        )
        if match and match.pk not in [category.pk for category in selected]:
            selected.append(match)
    return selected or ([fallback] if fallback else [])
