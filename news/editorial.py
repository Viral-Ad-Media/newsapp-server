from .public_cache import cached_public, invalidate_public
from .source_feeds import source_group_ids
"""Editorial curation and reader controls. No generated ratings or event matches."""
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.db.models import Count, Prefetch, F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import (NewsArticle, NewsSource, NewsCategory, ReaderProfile, DevelopingStory,
                     StoryUpdate, ArticleCorrection, ArticleReport, HomepagePlacement,
                     HomepageSection, ReadingHistory)
from .regions import filter_country, normalize_country
from .serializers import NewsArticleCardSerializer, NewsSourceSerializer
from .views import IsSuperAdmin


def reports():
    return NewsArticle.objects.select_related("source", "story").prefetch_related("categories", "tags", "corrections")


def publisher_count(rows):
    # Union both relationships: a shared host OR a declared group connects rows.
    groups = []
    for article in rows:
        source = article.source
        keys = {"host:" + (urlsplit(source.url).hostname or str(source.pk)).lower().removeprefix("www.")}
        if source.publisher_group.strip():
            keys.add("group:" + source.publisher_group.strip().casefold())
        connected = [group for group in groups if group & keys]
        groups = [group for group in groups if not group & keys]
        groups.append(keys.union(*connected))
    return len(groups)


def story_payload(story, detail=False):
    rows = getattr(story, "public_reports", None)
    if rows is None:
        rows = list(reports().filter(story=story).order_by("-published_at", "-id"))
    rows = [a for a in rows if filter_location(a.location, normalize_country(story.location))]
    payload = {"id": story.pk, "title": story.title, "location": story.location,
               "overview": story.overview, "updated_at": story.updated_at,
               "report_count": len(rows), "publisher_count": publisher_count(rows)}
    if detail:
        payload.update(uncertainty=story.uncertainty, agreements=story.agreements,
                       differences=story.differences, articles=NewsArticleCardSerializer(rows, many=True).data,
                       updates=[{"id": u.pk, "summary": u.summary, "occurred_at": u.occurred_at,
                                 "article": u.article_id, "title": u.article.title, "source": u.article.source.name}
                                for u in story.updates.select_related("article__source").filter(article_id__in=[a.pk for a in rows], article__story=story)])
    return payload


class EditionView(APIView):
    @cached_public
    def get(self, request):
        location = normalize_country(request.query_params.get("location"))
        if not location:
            raise serializers.ValidationError("Choose a supported country edition.")
        now = timezone.now()
        qs = filter_country(reports(), location).order_by("-published_at", "-id")
        # Each placement is rechecked at read time: moves and archives cannot leak
        # stories into the wrong edition, even after an editor saved a placement.
        placements = HomepagePlacement.objects.filter(location=location, starts_at__lte=now, ends_at__gt=now, article__in=qs).order_by("rank", "id")
        selected, slots = set(), {"lead": [], "supporting": [], "feature": []}
        limits = {"lead": 1, "supporting": 3, "feature": 2}
        for placement in placements:
            if placement.article_id not in selected and len(slots[placement.slot]) < limits[placement.slot]:
                slots[placement.slot].append(placement.article_id)
                selected.add(placement.article_id)
        latest = list(qs[:30])
        for slot in slots:
            for article in latest:
                if len(slots[slot]) >= limits[slot]:
                    break
                if article.pk not in selected:
                    slots[slot].append(article.pk)
                    selected.add(article.pk)
        by_id = {a.pk: a for a in qs.filter(pk__in=selected)}
        configured_sections = HomepageSection.objects.filter(location=location)
        sections = list(configured_sections.filter(enabled=True).select_related("category").order_by("rank", "id")[:12])
        if not configured_sections.exists():
            # Useful first-run sections use saved taxonomy and actual coverage.
            categories = NewsCategory.objects.filter(articles__in=qs).annotate(n=Count("articles")).order_by("-n", "name")[:4]
            sections = [HomepageSection(title=c.name, category=c) for c in categories]
        return Response({"location": location, **{slot: NewsArticleCardSerializer([by_id[i] for i in ids], many=True).data for slot, ids in slots.items()},
                         "latest": NewsArticleCardSerializer(latest[:6], many=True).data,
                         "sections": [{"title": s.title, "category": s.category_id, "category_name": s.category.name,
                                       "articles": NewsArticleCardSerializer(qs.filter(categories=s.category).distinct()[:3], many=True).data} for s in sections]}, headers={"Cache-Control": "no-store"})


