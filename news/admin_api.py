"""Private operations. Existing staff role is the administrative boundary."""

from datetime import timedelta
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from .views import IsSuperAdmin
from .models import (
    NewsArticle,
    NewsCategory,
    NewsSource,
    AutomationSettings,
    CrawlRun,
    AdminEvent,
)
from .automation import config, queue_run, event, Busy
from .pagination import NewsPagination
from .safe_fetch import validate_feed_url


class CategoryInput(serializers.ModelSerializer):
    article_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = NewsCategory
        fields = ["id", "name", "slug", "description", "article_count"]

    def validate(self, data):
        name = data.get("name", getattr(self.instance, "name", "")).strip()
        slug = data.get("slug") or slugify(name)
        others = NewsCategory.objects.exclude(pk=getattr(self.instance, "pk", None))
        if not name or not slug or slug != slugify(slug) or len(slug) > 120:
            raise serializers.ValidationError(
                "A valid name and lowercase hyphenated slug are required."
            )
        if others.filter(Q(name__iexact=name) | Q(slug__iexact=slug)).exists():
            raise serializers.ValidationError("Category name or slug already exists.")
        data["name"], data["slug"] = name, slug
        return data


class SourceInput(serializers.ModelSerializer):
    article_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = NewsSource
        fields = [
            "id",
            "name",
            "url",
            "feed_url",
            "enabled",
            "location",
            "default_category",
            "last_attempt",
            "last_success",
            "last_error",
            "retrieved",
            "article_count",
        ]
        read_only_fields = ["last_attempt", "last_success", "last_error", "retrieved"]

    def validate(self, data):
        feed = data.get("feed_url", getattr(self.instance, "feed_url", None))
        enabled = data.get("enabled", getattr(self.instance, "enabled", False))
        if feed:
            try:
                validate_feed_url(feed)
            except ValueError as exc:
                raise serializers.ValidationError({"feed_url": str(exc)})
        if enabled and not feed:
            raise serializers.ValidationError(
                "An RSS/Atom feed URL is required before enabling."
            )
        if (
            enabled
            and NewsSource.objects.filter(enabled=True)
            .exclude(pk=getattr(self.instance, "pk", None))
            .count()
            >= 20
        ):
            raise serializers.ValidationError("At most 20 feeds may be enabled.")
        return data


class ArticleInput(serializers.ModelSerializer):
    quality = serializers.SerializerMethodField()
    source_name = serializers.CharField(source="source.name", read_only=True)
    category_names = serializers.SerializerMethodField()
    duplicate_ids = serializers.SerializerMethodField()

    class Meta:
        model = NewsArticle
        fields = [
            "id",
            "title",
            "description",
            "content",
            "author",
            "source",
            "source_name",
            "categories",
            "category_names",
            "duplicate_ids",
            "quality",
            "article_url",
            "published_at",
            "created_at",
            "location",
            "archived",
            "imported",
            "revision",
            "focus_keyword",
            "seo_title",
            "seo_description",
            "image_alt",
        ]
        read_only_fields = ["created_at", "imported"]
        extra_kwargs = {
            "revision": {"required": False},
            "content": {"required": False, "allow_blank": True},
            "categories": {"required": False, "allow_empty": True},
            "published_at": {"required": False},
        }

    def get_quality(self, obj):
        from .article_writing import article_quality
        return article_quality(obj.content)

    def get_duplicate_ids(self, obj):
        query = Q(title__iexact=obj.title, source_id=obj.source_id)
        if obj.article_url:
            query |= Q(article_url=obj.article_url)
        return list(
            NewsArticle.all_objects.filter(query)
            .exclude(pk=obj.pk)
            .values_list("id", flat=True)[:20]
        )

    def get_category_names(self, obj):
        return list(obj.categories.values_list("name", flat=True))

    def validate(self, data):
        if self.instance and self.instance.imported:
            forbidden = set(data) & {
                "title",
                "description",
                "author",
                "source",
                "article_url",
                "published_at",
            }
            if forbidden:
                raise serializers.ValidationError(
                    "Imported source fields are read-only; edit the derived article body, category, location or SEO metadata."
                )
        for field in ["title", "description"]:
            if field in data and not data[field].strip():
                raise serializers.ValidationError(
                    {field: "This field cannot be blank."}
                )
        if not self.instance and not data.get("content"):
            data["content"] = data.get("description", "")
        if not data.get("archived", getattr(self.instance, "archived", False)) and (not self.instance or "content" in data or data.get("archived") is False):
            from .article_writing import article_quality
            report = article_quality(data.get("content", getattr(self.instance, "content", "")))
            if not report["passed"]:
                raise serializers.ValidationError({"content": report["errors"]})
        return data


