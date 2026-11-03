"""Per-source interval quotas, fenced by the worker's existing singleton lock."""

from datetime import timedelta
from .models import SourcePublishingWindow, NewsCategory


def source_limits(config, source_id, category_ids=None):
    rules = [rule for rule in config.publishing_rules if rule["source"] in (0, source_id)]
    if not rules:
        return {"*": config.publish_per_source}
    all_categories = next((rule for rule in rules if rule['category'] is None), None)
    if all_categories is not None:
        # A saved All categories rule is a per-category quota, resolved afresh
        # each batch so newly added categories are included automatically.
        if category_ids is None:
            category_ids = NewsCategory.objects.values_list('pk', flat=True)
        return {str(pk): all_categories['limit'] for pk in category_ids}
    return {
        str(rule["category"]) if rule["category"] is not None else "*": rule["limit"]
        for rule in rules
    }


def publishing_window(source, cycle_start, interval):
    window = SourcePublishingWindow.objects.filter(source=source).first()
    if window is None:
        window = SourcePublishingWindow(source=source, started_at=cycle_start)
    elif cycle_start >= window.started_at + timedelta(minutes=interval):
        window.started_at, window.counts, window.attempted = cycle_start, {}, []
    return window


def matching_limits(categories, limits):
    if "*" in limits:
        return ["*"]
    return [str(category.pk) for category in categories if str(category.pk) in limits]


def has_capacity(window, limits, keys=None):
    keys = list(limits) if keys is None else keys
    return (
        window.counts.get("*", 0) < sum(limits.values())
        and bool(keys)
        and all(window.counts.get(key, 0) < limits[key] for key in keys)
    )


def source_has_capacity(window, limits):
    return window.counts.get("*", 0) < sum(limits.values()) and any(
        window.counts.get(key, 0) < limit for key, limit in limits.items()
    )


def record_publication(window, categories):
    for key in {"*", *(str(category.pk) for category in categories)}:
        window.counts[key] = window.counts.get(key, 0) + 1