class StoriesView(APIView):
    def get(self, request, pk=None):
        qs = DevelopingStory.objects.filter(published=True, reports__archived=False).distinct()
        location = request.query_params.get("location")
        if location:
            if not normalize_country(location):
                raise serializers.ValidationError("Unsupported edition.")
            qs = filter_country(qs, location)
        if pk:
            payload = story_payload(get_object_or_404(qs, pk=pk), detail=True)
            if not payload["report_count"]:
                from django.http import Http404
                raise Http404
            return Response(payload)
        qs = qs.prefetch_related(Prefetch("reports", queryset=reports().order_by("-published_at", "-id"), to_attr="public_reports"))
        payloads = [story_payload(s) for s in qs.order_by("-updated_at", "-id")[:60]]
        return Response({"results": [p for p in payloads if p["report_count"]]})


class SourcesView(APIView):
    def get(self, request, pk=None):
        qs = NewsSource.objects.select_related("feed_owner").order_by("name", "id")
        if pk:
            source = get_object_or_404(qs, pk=pk)
            articles = filter_country(reports().filter(source_id__in=source_group_ids([source.pk])), request.query_params.get("location")).order_by("-published_at", "-id")
            return Response({"source": NewsSourceSerializer(source).data, "articles": NewsArticleCardSerializer(articles[:24], many=True).data})
        qs = qs.filter(feed_owner__isnull=True)
        # Public publisher list contains neither feed URLs nor private diagnostics.
        location = request.query_params.get("location")
        if location:
            qs = qs.filter(newsarticle__in=filter_country(NewsArticle.objects.all(), location)).distinct()
        return Response({"results": NewsSourceSerializer(qs[:300], many=True).data})


def preference_payload(profile):
    return {"categories": list(profile.categories.values_list("id", flat=True)),
            "sources": sorted(source_group_ids(profile.followed_sources.values_list("id", flat=True))),
            "muted_sources": sorted(source_group_ids(profile.muted_sources.values_list("id", flat=True))),
            "stories": list(profile.followed_stories.filter(published=True).values_list("id", flat=True))}


