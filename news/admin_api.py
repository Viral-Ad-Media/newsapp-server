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
    PublishingRetry,
)
from .automation import config, queue_run, cancel_run, event, Busy
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
    catalogue_verification = serializers.SerializerMethodField()

    def get_catalogue_verification(self, obj):
        import json
        from .source_coverage import CATALOGUE_PATH
        if not hasattr(self, '_catalogue'):
            self._catalogue = {entry['feed_url']: entry['verification'] for entry in json.loads(CATALOGUE_PATH.read_text())['sources']}
        entry = self._catalogue.get(obj.feed_url)
        return {'checked_on': entry['checked_on'], 'readable_samples': sum(sample.get('words', 0) >= 400 for sample in entry['article_samples'])} if entry else None

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
            "catalogue_verification", "feed_quality",
            "description", "review_notes", "review_evidence_url", "reviewed_at", "review_status", "publisher_group",
        ]
        read_only_fields = ["last_attempt", "last_success", "last_error", "retrieved", "reviewed_at", "feed_quality"]

    def validate_location(self, value):
        from .regions import normalize_country
        location = normalize_country(value)
        if not location:
            raise serializers.ValidationError('Choose a supported country or World for a global feed.')
        return location

    def validate(self, data):
        feed = data.get("feed_url", getattr(self.instance, "feed_url", None))
        enabled = data.get("enabled", getattr(self.instance, "enabled", False))
        if enabled and not (feed or '').strip():
            from .source_feeds import catalogue_feed
            feed = catalogue_feed(data.get('name', getattr(self.instance, 'name', '')), data.get('url', getattr(self.instance, 'url', '')), data.get('location', getattr(self.instance, 'location', 'world')))
            if not feed:
                from .source_feeds import discover_feed
                feed = discover_feed(data.get('url', getattr(self.instance, 'url', '')))
            if feed:
                if NewsSource.objects.filter(feed_url=feed).exclude(pk=getattr(self.instance, 'pk', None)).exists():
                    raise serializers.ValidationError({'feed_url': 'This publisher feed already exists under another source. Enable that configured source instead.'})
                data['feed_url'] = feed
        if feed and (not self.instance or 'feed_url' in data or (enabled and not self.instance.enabled)):
            try:
                validate_feed_url(feed)
            except ValueError as exc:
                raise serializers.ValidationError({"feed_url": str(exc)})
        if enabled and not feed:
            raise serializers.ValidationError(
                {"feed_url": "No known RSS feed is configured for this source. Edit the source and add its publisher RSS/Atom URL before enabling."}
            )
        if data.get("review_status", getattr(self.instance, "review_status", "unreviewed")) == "reviewed":
            if not data.get("review_notes", getattr(self.instance, "review_notes", "")).strip() or not data.get("review_evidence_url", getattr(self.instance, "review_evidence_url", "")):
                raise serializers.ValidationError("A reviewed source needs public review notes and an evidence URL.")
            data["reviewed_at"] = timezone.now()
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
            "editorial_type", "why_matters", "context_source_url", "image_credit", "updated_at",
        ]
        read_only_fields = ["created_at", "imported", "updated_at"]
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
        context = data.get("why_matters", getattr(self.instance, "why_matters", ""))
        if context.strip() and not data.get("context_source_url", getattr(self.instance, "context_source_url", "")):
            raise serializers.ValidationError({"context_source_url": "Provide a supporting source for the editorial context."})
        if not self.instance and not data.get("content"):
            data["content"] = data.get("description", "")
        if not data.get("archived", getattr(self.instance, "archived", False)) and (not self.instance or "content" in data or data.get("archived") is False):
            from .article_writing import article_quality
            report = article_quality(data.get("content", getattr(self.instance, "content", "")))
            if not report["passed"]:
                raise serializers.ValidationError({"content": report["errors"]})
        return data


