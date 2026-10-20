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
    path("password-reset/", views.PasswordResetRequestView.as_view(), name="password-reset"),
    path("ask/", views.AskNews.as_view(), name="ask-news"),
    path("lopsided/", views.LopsidedNewsList.as_view(), name="lopsided-news"),
    path("superadmin/overview/", views.SuperadminOverviewView.as_view(), name="superadmin-overview"),
    path("superadmin/articles/", views.SuperadminArticleListCreateView.as_view(), name="superadmin-articles"),
    path("superadmin/articles/<int:article_id>/", views.SuperadminArticleDetailView.as_view(), name="superadmin-article-detail"),
    path("superadmin/categories/", views.SuperadminCategoryListCreateView.as_view(), name="superadmin-categories"),
    path("superadmin/categories/<int:category_id>/", views.SuperadminCategoryDetailView.as_view(), name="superadmin-category-detail"),
    path("superadmin/users/<int:user_id>/", views.SuperadminUserDetailView.as_view(), name="superadmin-user-detail"),
    path("superadmin/actions/", views.SuperadminActionView.as_view(), name="superadmin-actions"),
    path("rss/", views.RssFeedView.as_view(), name="rss-feed"),
    path("news/", NewsList.as_view(), name="news-list"),
    path("news/<int:pk>/", NewsDetail.as_view(), name="news-detail"),
    path(
        "category/<int:category_id>/", views.category_detail, name="category_articles"
    ),
]
