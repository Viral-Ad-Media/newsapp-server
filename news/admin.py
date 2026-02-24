from django.contrib import admin
from .models import NewsSource, NewsCategory, NewsArticle

@admin.register(NewsSource)
class NewsSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'url')

@admin.register(NewsCategory)
class NewsCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'image')

@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'source', 'published_at', 'location', 'total_sources')
    search_fields = ('title', 'content', 'description')
    list_filter = ('source', 'categories', 'location')