class RetryOutput(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name', read_only=True)
    title = serializers.SerializerMethodField()
    def get_title(self, obj):
        return str(obj.item.get('title') or ('Feed retrieval' if obj.kind == 'feed' else 'Unknown report'))[:255]
    class Meta:
        model = PublishingRetry
        fields = ['id','source','source_name','kind','title','status','attempts','next_attempt','reason_code','reason','updated_at']


class PublishingRuleInput(serializers.Serializer):
    source = serializers.IntegerField(min_value=0)
    category = serializers.IntegerField(min_value=1, allow_null=True)
    limit = serializers.IntegerField(min_value=1, max_value=100)


class SettingsInput(serializers.ModelSerializer):
    publishing_rules = PublishingRuleInput(many=True, required=False)

    def to_internal_value(self, data):
        if set(data) - {
            "enabled",
            "interval_minutes",
            "max_per_source",
            "publish_per_source",
            "publishing_rules",
            "timeout_seconds",
            "revision",
        }:
            raise serializers.ValidationError(
                {"detail": "Unsupported or read-only setting."}
            )
        return super().to_internal_value(data)

    interval_minutes = serializers.IntegerField(min_value=1, max_value=1000)
    max_per_source = serializers.IntegerField(min_value=1, max_value=100)
    publish_per_source = serializers.IntegerField(min_value=1, max_value=100, required=False)
    timeout_seconds = serializers.IntegerField(min_value=2, max_value=15)

    def validate_publishing_rules(self, rules):
        if len(rules) > 200:
            raise serializers.ValidationError('Use at most 200 publishing rules.')
        source_ids = {rule['source'] for rule in rules if rule['source'] != 0}
        if any(rule['source'] == 0 for rule in rules) and source_ids:
            raise serializers.ValidationError('Use All sources rules or individual source rules, not both.')
        category_ids = {rule['category'] for rule in rules if rule['category'] is not None}
        if NewsSource.objects.filter(pk__in=source_ids).count() != len(source_ids):
            raise serializers.ValidationError('A selected source no longer exists. Refresh the source list.')
        if NewsCategory.objects.filter(pk__in=category_ids).count() != len(category_ids):
            raise serializers.ValidationError('A selected category no longer exists. Refresh the category list.')
        seen, totals = set(), {}
        for rule in rules:
            pair = (rule['source'], rule['category'])
            if pair in seen:
                raise serializers.ValidationError('Each source/category combination can appear only once.')
            seen.add(pair)
            totals[rule['source']] = totals.get(rule['source'], 0) + rule['limit']
        if any(total > 100 for total in totals.values()):
            raise serializers.ValidationError('Combined publishing limits must not exceed 100 articles per source per interval.')
        if any((source, None) in seen and sum(pair[0] == source for pair in seen) > 1 for source in {rule['source'] for rule in rules}):
            raise serializers.ValidationError('Choose all categories or individual categories for a source, not both.')
        return rules

    def validate(self, data):
        rules = data.get('publishing_rules', getattr(self.instance, 'publishing_rules', []))
        totals = {}
        for rule in rules:
            totals[rule['source']] = totals.get(rule['source'], 0) + rule['limit']
        # A requested publish target must not be silently truncated by the scan limit.
        data['max_per_source'] = max(100 if any(rule['category'] is None for rule in rules) else 1, data.get('max_per_source', getattr(self.instance, 'max_per_source', 20)), data.get('publish_per_source', getattr(self.instance, 'publish_per_source', 2)), *totals.values())
        return data

    class Meta:
        model = AutomationSettings
        fields = [
            "enabled",
            "interval_minutes",
            "max_per_source",
            "publish_per_source",
            "publishing_rules",
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

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response

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
        if action:
            return Response({'detail': 'Not found'}, status=404)
        if resource == 'publishing':
            from .on_demand import publishing_readiness, run_progress
            if pk:
                return Response(run_progress(get_object_or_404(CrawlRun, pk=pk)))
            return Response(publishing_readiness())
        if resource == 'source-coverage':
            from .source_coverage import source_coverage
            return Response(source_coverage())
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
        if resource == 'retries':
            qs = PublishingRetry.objects.select_related('source').order_by('-updated_at','-pk')
            state = request.query_params.get('status')
            if state:
                qs = qs.filter(status=state)
            return page(self, request, qs, RetryOutput)
        if resource == "dashboard":
            from .publishing_retries import retry_summary, writing_health
            cfg = config()
            last = CrawlRun.objects.order_by("-id").first()
            active = CrawlRun.objects.filter(status__in=['queued', 'running']).order_by('id').first()
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
                    "retry_summary": retry_summary(),
                    "writing_service": writing_health(),
                    "last_run": RunOutput(last).data if last else None,
                    "active_run": RunOutput(active).data if active else None,
                    'can_cancel_pending_runs': True,
                    'scheduler_healthy': bool(cfg.worker_seen and cfg.worker_seen > timezone.now()-timedelta(minutes=5)),
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
        if resource == 'retries' and action == 'retry' and not pk:
            from .publishing_retries import requeue_failed
            return Response({'queued': requeue_failed(request.user)}, status=202)
        if resource == 'sources' and action in ('enable-all', 'disable-all') and not pk:
            enable = action == 'enable-all'
            with transaction.atomic():
                config()
                AutomationSettings.objects.select_for_update().get(pk=1)
                if enable:
                    from .source_feeds import catalogue_feed
                    for source in NewsSource.objects.select_for_update().filter(Q(feed_url__isnull=True) | Q(feed_url__regex=r'^\s*$')):
                        feed = catalogue_feed(source.name, source.url, source.location)
                        if feed and not NewsSource.objects.filter(feed_url=feed).exclude(pk=source.pk).exists():
                            source.feed_url = feed
                            source.save(update_fields=['feed_url'])
                rows = list(NewsSource.objects.select_for_update().exclude(feed_url__isnull=True).exclude(feed_url__regex=r'^\s*$').order_by('pk').values('id', 'enabled'))
                if not enable:
                    rows = list(NewsSource.objects.select_for_update().order_by('pk').values('id', 'enabled'))
                ids = [row['id'] for row in rows if row['enabled'] != enable]
                changed = NewsSource.objects.filter(pk__in=ids).update(enabled=enable)
                result = {'enabled': changed, 'already_enabled': len(rows) - len(ids), 'without_feed': NewsSource.objects.count() - len(rows)}
                if not enable:
                    result = {'disabled': changed, 'already_disabled': len(rows) - len(ids)}
                event('audit', 'Enabled all configured feed sources' if enable else 'Disabled all sources', actor=request.user, details={**result, 'source_ids': ids})
            return Response(result)
        if resource == 'publishing' and not pk and not action:
            from .on_demand import OnDemandInput, publishing_readiness, run_progress
            serial = OnDemandInput(data=request.data)
            serial.is_valid(raise_exception=True)
            if not serial.validated_data['dry_run'] and not publishing_readiness()['writing_configured']:
                return Response({'detail': 'Rewriting is not configured. Set OPENAI_API_KEY on the backend and worker, then redeploy/restart them. You can still test matching feeds without publishing.'}, status=503)
            try:
                run = queue_run(actor=request.user, dry_run=serial.validated_data['dry_run'], publishing_rules=serial.validated_data['rules'])
            except Busy as exc:
                active = CrawlRun.objects.filter(status__in=['queued', 'running']).first()
                return Response({'detail': str(exc), 'active_run_id': active.pk if active else None}, status=409)
            return Response(run_progress(run), status=202)
        if resource == 'runs' and pk and action == 'cancel':
            get_object_or_404(CrawlRun, pk=pk)
            try:
                run = cancel_run(pk, actor=request.user)
            except Busy as exc:
                return Response({'detail': str(exc)}, status=409)
            return Response(RunOutput(run).data)
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
                timing_changed = ('interval_minutes' in serial.validated_data and serial.validated_data['interval_minutes'] != cfg.interval_minutes) or (serial.validated_data.get('enabled') is True and not cfg.enabled)
                next_run = timezone.now() + timedelta(minutes=serial.validated_data.get('interval_minutes', cfg.interval_minutes)) if timing_changed else cfg.next_run
                serial.save(revision=cfg.revision + 1, next_run=next_run)
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
            extras = {"revision": obj.revision + 1, "updated_at": timezone.now()} if resource == "articles" else {}
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
            if resource == 'categories' and any(rule['category'] == pk for rule in config().publishing_rules):
                return Response({'detail': 'Remove this category from publishing rules before deleting it.'}, status=409)
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
