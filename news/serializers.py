from rest_framework import serializers
from taggit.serializers import TagListSerializerField, TaggitSerializer
from .models import NewsSource, NewsCategory, NewsArticle


class NewsSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsSource
        fields = ['id', 'name', 'url']  # Removed 'bias' since it's not a field in NewsSource


class NewsCategorySerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    articles = serializers.PrimaryKeyRelatedField(
        many=True,
        read_only=True
    )

    class Meta:
        model = NewsCategory
        fields = ["id", "name", "image", "image_url", "articles"]

    def get_image_url(self, obj):
        return obj.image.url if obj.image else None


class NewsCategoryLiteSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = NewsCategory
        fields = ["id", "name", "image_url"]

    def get_image_url(self, obj):
        return obj.image.url if obj.image else None


class NewsArticleSerializer(TaggitSerializer, serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    source = NewsSourceSerializer(read_only=True)
    categories = NewsCategoryLiteSerializer(many=True, read_only=True)
    tags = TagListSerializerField(required=False)

    class Meta:
        model = NewsArticle
        fields = [
            "id",
            "title",
            "author",
            "description",
            "content",
            "image",
            "image_url",
            "categories",
            "location",
            "published_at",
            "source",
            "total_sources",
            "sentiment",
            "sentiment_positive",
            "sentiment_neutral",
            "sentiment_negative",
            "tags",
        ]

    def get_image_url(self, obj):
        return obj.image.url if obj.image else None

