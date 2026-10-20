from django.urls import path
from .views import NewsList, NewsDetail
from . import views

urlpatterns = [
    path("saved-status/", views.SavedNewsStatus.as_view(), name="saved-status"),
    path("feed/", views.PersonalizedNewsList.as_view(), name="reader-feed"),
    path("saved/", views.SavedNewsList.as_view(), name="saved-news"),
    path(
        "saved/<int:article_id>/",
        views.SavedNewsDetail.as_view(),
        name="saved-news-detail",
    ),
    path("preferences/", views.ReaderPreferences.as_view(), name="reader-preferences"),
    path("ask/", views.AskNews.as_view(), name="ask-news"),
    path("news/", NewsList.as_view(), name="news-list"),
    path("news/<int:pk>/", NewsDetail.as_view(), name="news-detail"),
    path(
        "category/<int:category_id>/", views.category_detail, name="category_articles"
    ),
]