class ReaderChoices(APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    def get(self, request):
        profile, _ = ReaderProfile.objects.get_or_create(user=request.user)
        return Response(preference_payload(profile))

    def put(self, request):
        class Choices(serializers.Serializer):
            categories = serializers.PrimaryKeyRelatedField(many=True, queryset=NewsCategory.objects.all(), required=False)
            sources = serializers.PrimaryKeyRelatedField(many=True, queryset=NewsSource.objects.all(), required=False)
            muted_sources = serializers.PrimaryKeyRelatedField(many=True, queryset=NewsSource.objects.all(), required=False)
            stories = serializers.PrimaryKeyRelatedField(many=True, queryset=DevelopingStory.objects.filter(published=True), required=False)
        serial = Choices(data=request.data)
        serial.is_valid(raise_exception=True)
        mapping = {"categories": "categories", "sources": "followed_sources", "muted_sources": "muted_sources", "stories": "followed_stories"}
        with transaction.atomic():
            ReaderProfile.objects.get_or_create(user=request.user)
            profile = ReaderProfile.objects.select_for_update().get(user=request.user)
            for key, values in serial.validated_data.items():
                if len(values) > 300:
                    raise serializers.ValidationError("Choose at most 300 items per preference.")
                values = source_group_ids([v.pk for v in values]) if key in ('sources','muted_sources') else values
                getattr(profile, mapping[key]).set(values)
            # Muting always wins over following.
            profile.followed_sources.remove(*profile.muted_sources.all())
        return Response(preference_payload(profile))


class ReaderChoiceDetail(ReaderChoices):
    """Atomic per-item mutations avoid overwriting choices in another browser tab."""
    def put(self, request, kind, pk):
        models = {"sources": NewsSource, "muted_sources": NewsSource, "stories": DevelopingStory}
        model = models.get(kind)
        if model is None:
            return Response(status=404)
        item = get_object_or_404(model, pk=pk, **({"published": True} if kind == "stories" else {}))
        with transaction.atomic():
            ReaderProfile.objects.get_or_create(user=request.user)
            profile = ReaderProfile.objects.select_for_update().get(user=request.user)
            group = source_group_ids([item.pk]) if kind in ('sources','muted_sources') else [item.pk]
            getattr(profile, {"sources": "followed_sources", "muted_sources": "muted_sources", "stories": "followed_stories"}[kind]).add(*group)
            if kind == "sources":
                profile.muted_sources.remove(*group)
            if kind == "muted_sources":
                profile.followed_sources.remove(*group)
        return Response(preference_payload(profile))

    def delete(self, request, kind, pk):
        relation = {"sources": "followed_sources", "muted_sources": "muted_sources", "stories": "followed_stories"}.get(kind)
        if not relation:
            return Response(status=404)
        with transaction.atomic():
            ReaderProfile.objects.get_or_create(user=request.user)
            profile = ReaderProfile.objects.select_for_update().get(user=request.user)
            group = source_group_ids([pk]) if kind in ('sources','muted_sources') else [pk]
            getattr(profile, relation).remove(*group)
        return Response(preference_payload(profile))


class HistoryView(ReaderChoices):
    def get(self, request, pk=None):
        ids = ReadingHistory.objects.filter(user=request.user).order_by("-viewed_at").values_list("article_id", flat=True)[:100]
        by_id = {a.pk: a for a in filter_country(reports().filter(pk__in=ids), request.query_params.get("location"))}
        return Response({"results": NewsArticleCardSerializer([by_id[i] for i in ids if i in by_id], many=True).data})

    def put(self, request, pk):
        article = get_object_or_404(NewsArticle, pk=pk)
        with transaction.atomic():
            # Serialize per-reader pruning as well as concurrent repeat views.
            ReaderProfile.objects.get_or_create(user=request.user)
            ReaderProfile.objects.select_for_update().get(user=request.user)
            ReadingHistory.objects.update_or_create(user=request.user, article=article, defaults={"viewed_at": timezone.now()})
            keep = list(ReadingHistory.objects.filter(user=request.user).order_by("-viewed_at", "-id").values_list("pk", flat=True)[:100])
            ReadingHistory.objects.filter(user=request.user).exclude(pk__in=keep).delete()
        return Response({"recorded": True})

    def delete(self, request, pk=None):
        ReadingHistory.objects.filter(user=request.user).delete()
        return Response(status=204)


class ReportView(ReaderChoices):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "article_report"

    def post(self, request, pk):
        article = get_object_or_404(NewsArticle, pk=pk)
        message = serializers.CharField(min_length=10, max_length=2000).run_validation(request.data.get("message"))
        ArticleReport.objects.create(article=article, user=request.user, message=message)
        return Response({"detail": "Your report has been sent to the editorial review queue."}, status=201)


class CountryField(serializers.CharField):
    def to_internal_value(self, data):
        country = normalize_country(super().to_internal_value(data))
        if not country:
            raise serializers.ValidationError("Choose a supported edition.")
        return country


class StoryInput(serializers.ModelSerializer):
    location = CountryField()
    article_ids = serializers.PrimaryKeyRelatedField(many=True, queryset=NewsArticle.objects.all(), write_only=True, required=False)
    report_ids = serializers.SerializerMethodField()

    def get_report_ids(self, obj):
        return list(obj.reports.filter(archived=False).values_list("pk", flat=True))

    class Meta:
        model = DevelopingStory
        fields = ["id", "title", "location", "overview", "uncertainty", "agreements", "differences", "published", "revision", "updated_at", "article_ids", "report_ids"]
        read_only_fields = ["revision", "updated_at"]

    def validate(self, data):
        rows = data.get("article_ids", list(self.instance.reports.filter(archived=False)) if self.instance else [])
        location = data.get("location", getattr(self.instance, "location", "ng"))
        if len(rows) > 100:
            raise serializers.ValidationError("Use at most 100 reports per event.")
        if filter_country(NewsArticle.objects.filter(pk__in=[a.pk for a in rows]), location).count() != len({a.pk for a in rows}):
            raise serializers.ValidationError("Every report must match the story's country edition.")
        if data.get("published", getattr(self.instance, "published", False)) and (not rows or not data.get("overview", getattr(self.instance, "overview", "")).strip()):
            raise serializers.ValidationError("Published stories need an overview and at least one source report.")
        # Reassigning from another story is deliberate; its old timeline is hidden
        # unless its evidence still belongs to it. It can be reassigned back.
        return data

    def create(self, data):
        rows = data.pop("article_ids", [])
        story = super().create(data)
        self.assign(story, rows)
        return story

    def update(self, obj, data):
        rows = data.pop("article_ids", None)
        obj = super().update(obj, data)
        if rows is not None:
            self.assign(obj, rows)
        return obj

    def assign(self, story, rows):
        ids = [a.pk for a in rows]
        old_stories = set(NewsArticle.objects.filter(pk__in=ids).exclude(story=None).values_list("story_id", flat=True))
        NewsArticle.all_objects.filter(story=story).exclude(pk__in=ids).update(story=None)
        NewsArticle.objects.filter(pk__in=ids).update(story=story)
        DevelopingStory.objects.filter(pk__in=old_stories - {story.pk}).update(updated_at=timezone.now(), revision=F("revision") + 1)


class PlacementInput(serializers.ModelSerializer):
    location = CountryField()

    class Meta:
        model = HomepagePlacement
        fields = "__all__"

    def validate(self, data):
        def value(key):
            return data.get(key, getattr(self.instance, key, None))
        if value("ends_at") <= (value("starts_at") or timezone.now()):
            raise serializers.ValidationError("Expiry must be after the start time.")
        if not filter_country(NewsArticle.objects.filter(pk=value("article").pk), value("location")).exists():
            raise serializers.ValidationError("Choose a published article from this edition.")
        return data


class SectionInput(serializers.ModelSerializer):
    location = CountryField()
    class Meta:
        model = HomepageSection
        fields = "__all__"


class UpdateInput(serializers.ModelSerializer):
    class Meta:
        model = StoryUpdate
        fields = "__all__"

    def validate(self, data):
        article = data.get("article", getattr(self.instance, "article", None))
        story = data.get("story", getattr(self.instance, "story", None))
        if article.archived or article.story_id != story.pk:
            raise serializers.ValidationError("The evidence article must be a published report in this story.")
        if data.get("occurred_at", getattr(self.instance, "occurred_at", timezone.now())) > timezone.now():
            raise serializers.ValidationError("A timeline update cannot be in the future.")
        return data


class CorrectionInput(serializers.ModelSerializer):
    class Meta:
        model = ArticleCorrection
        fields = "__all__"
        read_only_fields = ["created_at"]


class ReportInput(serializers.ModelSerializer):
    class Meta:
        model = ArticleReport
        fields = ["id", "article", "message", "status", "created_at"]
        read_only_fields = ["article", "message", "created_at"]


EDITORIAL_RESOURCES = {"stories": (DevelopingStory, StoryInput), "placements": (HomepagePlacement, PlacementInput),
                       "sections": (HomepageSection, SectionInput), "updates": (StoryUpdate, UpdateInput),
                       "corrections": (ArticleCorrection, CorrectionInput), "reports": (ArticleReport, ReportInput)}


class EditorialAdmin(APIView):
    permission_classes = [IsSuperAdmin]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    def get_resource(self, resource):
        if resource not in EDITORIAL_RESOURCES:
            from django.http import Http404
            raise Http404
        return EDITORIAL_RESOURCES[resource]

    def get(self, request, resource, pk=None):
        model, serial = self.get_resource(resource)
        if pk:
            return Response(serial(get_object_or_404(model, pk=pk)).data)
        qs = model.objects.order_by("-pk")
        from .pagination import NewsPagination
        pager = NewsPagination()
        return pager.get_paginated_response(serial(pager.paginate_queryset(qs, request, view=self), many=True).data)

    def post(self, request, resource, pk=None):
        if pk or resource == "reports":
            return Response(status=405)
        return self.save(request, resource)

    def put(self, request, resource, pk):
        return self.save(request, resource, pk)

    def save(self, request, resource, pk=None):
        model, cls = self.get_resource(resource)
        from .automation import event, config
        from .models import AutomationSettings
        with transaction.atomic():
            # Serialize membership changes and concurrent editorial writes.
            config()
            AutomationSettings.objects.select_for_update().get(pk=1)
            obj = get_object_or_404(model.objects.select_for_update(), pk=pk) if pk else None
            if obj and resource == "stories" and request.data.get("revision") != obj.revision:
                return Response({"detail": "Story changed. Reload before saving."}, status=409)
            serial = cls(obj, data=request.data, partial=bool(obj))
            serial.is_valid(raise_exception=True)
            audit_details = {"previous_note": obj.note} if obj and resource == "corrections" else {}
            extras = {"revision": (obj.revision + 1 if obj else 1), "updated_at": timezone.now()} if resource == "stories" else {}
            obj = serial.save(**extras)
            if resource == "updates":
                DevelopingStory.objects.filter(pk=obj.story_id).update(updated_at=timezone.now())
            if resource == "corrections":
                NewsArticle.all_objects.filter(pk=obj.article_id).update(updated_at=timezone.now())
            event("audit", f"Saved editorial {resource} #{obj.pk}", actor=request.user, details=audit_details)
        return Response(serial.data, status=200 if pk else 201)

    def delete(self, request, resource, pk):
        if not request.user.is_superuser:
            return Response({"detail": "Only a superuser may permanently delete editorial records."}, status=403)
        model, _ = self.get_resource(resource)
        # Corrections are a public record, not a disposable placement.
        if resource in {"corrections", "reports"}:
            return Response({"detail": "Update this record instead of deleting its history."}, status=405)
        from .automation import event
        with transaction.atomic():
            get_object_or_404(model.objects.select_for_update(), pk=pk).delete()
            event("audit", f"Deleted editorial {resource} #{pk}", actor=request.user)
        return Response(status=204)


class CoverageMonitor(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        from .source_coverage import source_coverage
        payload = source_coverage()
        cutoff = timezone.now() - timedelta(hours=24)
        # Attribute fresh publication to creation, retaining honest original dates.
        recent = list(NewsArticle.objects.filter(created_at__gte=cutoff).values("source_id", "location", "categories").annotate(total=Count("id", distinct=True)))
        source_rows = list(NewsSource.objects.values("id", "name", "location", "enabled", "default_category_id", "last_attempt", "last_success", "last_error"))
        matrix = []
        for country in payload["countries"]:
            code = country["code"]
            sources = [s for s in source_rows if code == "world" or normalize_country(s["location"]) == code]
            for category in payload["categories"]:
                relevant = [s for s in sources if s["default_category_id"] == category["id"]]
                fresh = sum(g["total"] for g in recent if g["categories"] == category["id"] and (code == "world" or filter_location(g["location"], code)))
                matrix.append({"country": code, "category": category["id"], "configured": len(relevant),
                               "enabled": sum(s["enabled"] for s in relevant), "failed": sum(bool(s["last_error"]) for s in relevant),
                               "last_success": max((s["last_success"] for s in relevant if s["last_success"]), default=None),
                               "published": country["published_categories"].get(str(category["id"]), 0), "published_24h": fresh})
        duplicates = list(NewsArticle.objects.exclude(article_url="").values("article_url").annotate(total=Count("pk")).filter(total__gt=1)[:50])
        response = Response({**payload, "matrix": matrix, "sources": source_rows, "duplicate_urls": duplicates,
                             "checked_at": timezone.now(), "note": "Source counts use assigned default categories. General feeds may cover additional topics; fresh counts show actual publications, not guaranteed coverage."})
        response["Cache-Control"] = "private, no-store"
        return response


def filter_location(value, code):
    if code == "world": return True
    import re
    return code in {normalize_country(token) for token in re.split(r"[,;|]", value or "")}
