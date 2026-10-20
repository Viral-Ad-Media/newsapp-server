from django.conf import settings
from django.utils import timezone
from django.db import models
from cloudinary.models import CloudinaryField
from taggit.managers import TaggableManager  # Import the TaggableManager for tagging


class NewsSource(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField()
    bias = models.CharField(max_length=30, default="center", blank=True)

    def __str__(self):
        return self.name


class NewsCategory(models.Model):
    name = models.CharField(max_length=100)
    image = CloudinaryField("image", blank=True, null=True)

    def __str__(self):
        return self.name


class NewsArticle(models.Model):
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField()
    content = models.TextField()
    image = CloudinaryField("image", blank=True, null=True)
    categories = models.ManyToManyField(NewsCategory, related_name="articles")
    location = models.CharField(max_length=100, blank=True, null=True)
    published_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.ForeignKey(NewsSource, on_delete=models.CASCADE)

    article_url = models.URLField(max_length=2000, blank=True)
    remote_image_url = models.URLField(max_length=2000, blank=True)

    focus_keyword = models.CharField(max_length=100, blank=True)
    seo_title = models.CharField(max_length=120, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    canonical_url = models.URLField(max_length=2000, blank=True)
    og_title = models.CharField(max_length=120, blank=True)
    og_description = models.CharField(max_length=320, blank=True)
    og_image_url = models.URLField(max_length=2000, blank=True)
    image_alt = models.CharField(max_length=255, blank=True)
    seo_noindex = models.BooleanField(default=False)
    seo_nofollow = models.BooleanField(default=False)
    schema_type = models.CharField(
        max_length=20,
        default="NewsArticle",
        choices=[
            ("Article", "Article"),
            ("NewsArticle", "NewsArticle"),
            ("BlogPosting", "BlogPosting"),
        ],
    )

    # Coverage-related fields
    total_sources = models.IntegerField(default=0)
    left_coverage = models.IntegerField(default=33)
    center_coverage = models.IntegerField(default=34)
    right_coverage = models.IntegerField(default=33)
    dominant_bias = models.CharField(
        max_length=20,
        choices=[
            ("left", "Left-Leaning"),
            ("center", "Center-Focused"),
            ("right", "Right-Leaning"),
            ("polarized", "Polarized / Center Blindspot"),
            ("balanced", "Balanced"),
        ],
        default="balanced",
    )
    perspectives = models.JSONField(default=list, blank=True)

    @property
    def is_lopsided(self):
        return (
            self.dominant_bias in {"left", "right", "polarized"}
            or max(self.left_coverage, self.right_coverage) >= 42
            or min(self.left_coverage, self.center_coverage, self.right_coverage) <= 22
        )

    # Sentiment-related fields
    sentiment = models.CharField(
        max_length=10,
        choices=[
            ("positive", "Positive"),
            ("neutral", "Neutral"),
            ("negative", "Negative"),
        ],
        default="neutral",
    )
    sentiment_positive = models.FloatField(default=0.0)
    sentiment_neutral = models.FloatField(default=0.0)
    sentiment_negative = models.FloatField(default=0.0)

    # Tags for articles
    tags = TaggableManager()  # Add the TaggableManager field for tags

    def __str__(self):
        return self.title

    def update_coverage_data(self, left_sources, right_sources, total_sources):
        """
        Update coverage data based on external API response.
        """
        self.total_sources = total_sources
        self.save(update_fields=["total_sources"])

    def update_sentiment_data(self, sentiment, positive, neutral, negative):
        """
        Update sentiment data based on external API response.
        """
        self.sentiment = sentiment
        self.sentiment_positive = positive
        self.sentiment_neutral = neutral
        self.sentiment_negative = negative
        self.save(
            update_fields=[
                "sentiment",
                "sentiment_positive",
                "sentiment_neutral",
                "sentiment_negative",
            ]
        )


class ReaderProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    categories = models.ManyToManyField(NewsCategory, blank=True)
    saved_articles = models.ManyToManyField(NewsArticle, blank=True)
