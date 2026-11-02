"""Read-only check of one configured feed and bounded article evidence samples."""

import json
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from news.models import NewsSource
from news.automation import source_items, canonical_url
from news.article_writing import source_evidence
from news.seo import words


class Command(BaseCommand):
    help = "Check one source RSS and up to two article samples. No AI calls, database writes or publishing."

    def add_arguments(self, parser):
        parser.add_argument("source_id", type=int)

    def handle(self, *args, **options):
        try:
            source = NewsSource.objects.get(pk=options["source_id"])
        except NewsSource.DoesNotExist:
            raise CommandError("Source does not exist.") from None
        report = {
            "source_id": source.pk,
            "checked_at": timezone.now().isoformat(),
            "samples": [],
        }
        try:
            items = source_items(source, 10)
        except Exception:
            raise CommandError(
                "Feed could not be retrieved and parsed under the current security policy."
            ) from None
        report["feed_entries"] = len(items)
        for item in [item for item in items if item.get("title")][:2]:
            sample = {"title": item["title"]}
            try:
                evidence = item.get("source_text") or item.get("content") or ""
                if len(words(evidence)) < 400:
                    evidence = source_evidence(
                        canonical_url(item["article_url"]), source.url
                    )
                sample["evidence_words"] = len(words(evidence))
                sample["evidence_ready"] = True
            except ValueError as exc:
                # source_evidence exposes only controlled, non-credential-bearing errors.
                sample["evidence_ready"] = False
                sample["error"] = str(exc)[:300]
            report["samples"].append(sample)
        self.stdout.write(json.dumps(report, indent=2))
