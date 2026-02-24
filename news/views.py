from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
from rest_framework import generics, viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework.views import APIView
from .models import NewsArticle, NewsCategory
from .serializers import NewsArticleSerializer, NewsCategorySerializer
from django.http import JsonResponse


class NewsList(generics.ListAPIView):
    """
    API view to list news articles with optional filters for category, location, and time frame.
    """
    serializer_class = NewsArticleSerializer

    def get_queryset(self):
        queryset = NewsArticle.objects.all()

        # Filter by category
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(categories__name__icontains=category)

        # Filter by location
        location = self.request.query_params.get('location')
        if location:
            queryset = queryset.filter(location__icontains=location)

        # Filter by time frame
        time_filter = self.request.query_params.get('filter')
        if time_filter == 'last_day':
            one_day_ago = timezone.now() - timedelta(days=1)
            queryset = queryset.filter(published_at__gte=one_day_ago)
        elif time_filter == 'last_week':
            one_week_ago = timezone.now() - timedelta(weeks=1)
            queryset = queryset.filter(published_at__gte=one_week_ago)
        elif time_filter == 'last_month':
            one_month_ago = timezone.now() - timedelta(days=30)  # Approximate month
            queryset = queryset.filter(published_at__gte=one_month_ago)

        return queryset


class NewsDetail(generics.RetrieveAPIView):
    """
    API view to retrieve details of a specific news article by its ID.
    """
    queryset = NewsArticle.objects.all()
    serializer_class = NewsArticleSerializer


class NewsCoverageView(APIView):
    def get(self, request, article_id):
        try:
            article = NewsArticle.objects.get(id=article_id)
            coverage_data = {
                "total_sources": article.total_sources,
                "sentiment_stats": {
                    "positive": article.sentiment_positive,
                    "neutral": article.sentiment_neutral,
                    "negative": article.sentiment_negative,
                },
                "sentiment": getattr(article, "sentiment", "Unknown"),
            }
            return JsonResponse(coverage_data, status=200)
        except NewsArticle.DoesNotExist:
            return JsonResponse({"error": "Article not found"}, status=404)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class NewsCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API view set for retrieving categories and their related articles.
    """
    queryset = NewsCategory.objects.prefetch_related('articles')
    serializer_class = NewsCategorySerializer


@api_view(['GET'])
def category_detail(request, id):
    """
    API endpoint to retrieve details of a specific category by its ID.
    """
    try:
        category = get_object_or_404(NewsCategory, id=id)
        serializer = NewsCategorySerializer(category)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def related_news(request, article_id):
    """
    API endpoint to fetch related news articles based on shared categories.
    """
    try:
        main_article = get_object_or_404(NewsArticle, id=article_id)

        # Fetch related articles based on shared categories
        related_articles = NewsArticle.objects.filter(
            categories__in=main_article.categories.all()
        ).exclude(id=main_article.id).distinct()[:4]  # Limit to 4 articles

        # Fallback if no related articles are found
        if not related_articles.exists():
            related_articles = NewsArticle.objects.exclude(id=main_article.id)[:4]

        serializer = NewsArticleSerializer(related_articles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except NewsArticle.DoesNotExist:
        return Response({"error": "Article not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
