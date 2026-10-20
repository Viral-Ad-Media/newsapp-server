from django.conf import settings
from rest_framework import serializers
from taggit.serializers import TagListSerializerField, TaggitSerializer
from .models import NewsSource, NewsCategory, NewsArticle


def resolve_cloudinary_asset_url(image_field, fallback_url=""):
    """
    Safely resolve a CloudinaryField or remote image URL into a full https:// URL.
    """
    if fallback_url and str(fallback_url).startswith(("http://", "https://")):
        url_str = str(fallback_url)
        if url_str.startswith("http://res.cloudinary.com/"):
            return "https://" + url_str[len("http://") :]
        return url_str

    if image_field:
        try:
            field_url = getattr(image_field, "url", None)
            if field_url and str(field_url).startswith(("http://", "https://")):
                url_str = str(field_url)
                if url_str.startswith("http://res.cloudinary.com/"):
                    return "https://" + url_str[len("http://") :]
                return url_str
        except Exception:
            pass

        raw = str(image_field).strip().lstrip("/")
        if raw and raw != "None":
            if raw.startswith(("http://", "https://")):
                return raw
            cloud_name = (
                getattr(settings, "CLOUDINARY_CLOUD_NAME", "") or "dz58pgz41"
            )
            if raw.startswith("image/upload/"):
                return f"https://res.cloudinary.com/{cloud_name}/{raw}"
            return f"https://res.cloudinary.com/{cloud_name}/image/upload/{raw}"

    return fallback_url or None


class NewsSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsSource
        fields = [
            "id",
            "name",
            "url",
            "bias",
        ]


class NewsCategorySerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    article_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = NewsCategory
        fields = ["id", "name", "image", "image_url", "article_count"]

    def get_image_url(self, obj):
        return resolve_cloudinary_asset_url(obj.image)


class NewsCategoryLiteSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = NewsCategory
        fields = ["id", "name", "image_url"]

    def get_image_url(self, obj):
        return resolve_cloudinary_asset_url(obj.image)


class NewsArticleSerializer(TaggitSerializer, serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    source = NewsSourceSerializer(read_only=True)
    categories = NewsCategoryLiteSerializer(many=True, read_only=True)
    tags = TagListSerializerField(required=False)
    summary = serializers.CharField(source="description", read_only=True)
    is_lopsided = serializers.BooleanField(read_only=True)

    class Meta:
        model = NewsArticle
        fields = [
            "id",
            "title",
            "author",
            "description",
            "summary",
            "article_url",
            "content",
            "image",
            "image_url",
            "categories",
            "location",
            "published_at",
            "source",
            "total_sources",
            "left_coverage",
            "center_coverage",
            "right_coverage",
            "dominant_bias",
            "is_lopsided",
            "perspectives",
            "sentiment",
            "sentiment_positive",
            "sentiment_neutral",
            "sentiment_negative",
            "tags",
        ]

    def get_image_url(self, obj):
        return resolve_cloudinary_asset_url(obj.image, obj.remote_image_url)
