from .category_filters import filter_category
from .regions import filter_country, country_query, COUNTRY_NAMES
from .seo import SEO_FIELDS, SeoInputSerializer, analyze_seo
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Count, Q
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView
from datetime import timedelta
from rest_framework import generics, viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework.views import APIView
from .models import NewsArticle, NewsCategory, NewsSource, ReaderProfile
from .serializers import NewsArticleSerializer, NewsCategorySerializer, NewsSourceSerializer


class IsSuperAdmin(BasePermission):
    """Allow access only to authenticated superusers or staff members."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.is_staff)
        )


class EmailOrUsernameTokenObtainPairView(TokenObtainPairView):
    """
    Allow signing in with either a username or an email address (e.g.
    viraladmediacontent@gmail.com) and return role flags alongside JWT tokens.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        raw_identifier = str(data.get("username") or data.get("email") or "").strip()

        matched_user = None
        if raw_identifier:
            matched_user = User.objects.filter(
                Q(username__iexact=raw_identifier) | Q(email__iexact=raw_identifier)
            ).first()
            if matched_user:
                data["username"] = matched_user.get_username()

        serializer = self.get_serializer(data=data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return super().post(request, *args, **kwargs)

        user_obj = getattr(serializer, "user", None) or matched_user
        payload = dict(serializer.validated_data)
        if user_obj:
            payload["username"] = user_obj.get_username()
            payload["email"] = user_obj.email
            payload["is_staff"] = bool(user_obj.is_staff)
            payload["is_superuser"] = bool(user_obj.is_superuser)
        return Response(payload, status=status.HTTP_200_OK)


class NewsList(generics.ListAPIView):
    """
    API view to list news articles with optional filters for category, location, and time frame.
    """

    serializer_class = NewsArticleSerializer

    def get_queryset(self):
        queryset = (
            NewsArticle.objects.select_related("source")
            .prefetch_related("categories", "tags")
            .order_by("-published_at", "-id")
        )

        category_id = self.request.query_params.get("category_id")
        if category_id:
            if not category_id.isdigit():
                return queryset.none()
            queryset = queryset.filter(categories__id=int(category_id))
        search = self.request.query_params.get("search", "").strip()[:200]
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )

        queryset = filter_category(queryset, self.request.query_params.get("category"))

        queryset = filter_country(queryset, self.request.query_params.get("location"))

        # Filter by bias / lopsided status
        bias = self.request.query_params.get("bias", "").strip().lower()
        if bias == "lopsided":
            queryset = queryset.filter(
                Q(dominant_bias__in=["left", "right", "polarized"])
                | Q(left_coverage__gte=42)
                | Q(right_coverage__gte=42)
                | Q(center_coverage__lte=22)
            )
        elif bias in {"left", "center", "right", "polarized", "balanced"}:
            queryset = queryset.filter(dominant_bias=bias)

        # Filter by time frame (fallback to latest anchor date if static dataset is older)
        time_filter = self.request.query_params.get("filter")
        if time_filter in {"last_day", "last_week", "last_month"}:
            days_map = {"last_day": 1, "last_week": 7, "last_month": 30}
            delta = timedelta(days=days_map[time_filter])
            cutoff = timezone.now() - delta
            queryset = queryset.filter(published_at__gte=cutoff, published_at__lte=timezone.now())

        return queryset


class NewsDetail(generics.RetrieveAPIView):
    """
    API view to retrieve details of a specific news article by its ID.
    """

    queryset = NewsArticle.objects.select_related("source").prefetch_related(
        "categories", "tags"
    )
    serializer_class = NewsArticleSerializer


class LopsidedNewsList(NewsList):
    """
    API view returning AllSides-inspired multi-perspective and lopsided stories.
    """

    def get_queryset(self):
        return super().get_queryset()


