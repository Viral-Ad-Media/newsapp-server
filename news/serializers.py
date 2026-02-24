from rest_framework import serializers
from taggit.serializers import TagListSerializerField, TaggitSerializer
from .models import NewsSource, NewsCategory, NewsArticle


class NewsSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsSource
        fields = ['id', 'name', 'url']  # Removed 'bias' since it's not a field in NewsSource


class NewsCategorySerializer(serializers.ModelSerializer):
    articles = serializers.PrimaryKeyRelatedField(
        many=True,
        read_only=True
    )  # Ensure this works with the ManyToManyField in NewsArticle

    class Meta:
        model = NewsCategory
        fields = ['id', 'name', 'image', 'articles']  # Added 'articles' to include related posts


class NewsArticleSerializer(TaggitSerializer, serializers.ModelSerializer):
    source = NewsSourceSerializer()
    categories = NewsCategorySerializer(many=True)
    tags = TagListSerializerField()  # Serialize tags as a list of strings

    class Meta:
        model = NewsArticle
        fields = '__all__'  # Include all fields, including tags

