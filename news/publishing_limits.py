"""Per-source interval quotas, fenced by the worker's existing singleton lock."""

from datetime import timedelta
from .models import SourcePublishingWindow


def source_limits(config, source_id):
    rules = [rule for rule in config.publishing_rules if rule["source"] == source_id]
    if not rules:
        return {"*": config.publish_per_source}
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
        window.counts.get("*", 0) < 100
        and bool(keys)
        and all(window.counts.get(key, 0) < limits[key] for key in keys)
    )


def source_has_capacity(window, limits):
    return window.counts.get("*", 0) < 100 and any(
        window.counts.get(key, 0) < limit for key, limit in limits.items()
    )


def record_publication(window, categories):
    for key in {"*", *(str(category.pk) for category in categories)}:
        window.counts[key] = window.counts.get(key, 0) + 1
