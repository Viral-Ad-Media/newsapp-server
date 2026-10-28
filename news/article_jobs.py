"""Upgrades existing stories using the same singleton worker as RSS imports."""

from django.db import transaction
from django.utils import timezone
from .models import ArticlePreparation, NewsArticle, AutomationWorkerLock
from .article_writing import prepare_article, source_evidence, ArticlePreparationError
from .automation import event

BACKUP_FIELDS = (
    "title",
    "description",
    "content",
    "focus_keyword",
    "seo_title",
    "seo_description",
    "og_title",
    "og_description",
    "image_alt",
    "revision",
    "author",
    "article_url",
    "source_id",
    "published_at",
)


def snapshot(article):
    values = {key: getattr(article, key) for key in BACKUP_FIELDS}
    values["published_at"] = article.published_at.isoformat()
    return values


def queue_article(article_id, evidence="", references=None):
    if len(evidence) > 60000:
        raise ArticlePreparationError(
            "Source evidence cannot exceed 60,000 characters."
        )
    with transaction.atomic():
        article = NewsArticle.all_objects.select_for_update().get(pk=article_id)
        from .article_writing import validate_references

        references = validate_references(references, article.article_url)
        if references and not evidence.strip():
            raise ArticlePreparationError(
                "Provide verified text for the supporting sources."
            )
        active = ArticlePreparation.objects.filter(
            article=article, status="queued"
        ).first()
        if active:
            return active
        return ArticlePreparation.objects.create(
            article=article,
            expected_revision=article.revision,
            original=snapshot(article),
            evidence=evidence,
            result={"input_references": references},
        )


def tick_article():
    # The same lock fences crawler, provider CLI imports and article upgrades.
    AutomationWorkerLock.objects.get_or_create(pk=1)
    with transaction.atomic():
        AutomationWorkerLock.objects.select_for_update().get(pk=1)
        job = ArticlePreparation.objects.filter(status="queued").order_by("id").first()
        if not job:
            return None
        article = NewsArticle.all_objects.select_for_update().get(pk=job.article_id)
        try:
            if article.archived:
                raise ArticlePreparationError(
                    "Archived article was preserved; restore it explicitly before preparing."
                )
            if snapshot(article) != job.original:
                raise ArticlePreparationError(
                    "Article changed after queueing; queue again using the latest revision."
                )
            from .utils.ai_summarizer import client

            if client is None:
                raise ArticlePreparationError(
                    "OPENAI_API_KEY is not configured. Existing article and summary were preserved."
                )
            evidence = job.evidence or source_evidence(
                article.article_url, article.source.url
            )
            result = prepare_article(
                title=article.title,
                summary=article.description,
                evidence=evidence,
                source_name=article.source.name,
                source_url=article.article_url,
                references=job.result.get("input_references", []),
            )
            from .publishing import metadata_defaults

            old_defaults = metadata_defaults(article)
            with transaction.atomic():
                article.content = result["content"]
                for key in ("focus_keyword", "seo_title", "seo_description"):
                    # Retain deliberate editor metadata; generated fields fill missing/automatic previews.
                    if not getattr(article, key) or getattr(
                        article, key
                    ) == old_defaults.get(key):
                        setattr(article, key, result[key])
                article.revision += 1
                article.save()
                article.tags.add("ai-assisted-source-article")
                job.result = result
                job.status = "completed"
                event(
                    "publishing",
                    "Full article upgraded from source evidence",
                    article=article,
                    status="published",
                    details={
                        "preparation": job.pk,
                        "quality": result["quality"],
                        "seo_score": result["seo"]["score"],
                    },
                )
        except ArticlePreparationError as exc:
            job.status, job.error = "blocked", str(exc)[:500]
            job.result = exc.details
            event(
                "publishing",
                "Full article preparation blocked",
                article=article,
                status="blocked",
                severity="warning",
                details={"preparation": job.pk, "reason": job.error},
            )
        except Exception:
            job.status, job.error = (
                "blocked",
                "Article preparation failed; existing content was preserved.",
            )
        job.completed_at = timezone.now()
        # Full evidence is transient. Retain the prior article backup and generated report.
        job.evidence = ""
        job.save()
        return job
