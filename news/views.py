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

        # Filter by category
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(categories__name__icontains=category).distinct()

        # Filter by location
        location = self.request.query_params.get("location")
        if location:
            loc_clean = location.strip().lower()
            if loc_clean == "gb":
                queryset = queryset.filter(
                    Q(location__icontains="gb")
                    | Q(location__icontains="uk")
                    | Q(location__icontains="world")
                )
            else:
                queryset = queryset.filter(location__icontains=location)

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
            filtered = queryset.filter(published_at__gte=cutoff)
            if filtered.exists():
                queryset = filtered
            else:
                latest_pub = (
                    queryset.order_by("-published_at")
                    .values_list("published_at", flat=True)
                    .first()
                )
                if latest_pub:
                    queryset = queryset.filter(published_at__gte=latest_pub - delta)

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

    queryset = NewsCategory.objects.annotate(article_count=Count("articles")).order_by(
        "name", "id"
    )
    serializer_class = NewsCategorySerializer


@api_view(["GET"])
def category_detail(request, category_id):
    """
    API endpoint to retrieve details of a specific category by its ID.
    """
    category = get_object_or_404(
        NewsCategory.objects.annotate(article_count=Count("articles")), id=category_id
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


class AskNews(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ask"

    def post(self, request):
        from rest_framework import serializers
        from .utils.ai_summarizer import answer_question

        class QuestionSerializer(serializers.Serializer):
            question = serializers.CharField(max_length=500)

        serializer = QuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question"]
        queryset = NewsArticle.objects.select_related("source").order_by(
            "-published_at", "-id"
        )
        # Prefer matching stories, otherwise provide the latest available headlines.
        terms = [term.strip(".,?!") for term in question.split() if len(term) > 3][:12]
        query = Q()
        for term in terms:
            query |= Q(title__icontains=term) | Q(description__icontains=term)
        matching = queryset.filter(query) if terms else queryset.none()
        articles = list((matching if matching.exists() else queryset)[:5])
        return Response(
            {
                "answer": answer_question(question, articles),
                "sources": [{"id": a.id, "title": a.title} for a in articles],
            }
        )


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


class PasswordResetRequestView(APIView):
    """
    Privacy-preserving password reset endpoint supporting username or email lookup.
    """

    def post(self, request):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.forms import PasswordResetForm
        from rest_framework import serializers

        class ResetSerializer(serializers.Serializer):
            identifier = serializers.CharField(max_length=254)

        serializer = ResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identifier = serializer.validated_data["identifier"].strip()

        User = get_user_model()
        user = (
            User.objects.filter(
                Q(username__iexact=identifier) | Q(email__iexact=identifier)
            )
            .exclude(email="")
            .first()
        )
        if user and user.email:
            form = PasswordResetForm({"email": user.email})
            if form.is_valid():
                form.save(request=request)

        return Response(
            {
                "detail": (
                    "If an account matches that username or email, "
                    "password recovery instructions have been sent."
                )
            },
            status=status.HTTP_200_OK,
        )


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
            article_count=Count("articles")
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

        return Response(
            NewsArticleSerializer(article).data,
            status=status.HTTP_201_CREATED,
        )


class SuperadminArticleDetailView(APIView):
    """Update or delete an existing article from the Superadmin Dashboard."""

    permission_classes = [IsSuperAdmin]

    def put(self, request, article_id):
        article = get_object_or_404(NewsArticle, pk=article_id)
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

        article.save()

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
        article.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SuperadminCategoryListCreateView(APIView):
    """Create a category from the Superadmin Dashboard."""

    permission_classes = [IsSuperAdmin]

    def post(self, request):
        name = str(request.data.get("name") or "").strip()[:50]
        if not name:
            return Response(
                {"detail": "Category name is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cat, _ = NewsCategory.objects.get_or_create(name=name)
        annotated = NewsCategory.objects.annotate(
            article_count=Count("articles")
        ).get(pk=cat.pk)
        return Response(
            NewsCategorySerializer(annotated).data,
            status=status.HTTP_201_CREATED,
        )


class SuperadminCategoryDetailView(APIView):
    """Delete a category from the Superadmin Dashboard."""

    permission_classes = [IsSuperAdmin]

    def delete(self, request, category_id):
        cat = get_object_or_404(NewsCategory, pk=category_id)
        cat.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SuperadminUserDetailView(APIView):
    """Update user role flags (is_staff, is_superuser, is_active) in Superadmin."""

    permission_classes = [IsSuperAdmin]

    def put(self, request, user_id):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        target_user = get_object_or_404(User, pk=user_id)
        data = request.data

        if "is_staff" in data:
            target_user.is_staff = bool(data["is_staff"])
        if "is_superuser" in data:
            target_user.is_superuser = bool(data["is_superuser"])
        if "is_active" in data:
            target_user.is_active = bool(data["is_active"])
        target_user.save()

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
        from io import StringIO
        from django.core.management import call_command

        action = str(request.data.get("action") or "").strip()
        out = StringIO()

        if action == "seed_news":
            call_command("seed_news", stdout=out)
        elif action == "fetch_allsides":
            call_command("fetch_allsides", offline=True, stdout=out)
        elif action == "fetch_news":
            country = str(request.data.get("country") or "ng").strip().lower()
            if country not in {"ng", "us", "gb"}:
                country = "ng"
            call_command("fetch_news", country=country, stdout=out)
        elif action == "fetch_rss":
            rss_url = str(
                request.data.get("url") or request.data.get("rss_url") or ""
            ).strip()
            category = str(request.data.get("category") or "Business").strip()
            location = str(request.data.get("location") or "ng").strip()
            call_command(
                "fetch_rss",
                url=rss_url,
                category=category,
                location=location,
                offline=not bool(rss_url),
                stdout=out,
            )
        else:
            return Response(
                {"detail": "Unsupported action."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"action": action, "output": out.getvalue().strip()},
            status=status.HTTP_200_OK,
        )


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
            queryset = queryset.filter(categories__name__icontains=category).distinct()

        location = request.query_params.get("location", "").strip().lower()
        if location:
            if location == "gb":
                queryset = queryset.filter(
                    Q(location__icontains="gb")
                    | Q(location__icontains="uk")
                    | Q(location__icontains="world")
                )
            else:
                queryset = queryset.filter(location__icontains=location)

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
