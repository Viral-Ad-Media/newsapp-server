"""
URL configuration for newsapp project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from .views import service_status
from .social_auth import GoogleLoginView
from rest_framework.routers import DefaultRouter
from news.views import (
    EmailOrUsernameTokenObtainPairView,
    NewsCategoryViewSet,
    NewsCoverageView,
    RssFeedView,
    related_news,
)


router = DefaultRouter()
router.register(r'categories', NewsCategoryViewSet)


from .jwt import PasswordAwareRefreshView


urlpatterns = [
    path('', service_status, name='service-status'),
    path('health/', service_status, name='health'),
    path('rss.xml', RssFeedView.as_view(), name='root-rss-xml'),
    path('admin/', admin.site.urls),
    path('api/', include('news.urls')),
    path('api/', include(router.urls)),
    path('accounts/', include('allauth.urls')),  # <-- Include allauth URLs
    path('api/auth/google/', GoogleLoginView.as_view(), name='google-login'),
    path('api/auth/', include('dj_rest_auth.urls')),  # Login/Logout/Password reset etc.
    path('api/auth/registration/', include('dj_rest_auth.registration.urls')),  # Registration
    path('api/token/', EmailOrUsernameTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', PasswordAwareRefreshView.as_view(), name='token_refresh'),
    path('api/news/<int:article_id>/coverage/', NewsCoverageView.as_view(), name='news-coverage'),
    path('api/news/<int:article_id>/related/', related_news, name='related_news'),


]

if settings.MEDIA_URL.startswith("/"):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

