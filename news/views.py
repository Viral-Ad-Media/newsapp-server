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
            queryset = queryset.filter(location__icontains=location)

        # Filter by time frame
        time_filter = self.request.query_params.get("filter")
        if time_filter == "last_day":
            one_day_ago = timezone.now() - timedelta(days=1)
            queryset = queryset.filter(published_at__gte=one_day_ago)
        elif time_filter == "last_week":
            one_week_ago = timezone.now() - timedelta(weeks=1)
            queryset = queryset.filter(published_at__gte=one_week_ago)
        elif time_filter == "last_month":
            one_month_ago = timezone.now() - timedelta(days=30)  # Approximate month
            queryset = queryset.filter(published_at__gte=one_month_ago)

        return queryset


class NewsDetail(generics.RetrieveAPIView):
    """
    API view to retrieve details of a specific news article by its ID.
    """

    queryset = NewsArticle.objects.select_related("source").prefetch_related(
        "categories", "tags"
    )
    serializer_class = NewsArticleSerializer


class NewsCoverageView(APIView):
    def get(self, request, article_id):
        article = get_object_or_404(NewsArticle, id=article_id)
        coverage_data = {
            "total_sources": article.total_sources or 1,
            "articles": [
                {
                    "id": article.id,
                    "title": article.title,
                    "source": article.source.name,
                    "url": article.article_url,
                    "published_at": article.published_at,
                }
            ],
            "sentiment_stats": {
                "positive": article.sentiment_positive,
                "neutral": article.sentiment_neutral,
                "negative": article.sentiment_negative,
            },
            "sentiment": getattr(article, "sentiment", "Unknown"),
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
