from django.urls import path
from .views import NewsList, NewsDetail
from . import views

urlpatterns = [
    path('news/', NewsList.as_view(), name='news-list'),
    path('news/<int:pk>/', NewsDetail.as_view(), name='news-detail'),
    path('category/<int:category_id>/', views.category_detail, name='category_articles'),
]
