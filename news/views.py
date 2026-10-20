from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Count, Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from datetime import timedelta
from rest_framework import generics, viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework.views import APIView
from .models import NewsArticle, NewsCategory, ReaderProfile
from .serializers import NewsArticleSerializer, NewsCategorySerializer


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
