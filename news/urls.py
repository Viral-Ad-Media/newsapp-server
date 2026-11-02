from django.urls import path
from .views import NewsList, NewsDetail
from . import views
from .admin_api import AdminAPI
from .ask import AskNews
from .recovery import PasswordResetRequestView, PasswordResetConfirmView
from .editorial import EditionView, StoriesView, SourcesView, ReaderChoices, ReaderChoiceDetail, HistoryView, ReportView, EditorialAdmin, CoverageMonitor

urlpatterns = [
    path("edition/", EditionView.as_view()),
    path("stories/", StoriesView.as_view()),
    path("stories/<int:pk>/", StoriesView.as_view()),
    path("sources/", SourcesView.as_view()),
    path("sources/<int:pk>/", SourcesView.as_view()),
    path("reader-choices/", ReaderChoices.as_view()),
    path("reader-choices/<str:kind>/<int:pk>/", ReaderChoiceDetail.as_view()),
    path("history/", HistoryView.as_view()),
    path("history/<int:pk>/", HistoryView.as_view()),
    path("article-reports/<int:pk>/", ReportView.as_view()),
    path("admin/coverage-monitor/", CoverageMonitor.as_view()),
    path("admin/editorial/<str:resource>/", EditorialAdmin.as_view()),
    path("admin/editorial/<str:resource>/<int:pk>/", EditorialAdmin.as_view()),
    path("admin/<str:resource>/", AdminAPI.as_view()),
    path("admin/<str:resource>/<int:pk>/", AdminAPI.as_view()),
    path("admin/<str:resource>/<int:pk>/<str:action>/", AdminAPI.as_view()),
    path("sitemap/", views.PublicSitemapView.as_view(), name="public-sitemap"),
    path("superadmin/seo/analyze/", views.SuperadminSeoAnalysisView.as_view(), name="superadmin-seo-analysis"),
    path("saved-status/", views.SavedNewsStatus.as_view(), name="saved-status"),
    path("live/", views.LiveNewsList.as_view(), name="live-news"),
    path("feed/", views.PersonalizedNewsList.as_view(), name="reader-feed"),
    path("saved/", views.SavedNewsList.as_view(), name="saved-news"),
    path(
        "saved/<int:article_id>/",
        views.SavedNewsDetail.as_view(),
        name="saved-news-detail",
    ),
    path("preferences/", views.ReaderPreferences.as_view(), name="reader-preferences"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path("ask/", AskNews.as_view(), name="ask-news"),
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