class NewsCoverageView(APIView):
    def get(self, request, article_id):
        article = get_object_or_404(NewsArticle, id=article_id)
        perspectives = article.perspectives if isinstance(article.perspectives, list) else []
        articles_payload = [
            {
                "id": article.id,
                "title": article.title,
                "source": article.source.name,
                "url": article.article_url,
                "published_at": article.published_at,
                "bucket": "center",
                "label": "Primary Report",
                "bias_rating": getattr(article.source, "bias", "Center") or "Center",
                "summary": article.description,
            }
        ]
        for idx, item in enumerate(perspectives, start=1):
            if not isinstance(item, dict):
                continue
            item_url = item.get("url") or article.article_url
            if idx == 1 and item_url == article.article_url and item.get("source") == article.source.name:
                continue
            articles_payload.append(
                {
                    "id": f"{article.id}-p{idx}",
                    "title": item.get("title") or article.title,
                    "source": item.get("source") or article.source.name,
                    "source_url": item.get("source_url") or "",
                    "url": item_url,
                    "published_at": article.published_at,
                    "bucket": item.get("bucket") or "center",
                    "label": item.get("label") or "Perspective",
                    "bias_rating": item.get("bias_rating") or "Center",
                    "summary": item.get("summary") or "",
                }
            )
        pos = float(article.sentiment_positive or 0.0)
        neu = float(article.sentiment_neutral or 0.0)
        neg = float(article.sentiment_negative or 0.0)
        sentiment_label = getattr(article, "sentiment", "neutral") or "neutral"

        if pos == 0.0 and neu == 0.0 and neg == 0.0:
            if article.dominant_bias == "polarized" or article.is_lopsided:
                pos, neu, neg = 22.0, 40.0, 38.0
                if sentiment_label in {"neutral", "Unknown"}:
                    sentiment_label = "contested"
            elif article.dominant_bias in {"left", "right"}:
                pos, neu, neg = 28.0, 44.0, 28.0
                if sentiment_label in {"neutral", "Unknown"}:
                    sentiment_label = "analytical"
            else:
                pos, neu, neg = 34.0, 48.0, 18.0
                if sentiment_label in {"neutral", "Unknown"}:
                    sentiment_label = "balanced"

        index_value = max(
            15,
            min(
                95,
                round(
                    50
                    + (pos - neg) * 0.45
                    + ((article.center_coverage or 34) - 33) * 0.35
                ),
            ),
        )

        coverage_data = {
            "total_sources": max(article.total_sources or 1, len(perspectives)),
            "left_coverage": article.left_coverage,
            "center_coverage": article.center_coverage,
            "right_coverage": article.right_coverage,
            "dominant_bias": article.dominant_bias,
            "is_lopsided": article.is_lopsided,
            "perspectives": perspectives,
            "articles": articles_payload,
            "sentiment_stats": {
                "positive": pos,
                "neutral": neu,
                "negative": neg,
            },
            "sentiment": sentiment_label,
            "index_value": index_value,
        }
        return Response(coverage_data, status=status.HTTP_200_OK)


class NewsCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API view set for retrieving categories and their related articles.
    """

    queryset = NewsCategory.objects.annotate(article_count=Count("articles", filter=Q(articles__archived=False))).order_by(
        "name", "id"
    )
    serializer_class = NewsCategorySerializer

    def get_queryset(self):
        location = self.request.query_params.get("location")
        if not location:
            return super().get_queryset()
        return NewsCategory.objects.annotate(
            article_count=Count("articles", filter=country_query(location, "articles__location") & Q(articles__archived=False))
        ).filter(article_count__gt=0).order_by("name", "id")


@api_view(["GET"])
def category_detail(request, category_id):
    """
    API endpoint to retrieve details of a specific category by its ID.
    """
    category = get_object_or_404(
        NewsCategory.objects.annotate(article_count=Count("articles", filter=Q(articles__archived=False))), id=category_id
    )
    serializer = NewsCategorySerializer(category)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
def related_news(request, article_id):
    """
    API endpoint to fetch related news articles based on shared categories.
    """
    main_article = get_object_or_404(
        NewsArticle.objects.prefetch_related("categories"),
        id=article_id,
    )
    base_queryset = (
        NewsArticle.objects.select_related("source")
        .prefetch_related("categories", "tags")
        .exclude(id=main_article.id)
        .order_by("-published_at", "-id")
    )
    base_queryset = filter_country(base_queryset, request.query_params.get("location") or main_article.location)
    related_queryset = base_queryset.filter(
        categories__in=main_article.categories.all()
    ).distinct()
    related_articles = (
        related_queryset[:4] if related_queryset.exists() else base_queryset[:4]
    )

    serializer = NewsArticleSerializer(related_articles, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


class PersonalizedNewsList(NewsList):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        profile, _ = ReaderProfile.objects.get_or_create(user=self.request.user)
        queryset = super().get_queryset()
        if profile.categories.exists():
            queryset = queryset.filter(
                categories__in=profile.categories.all()
            ).distinct()
        return queryset


class LiveNewsList(NewsList):
    """All published categories for members, independent of topic subscriptions."""
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class SavedNewsList(NewsList):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        profile, _ = ReaderProfile.objects.get_or_create(user=self.request.user)
        return super().get_queryset().filter(readerprofile=profile)


class SavedNewsDetail(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, article_id):
        profile, _ = ReaderProfile.objects.get_or_create(user=request.user)
        return Response(
            {"saved": profile.saved_articles.filter(pk=article_id).exists()}
        )

    def put(self, request, article_id):
        article = get_object_or_404(NewsArticle, pk=article_id)
        profile, _ = ReaderProfile.objects.get_or_create(user=request.user)
        profile.saved_articles.add(article)
        return Response({"saved": True})

    def delete(self, request, article_id):
        profile, _ = ReaderProfile.objects.get_or_create(user=request.user)
        profile.saved_articles.remove(article_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReaderPreferences(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile, _ = ReaderProfile.objects.get_or_create(user=request.user)
        return Response(
            {"categories": list(profile.categories.values_list("id", flat=True))}
        )

    def put(self, request):
        from rest_framework import serializers

        class PreferencesSerializer(serializers.Serializer):
            categories = serializers.PrimaryKeyRelatedField(
                many=True, queryset=NewsCategory.objects.all(), allow_empty=True
            )

        serializer = PreferencesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile, _ = ReaderProfile.objects.get_or_create(user=request.user)
        profile.categories.set(serializer.validated_data["categories"])
        return self.get(request)


class SavedNewsStatus(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from rest_framework.exceptions import ValidationError

        values = request.query_params.get("ids", "").split(",")
        if len(values) > 100 or any(
            not value.isdigit() or len(value) > 18 for value in values
        ):
            raise ValidationError("Provide up to 100 article IDs.")
        profile, _ = ReaderProfile.objects.get_or_create(user=request.user)
        saved_ids = list(
            profile.saved_articles.filter(
                pk__in=[int(value) for value in values]
            ).values_list("id", flat=True)
        )
        return Response({"saved_ids": saved_ids})


class SuperadminOverviewView(APIView):
    """
    Return comprehensive metrics, articles, categories, sources, and users
    for the Superadmin Command Center.
    """

    permission_classes = [IsSuperAdmin]

    def get(self, request):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        articles_qs = (
            NewsArticle.objects.select_related("source")
            .prefetch_related("categories", "tags")
            .order_by("-published_at", "-id")
        )
        categories_qs = NewsCategory.objects.annotate(
            article_count=Count("articles", filter=Q(articles__archived=False))
        ).order_by("name", "id")
        sources_qs = NewsSource.objects.annotate(
            article_count=Count("newsarticle")
        ).order_by("name", "id")
        users_qs = User.objects.order_by("-is_superuser", "-is_staff", "-date_joined")

        total_articles = articles_qs.count()
        bias_counts = {
            "left": articles_qs.filter(dominant_bias="left").count(),
            "center": articles_qs.filter(dominant_bias="center").count(),
            "right": articles_qs.filter(dominant_bias="right").count(),
            "balanced": articles_qs.filter(dominant_bias="balanced").count(),
            "polarized": articles_qs.filter(dominant_bias="polarized").count(),
        }
        location_counts = {
            "ng": articles_qs.filter(location__iexact="ng").count(),
            "us": articles_qs.filter(location__iexact="us").count(),
            "gb": articles_qs.filter(
                Q(location__iexact="gb") | Q(location__iexact="uk")
            ).count(),
            "world": articles_qs.filter(location__iexact="world").count(),
        }

        total_bookmarks = sum(
            p.saved_articles.count()
            for p in ReaderProfile.objects.prefetch_related("saved_articles")
        )

        users_payload = []
        for u in users_qs[:100]:
            profile = getattr(u, "readerprofile", None)
            users_payload.append(
                {
                    "id": u.id,
                    "username": u.get_username(),
                    "email": u.email or "",
                    "is_staff": bool(u.is_staff),
                    "is_superuser": bool(u.is_superuser),
                    "is_active": bool(u.is_active),
                    "date_joined": u.date_joined,
                    "saved_count": profile.saved_articles.count() if profile else 0,
                    "topics_count": profile.categories.count() if profile else 0,
                }
            )

        sources_payload = [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "bias": s.bias,
                "article_count": s.article_count,
            }
            for s in sources_qs
        ]

        return Response(
            {
                "stats": {
                    "total_articles": total_articles,
                    "total_categories": categories_qs.count(),
                    "total_sources": sources_qs.count(),
                    "total_users": users_qs.count(),
                    "total_superadmins": users_qs.filter(
                        Q(is_superuser=True) | Q(is_staff=True)
                    ).count(),
                    "total_bookmarks": total_bookmarks,
                    "bias_counts": bias_counts,
                    "location_counts": location_counts,
                },
                "integrations": {
                    "news_api_configured": bool( getattr(settings, "NEWS_API_KEY", "") ),
                    "newsdata_io_configured": bool(
                        getattr(settings, "NEWS_DATA_IO_API_KEY", "")
                    ),
                    "cloudinary_configured": bool(
                        getattr(settings, "CLOUDINARY_CLOUD_NAME", "")
                        and getattr(settings, "CLOUDINARY_API_KEY", "")
                        and getattr(settings, "CLOUDINARY_API_SECRET", "")
                    ),
                    "openai_configured": bool(
                        getattr(settings, "OPENAI_API_KEY", "")
                    ),
                },
                "articles": NewsArticleSerializer(articles_qs[:50], many=True).data,
                "categories": NewsCategorySerializer(categories_qs, many=True).data,
                "sources": sources_payload,
                "users": users_payload,
            },
            status=status.HTTP_200_OK,
        )


class SuperadminArticleListCreateView(APIView):
    """Create a new blog post / news article from the Superadmin Dashboard."""

    permission_classes = [IsSuperAdmin]

    def post(self, request):
        data = request.data
        seo = SeoInputSerializer(data={key: data[key] for key in SEO_FIELDS if key in data})
        seo.is_valid(raise_exception=True)
        title = str(data.get("title") or "").strip()[:255]
        description = str(data.get("description") or "").strip()
        content = str(data.get("content") or description).strip()

        if not title or not description:
            return Response(
                {"detail": "Both title and summary description are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .article_writing import article_quality
        quality = article_quality(content)
        if not quality["passed"]:
            return Response({"content": quality["errors"]}, status=400)

        source_name = str(data.get("source_name") or "AbokiNews Editorial").strip()[:100]
        source_url = str(data.get("source_url") or "https://nairametrics.com").strip()
        source_bias = str(data.get("source_bias") or "center").strip().lower()
        if source_bias not in {"left", "lean_left", "center", "lean_right", "right"}:
            source_bias = "center"

        source, _ = NewsSource.objects.get_or_create(
            name=source_name,
            defaults={"url": source_url, "bias": source_bias},
        )

        left_cov = int(data.get("left_coverage", 33))
        center_cov = int(data.get("center_coverage", 34))
        right_cov = max(0, 100 - left_cov - center_cov)
        dominant_bias = str(data.get("dominant_bias") or "balanced").strip().lower()
        if dominant_bias not in {"left", "center", "right", "balanced", "polarized"}:
            dominant_bias = "balanced"

        article_url = str(data.get("article_url") or "").strip()
        remote_image_url = str(data.get("remote_image_url") or "").strip()
        location = str(data.get("location") or "ng").strip().lower()[:100]
        author = str(data.get("author") or request.user.get_username()).strip()[:150]

        perspectives = data.get("perspectives")
        if not isinstance(perspectives, list):
            perspectives = []
        # A new editorial post has one source unless actual perspectives are
        # supplied. Never manufacture publisher names or quotations on publish.


        article = NewsArticle.objects.create(
            **seo.validated_data,
            title=title,
            author=author,
            description=description,
            content=content,
            article_url=article_url,
            remote_image_url=remote_image_url,
            source=source,
            location=location,
            published_at=timezone.now(),
            total_sources=max(1, len(perspectives)),
            left_coverage=left_cov,
            center_coverage=center_cov,
            right_coverage=right_cov,
            dominant_bias=dominant_bias,
            perspectives=perspectives,
            sentiment="neutral",
            sentiment_positive=0.0,
            sentiment_neutral=0.0,
            sentiment_negative=0.0,
        )

        raw_categories = data.get("categories") or ["Business"]
        if isinstance(raw_categories, str):
            raw_categories = [
                c.strip() for c in raw_categories.split(",") if c.strip()
            ]
        cat_objs = [
            NewsCategory.objects.get_or_create(name=str(name).strip()[:50])[0]
            for name in raw_categories
            if str(name).strip()
        ]
        if cat_objs:
            article.categories.set(cat_objs)

        from .automation import event
        event("audit", f"Created article #{article.pk}", actor=request.user)
        return Response(
            NewsArticleSerializer(article).data,
            status=status.HTTP_201_CREATED,
        )


class SuperadminArticleDetailView(APIView):
    """Update or delete an existing article from the Superadmin Dashboard."""

    permission_classes = [IsSuperAdmin]

    def put(self, request, article_id):
        article = get_object_or_404(NewsArticle, pk=article_id)
        if article.imported and set(request.data) & {"title", "description", "author", "source", "article_url", "published_at"}:
            return Response({"detail": "Imported source fields are read-only."}, status=400)
        article.revision += 1
        data = request.data
        seo = SeoInputSerializer(data={key: data[key] for key in SEO_FIELDS if key in data})
        seo.is_valid(raise_exception=True)
        for key, value in seo.validated_data.items():
            setattr(article, key, value)

        if "title" in data and str(data["title"]).strip():
            article.title = str(data["title"]).strip()[:255]
        if "description" in data and str(data["description"]).strip():
            article.description = str(data["description"]).strip()
        if "content" in data and str(data["content"]).strip():
            article.content = str(data["content"]).strip()
        if "location" in data and str(data["location"]).strip():
            article.location = str(data["location"]).strip().lower()[:100]
        if "remote_image_url" in data:
            article.remote_image_url = str(data["remote_image_url"] or "").strip()
        if "article_url" in data:
            article.article_url = str(data["article_url"] or "").strip()
        if "dominant_bias" in data:
            bias_val = str(data["dominant_bias"]).strip().lower()
            if bias_val in {"left", "center", "right", "balanced", "polarized"}:
                article.dominant_bias = bias_val
        if "left_coverage" in data and "center_coverage" in data:
            l_cov = int(data["left_coverage"])
            c_cov = int(data["center_coverage"])
            article.left_coverage = l_cov
            article.center_coverage = c_cov
            article.right_coverage = max(0, 100 - l_cov - c_cov)

        if "content" in data:
            from .article_writing import article_quality
            quality = article_quality(article.content)
            if not quality["passed"]:
                return Response({"content": quality["errors"]}, status=400)
        article.save()
        from .automation import event
        event("audit", f"Updated article #{article.pk}", actor=request.user)

        if "categories" in data:
            raw_cats = data["categories"]
            if isinstance(raw_cats, str):
                raw_cats = [c.strip() for c in raw_cats.split(",") if c.strip()]
            if isinstance(raw_cats, list):
                cat_objs = [
                    NewsCategory.objects.get_or_create(name=str(name).strip()[:50])[0]
                    for name in raw_cats
                    if str(name).strip()
                ]
                if cat_objs:
                    article.categories.set(cat_objs)

        return Response(NewsArticleSerializer(article).data, status=status.HTTP_200_OK)

    def delete(self, request, article_id):
        article = get_object_or_404(NewsArticle, pk=article_id)
        if not request.user.is_superuser:
            return Response({"detail": "Only a superuser may delete articles."}, status=403)
        from .automation import event
        event("audit", f"Deleted article #{article.pk}", actor=request.user)
        article.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SuperadminCategoryListCreateView(APIView):
    """Create a category from the Superadmin Dashboard."""

    permission_classes = [IsSuperAdmin]

    def post(self, request):
        from django.db import transaction
        from .admin_api import CategoryInput
        from .automation import config, event
        from .models import AutomationSettings
        with transaction.atomic():
            config()
            AutomationSettings.objects.select_for_update().get(pk=1)
            serial = CategoryInput(data=request.data)
            serial.is_valid(raise_exception=True)
            cat = serial.save()
            event("audit", f"Created category #{cat.pk}", actor=request.user)
        return Response(NewsCategorySerializer(cat).data, status=201)


class SuperadminCategoryDetailView(APIView):
    """Delete a category from the Superadmin Dashboard."""

    permission_classes = [IsSuperAdmin]

    def delete(self, request, category_id):
        cat = get_object_or_404(NewsCategory, pk=category_id)
        if not request.user.is_superuser:
            return Response({"detail": "Only a superuser may delete categories."}, status=403)
        if NewsArticle.all_objects.filter(categories=cat).exists():
            return Response({"detail": "Reassign associated articles first."}, status=409)
        from .automation import event
        event("audit", f"Deleted category #{cat.pk}", actor=request.user)
        cat.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SuperadminUserDetailView(APIView):
    """Update user role flags (is_staff, is_superuser, is_active) in Superadmin."""

    permission_classes = [IsSuperAdmin]

    def put(self, request, user_id):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        if not request.user.is_superuser:
            return Response({"detail": "Only a superuser may manage roles."}, status=403)
        target_user = get_object_or_404(User, pk=user_id)
        data = request.data

        from rest_framework import serializers
        if "is_staff" in data:
            target_user.is_staff = serializers.BooleanField().run_validation(data["is_staff"])
        if "is_superuser" in data:
            target_user.is_superuser = serializers.BooleanField().run_validation(data["is_superuser"])
        if "is_active" in data:
            target_user.is_active = serializers.BooleanField().run_validation(data["is_active"])
        target_user.save()
        from .automation import event
        event("audit", f"Updated roles for user #{target_user.pk}", actor=request.user)

        return Response(
            {
                "id": target_user.id,
                "username": target_user.get_username(),
                "email": target_user.email or "",
                "is_staff": bool(target_user.is_staff),
                "is_superuser": bool(target_user.is_superuser),
                "is_active": bool(target_user.is_active),
            },
            status=status.HTTP_200_OK,
        )


class SuperadminActionView(APIView):
    """Trigger seed_news or fetch_allsides pipeline commands from Superadmin."""

    permission_classes = [IsSuperAdmin]

    def post(self, request):
        from .automation import queue_run, Busy
        if request.data.get("action") not in {"fetch_rss", "fetch_news"}:
            return Response({"detail": "Use configured feeds and the durable crawler. Sample import commands are CLI-only."}, status=400)
        try:
            run = queue_run(actor=request.user)
        except Busy as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response({"run_id": run.pk, "output": f"Run #{run.pk} queued; inspect /admin/logs. A worker must be running."}, status=202)


class RssFeedView(APIView):
    """
    Serve an RSS 2.0 XML syndication feed of the latest news articles,
    supporting optional ?category=, ?location=, and ?bias= query filters.
    """

    def get(self, request):
        from django.http import HttpResponse
        from .utils.rss_ingestor import build_rss_20_xml

        queryset = (
            NewsArticle.objects.select_related("source")
            .prefetch_related("categories")
            .order_by("-published_at", "-id")
        )

        category = request.query_params.get("category", "").strip()
        if category:
            queryset = filter_category(queryset, category)

        location = request.query_params.get("location", "").strip().lower()
        queryset = filter_country(queryset, location)

        bias = request.query_params.get("bias", "").strip().lower()
        if bias == "lopsided":
            queryset = queryset.filter(
                Q(dominant_bias__in=["left", "right", "polarized"])
                | Q(left_coverage__gte=42)
                | Q(right_coverage__gte=42)
                | Q(center_coverage__lte=22)
            )
        elif bias in {"left", "center", "right", "polarized", "balanced"}:
            queryset = queryset.filter(dominant_bias=bias)

        title_suffix = []
        if category:
            title_suffix.append(category.title())
        if location:
            title_suffix.append(location.upper())
        if bias:
            title_suffix.append(f"{bias.title()} Spectrum")

        feed_title = "AbokiNews — Multi-Perspective News Feed"
        if title_suffix:
            feed_title = f"AbokiNews — {' · '.join(title_suffix)} RSS Feed"

        xml_body = build_rss_20_xml(
            list(queryset[:30]),
            site_title=feed_title,
            site_link=request.build_absolute_uri("/").rstrip("/"),
            feed_url=request.build_absolute_uri(),
        )
        return HttpResponse(
            xml_body,
            content_type="application/rss+xml; charset=utf-8",
        )


class SuperadminSeoAnalysisView(APIView):
    """Evaluate an unsaved draft, without writes or external provider requests."""

    permission_classes = [IsSuperAdmin]

    def post(self, request):
        if len(str(request.data.get("content") or "")) > 200000:
            return Response({"detail": "Analyze up to 200,000 characters at a time."}, status=400)
        seo = SeoInputSerializer(data={key: request.data[key] for key in SEO_FIELDS if key in request.data})
        seo.is_valid(raise_exception=True)
        return Response(analyze_seo({**request.data, **seo.validated_data}))


class PublicSitemapView(APIView):
    """News is immediately public; omit only explicitly noindexed canonicals."""

    def get(self, request):
        from django.http import HttpResponse
        from xml.etree.ElementTree import Element, SubElement, tostring
        from urllib.parse import urlsplit

        origin = settings.PUBLISHING_SITE_URL.rstrip("/")
        root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
        for article in NewsArticle.objects.filter(seo_noindex=False).order_by("id").iterator(chunk_size=500):
            canonical = article.canonical_url or f"{origin}/news/{article.pk}"
            if urlsplit(canonical).netloc.casefold() != urlsplit(origin).netloc.casefold():
                continue
            row = SubElement(root, "url")
            SubElement(row, "loc").text = canonical
        response = HttpResponse(tostring(root, encoding="utf-8", xml_declaration=True), content_type="application/xml")
        response["Cache-Control"] = "no-store"
        return response