class SettingsInput(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if set(data) - {
            "enabled",
            "interval_minutes",
            "max_per_source",
            "timeout_seconds",
            "revision",
        }:
            raise serializers.ValidationError(
                {"detail": "Unsupported or read-only setting."}
            )
        return super().to_internal_value(data)

    interval_minutes = serializers.IntegerField(min_value=60, max_value=1440)
    max_per_source = serializers.IntegerField(min_value=1, max_value=100)
    timeout_seconds = serializers.IntegerField(min_value=2, max_value=15)

    class Meta:
        model = AutomationSettings
        fields = [
            "enabled",
            "interval_minutes",
            "max_per_source",
            "timeout_seconds",
            "revision",
            "next_run",
            "worker_seen",
        ]
        read_only_fields = ["next_run", "worker_seen"]


class RunOutput(serializers.ModelSerializer):
    class Meta:
        model = CrawlRun
        fields = "__all__"


class EventOutput(serializers.ModelSerializer):
    class Meta:
        model = AdminEvent
        fields = "__all__"


def page(view, request, queryset, serializer):
    pager = NewsPagination()
    rows = pager.paginate_queryset(queryset, request, view=view)
    return pager.get_paginated_response(serializer(rows, many=True).data)


def filtered(qs, request, search_fields, date_field):
    search = request.query_params.get("search", "").strip()[:200]
    if search:
        query = Q()
        for field in search_fields:
            query |= Q(**{field + "__icontains": search})
        qs = qs.filter(query)
    for key, suffix in [("after", "__date__gte"), ("before", "__date__lte")]:
        value = request.query_params.get(key)
        if value:
            date = serializers.DateField().run_validation(value)
            qs = qs.filter(**{date_field + suffix: date})
    return qs


class AdminAPI(APIView):
    permission_classes = [IsSuperAdmin]

    def handle_exception(self, exc):
        from .article_writing import ArticlePreparationError
        if isinstance(exc, ArticlePreparationError):
            return Response({'detail': str(exc)}, status=400)
        if isinstance(exc, IntegrityError):
            return Response(
                {"detail": "Conflicting value or operation; refresh and try again."},
                status=409,
            )
        return super().handle_exception(exc)

    def get(self, request, resource="dashboard", pk=None, action=None):
        if resource == "preparations":
            from .models import ArticlePreparation
            from .utils.ai_summarizer import client
            jobs = ArticlePreparation.objects.order_by("-id")[:100]
            return Response({"configured": client is not None, "jobs": [{"id": j.pk, "article": j.article_id, "status": j.status, "error": j.error, "created_at": j.created_at, "completed_at": j.completed_at, "word_count": j.result.get("quality", {}).get("word_count"), "seo_score": j.result.get("seo", {}).get("score")} for j in jobs]})

        if resource == "me":
            return Response(
                {
                    "username": request.user.get_username(),
                    "can_delete": request.user.is_superuser,
                }
            )
        if resource == "dashboard":
            cfg = config()
            last = CrawlRun.objects.order_by("-id").first()
            return Response(
                {
                    "stats": {
                        "total_articles": NewsArticle.all_objects.count(),
                        "published_articles": NewsArticle.objects.count(),
                        "archived_articles": NewsArticle.all_objects.filter(
                            archived=True
                        ).count(),
                        "fetched_24h": NewsArticle.all_objects.filter(
                            imported=True,
                            created_at__gte=timezone.now() - timedelta(hours=24),
                        ).count(),
                        "failed_runs": CrawlRun.objects.filter(
                            status__in=["failed", "partial"]
                        ).count(),
                        "active_sources": NewsSource.objects.filter(enabled=True)
                        .exclude(feed_url__isnull=True)
                        .exclude(feed_url="")
                        .count(),
                    },
                    "settings": SettingsInput(cfg).data,
                    "last_run": RunOutput(last).data if last else None,
                    "active_run": (
                        RunOutput(
                            CrawlRun.objects.filter(
                                status__in=["queued", "running"]
                            ).first()
                        ).data
                        if CrawlRun.objects.filter(
                            status__in=["queued", "running"]
                        ).exists()
                        else None
                    ),
                    "recent_articles": ArticleInput(
                        NewsArticle.all_objects.order_by("-created_at", "-id")[:10],
                        many=True,
                    ).data,
                    "events": EventOutput(
                        AdminEvent.objects.order_by("-id")[:10], many=True
                    ).data,
                }
            )
        if resource == "settings":
            cfg = config()
            return Response(
                {
                    **SettingsInput(cfg).data,
                    "environment": "development" if settings.DEBUG else "production",
                    "database": "connected",
                    "scheduler_healthy": bool(
                        cfg.worker_seen
                        and cfg.worker_seen > timezone.now() - timedelta(minutes=5)
                    ),
                    "publishing_policy": "New publication requires a substantive full article. RSS/provider imports require source evidence, editorial SEO checks and source-support review. Missing evidence or writing configuration blocks publication.",
                    "retention_days": 90,
                    "concurrency": 1,
                }
            )
        if resource == "articles":
            qs = NewsArticle.all_objects.select_related("source").prefetch_related(
                "categories"
            )
            if pk:
                return Response(ArticleInput(get_object_or_404(qs, pk=pk)).data)
            qs = filtered(qs, request, ["title", "description"], "published_at")
            for key, field in [("source", "source_id"), ("category", "categories__id")]:
                if request.query_params.get(key):
                    value = serializers.IntegerField(min_value=1).run_validation(
                        request.query_params[key]
                    )
                    qs = qs.filter(**{field: value})
            state = request.query_params.get("status")
            if state in ["published", "archived"]:
                qs = qs.filter(archived=state == "archived")
            sort = request.query_params.get("sort", "-created_at")
            if sort not in [
                "title",
                "-title",
                "created_at",
                "-created_at",
                "published_at",
                "-published_at",
            ]:
                raise serializers.ValidationError("Invalid sort field.")
            return page(
                self, request, qs.order_by(sort, "-id").distinct(), ArticleInput
            )
        if resource in ["categories", "sources"]:
            model, serial, relation = (
                (NewsCategory, CategoryInput, "articles")
                if resource == "categories"
                else (NewsSource, SourceInput, "newsarticle")
            )
            qs = model.objects.annotate(article_count=Count(relation)).order_by(
                "name", "id"
            )
            if pk:
                return Response(serial(get_object_or_404(qs, pk=pk)).data)
            if request.query_params.get("search"):
                qs = qs.filter(name__icontains=request.query_params["search"][:200])
            if resource == "sources" and request.query_params.get("enabled") in [
                "true",
                "false",
            ]:
                qs = qs.filter(enabled=request.query_params["enabled"] == "true")
            return page(self, request, qs, serial)
        if resource == "runs":
            if pk:
                return Response(RunOutput(get_object_or_404(CrawlRun, pk=pk)).data)
            qs = filtered(
                CrawlRun.objects.all(), request, ["error", "status"], "created_at"
            )
            if request.query_params.get("run"):
                qs = qs.filter(
                    pk=serializers.IntegerField(min_value=1).run_validation(
                        request.query_params["run"]
                    )
                )
            for key in ["status", "source"]:
                value = request.query_params.get(key)
                if value:
                    if key == "source":
                        value = serializers.IntegerField(min_value=1).run_validation(
                            value
                        )
                    qs = qs.filter(**{key: value})
            return page(self, request, qs.order_by("-id"), RunOutput)
        if resource == "logs":
            qs = filtered(
                AdminEvent.objects.all(), request, ["message", "kind"], "timestamp"
            )
            for field in ["kind", "severity", "status", "source", "run"]:
                value = request.query_params.get(field)
                if value:
                    if field in ["source", "run"]:
                        value = serializers.IntegerField(min_value=1).run_validation(
                            value
                        )
                    qs = qs.filter(**{field: value})
            return page(self, request, qs.order_by("-id"), EventOutput)
        return Response({"detail": "Not found"}, status=404)

    def post(self, request, resource, pk=None, action=None):
        if (resource == "articles" and pk and action == "prepare") or resource == "preparations":
            from .article_jobs import queue_article
            from .article_writing import article_quality
            targets = NewsArticle.objects.filter(pk=pk) if pk else NewsArticle.objects.order_by("pk")
            if pk:
                get_object_or_404(targets)
            evidence = str(request.data.get("evidence") or "") if pk else ""
            if len(evidence) > 60000:
                raise serializers.ValidationError("Evidence is limited to 60,000 characters.")
            jobs = []
            for article in targets[:200]:
                if pk or not article_quality(article.content)["passed"]:
                    job = queue_article(article.pk, evidence, request.data.get("references") if pk else None)
                    jobs.append({"id": job.pk, "article": article.pk, "status": job.status})
            event("audit", "Full article preparation queued", actor=request.user, details={"count": len(jobs)})
            return Response({"jobs": jobs, "detail": "Queued for the background worker. Existing summaries remain unchanged."}, status=202)
        if resource == "runs":
            dry_run = serializers.BooleanField().run_validation(
                request.data.get("dry_run", False)
            )
            source = get_object_or_404(NewsSource, pk=pk, enabled=True) if pk else None
            try:
                run = queue_run(source=source, actor=request.user, dry_run=dry_run)
            except Busy as exc:
                return Response({"detail": str(exc)}, status=409)
            return Response(RunOutput(run).data, status=202)
        if resource == "sources" and pk and action == "test":
            # Tests use the same durable worker; they do not publish.
            source = get_object_or_404(NewsSource, pk=pk)
            if not source.feed_url:
                raise serializers.ValidationError("Configure a feed first.")
            try:
                run = queue_run(
                    trigger="test", source=source, actor=request.user, dry_run=True
                )
            except Busy as exc:
                return Response({"detail": str(exc)}, status=409)
            return Response(RunOutput(run).data, status=202)
        if pk or resource not in ["articles", "categories", "sources"]:
            return Response({"detail": "Not found"}, status=404)
        serial = {
            "articles": ArticleInput,
            "categories": CategoryInput,
            "sources": SourceInput,
        }[resource]
        with transaction.atomic():
            config()
            AutomationSettings.objects.select_for_update().get(pk=1)
            data = dict(request.data)
            data.pop("revision", None)
            obj = serial(data=data)
            obj.is_valid(raise_exception=True)
            obj.save()
            event("audit", f"Created {resource} #{obj.instance.pk}", actor=request.user)
        return Response(obj.data, status=201)

    def put(self, request, resource, pk=None, action=None):
        config()
        with transaction.atomic():
            cfg = AutomationSettings.objects.select_for_update().get(pk=1)
            if resource == "settings":
                if request.data.get("revision") != cfg.revision:
                    return Response(
                        {"detail": "Settings changed. Refresh before saving."},
                        status=409,
                    )
                serial = SettingsInput(cfg, data=request.data, partial=True)
                serial.is_valid(raise_exception=True)
                serial.save(
                    revision=cfg.revision + 1,
                    next_run=timezone.now()
                    + timedelta(
                        minutes=serial.validated_data.get(
                            "interval_minutes", cfg.interval_minutes
                        )
                    ),
                )
                event("audit", "Automation settings updated", actor=request.user)
                return Response(serial.data)
            if resource not in ["articles", "categories", "sources"] or not pk:
                return Response({"detail": "Not found"}, status=404)
            model, cls = {
                "articles": (NewsArticle.all_objects, ArticleInput),
                "categories": (NewsCategory.objects, CategoryInput),
                "sources": (NewsSource.objects, SourceInput),
            }[resource]
            obj = get_object_or_404(model.select_for_update(), pk=pk)
            data = dict(request.data)
            if resource == "articles":
                if data.pop("revision", None) != obj.revision:
                    return Response(
                        {"detail": "Article changed. Refresh before saving."},
                        status=409,
                    )
            serial = cls(obj, data=data, partial=True)
            serial.is_valid(raise_exception=True)
            extras = {"revision": obj.revision + 1} if resource == "articles" else {}
            if (
                resource == "sources"
                and "feed_url" in data
                and data["feed_url"] != obj.feed_url
            ):
                extras.update(
                    last_success=None, last_error="", last_attempt=None, retrieved=0
                )
            serial.save(**extras)
            event("audit", f"Updated {resource} #{pk}", actor=request.user)
            return Response(serial.data)

    def delete(self, request, resource, pk=None, action=None):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Only a superuser may permanently delete records."},
                status=403,
            )
        if resource not in ["articles", "categories"] or not pk:
            return Response({"detail": "Not found"}, status=404)
        with transaction.atomic():
            config()
            AutomationSettings.objects.select_for_update().get(pk=1)
            model = (
                NewsArticle.all_objects
                if resource == "articles"
                else NewsCategory.objects
            )
            obj = get_object_or_404(model.select_for_update(), pk=pk)
            if (
                resource == "categories"
                and NewsArticle.all_objects.filter(categories=obj).exists()
            ):
                return Response(
                    {
                        "detail": "Reassign all associated articles before deleting this category."
                    },
                    status=409,
                )
            obj.delete()
            event(
                "audit",
                f"Deleted {resource} #{pk}",
                actor=request.user,
                severity="warning",
            )
        return Response(status=204)
