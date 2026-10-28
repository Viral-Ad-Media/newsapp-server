"""Shared category aliases keep website queries and outbound RSS consistent."""

import re

ALIASES = {
    "sports": ["sport", "sports"],
    "arts": ["art", "arts"],
    "games": ["game", "games", "gaming", "video games"],
    "finance": ["finance", "financial"],
}


def filter_category(queryset, category):
    category = str(category or "").strip()[:100]
    if not category:
        return queryset
    aliases = ALIASES.get(category.lower())
    if aliases:
        pattern = (
            r"(^|[^a-z0-9])("
            + "|".join(re.escape(term) for term in aliases)
            + r")([^a-z0-9]|$)"
        )
        return queryset.filter(categories__name__iregex=pattern).distinct()
    return queryset.filter(categories__name__icontains=category).distinct()
