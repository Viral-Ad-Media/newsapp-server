from django.conf import settings
from django.utils import timezone
from django.db import models
from cloudinary.models import CloudinaryField
from taggit.managers import TaggableManager  # Import the TaggableManager for tagging


class NewsSource(models.Model):
    feed_url = models.URLField(max_length=2000, blank=True, null=True, unique=True)
    enabled = models.BooleanField(default=False)
    location = models.CharField(max_length=100, default="ng")
    default_category = models.ForeignKey("NewsCategory", null=True, blank=True, on_delete=models.SET_NULL)
    last_attempt = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    retrieved = models.PositiveIntegerField(default=0)

    name = models.CharField(max_length=255)
    url = models.URLField()
    bias = models.CharField(max_length=30, default="center", blank=True)

    def __str__(self):
        return self.name


class NewsCategory(models.Model):
    slug = models.SlugField(max_length=120, null=True, blank=True, unique=True)
    description = models.TextField(blank=True)

    name = models.CharField(max_length=100)
    image = CloudinaryField("image", blank=True, null=True)

    def __str__(self):
        return self.name


class PublishedArticles(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(archived=False)


class NewsArticle(models.Model):
    objects = PublishedArticles()
    all_objects = models.Manager()
    archived = models.BooleanField(default=False, db_index=True)
    imported = models.BooleanField(default=False)
    ingestion_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    revision = models.PositiveIntegerField(default=1)

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

    def clean(self):
        super().clean()
        if self.archived:
            return
        previous = type(self).all_objects.filter(pk=self.pk).first() if self.pk else None
        if previous is None or previous.archived or previous.content != self.content:
            from django.core.exceptions import ValidationError
            from .article_writing import article_quality
            report = article_quality(self.content)
            if not report['passed']:
                raise ValidationError({'content': report['errors']})

    def save(self, *args, **kwargs):
        from .publishing import populate_news_metadata

        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        changed = populate_news_metadata(self, previous)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | set(changed)
        super().save(*args, **kwargs)

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


class AutomationSettings(models.Model):
    # A singleton is shared by web requests and any number of workers.
    enabled = models.BooleanField(default=False)
    interval_minutes = models.PositiveIntegerField(default=60)
    max_per_source = models.PositiveIntegerField(default=20)
    timeout_seconds = models.PositiveIntegerField(default=10)
    next_run = models.DateTimeField(default=timezone.now)
    worker_seen = models.DateTimeField(null=True)
    revision = models.PositiveIntegerField(default=1)


class CrawlRun(models.Model):
    trigger = models.CharField(max_length=20)
    status = models.CharField(max_length=20, default="queued", db_index=True)
    source = models.ForeignKey(NewsSource, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    duration = models.FloatField(null=True)
    counts = models.JSONField(default=dict)
    error = models.CharField(max_length=300, blank=True)
    dry_run = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["status"], condition=models.Q(status="queued"), name="one_queued_crawl")]


class AdminEvent(models.Model):
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=30)
    severity = models.CharField(max_length=10, default="info")
    message = models.CharField(max_length=300)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    run = models.ForeignKey(CrawlRun, null=True, on_delete=models.CASCADE)
    source = models.ForeignKey(NewsSource, null=True, on_delete=models.SET_NULL)
    article = models.ForeignKey(NewsArticle, null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, blank=True)
    details = models.JSONField(default=dict)


class AutomationWorkerLock(models.Model):
    """Separate lock row keeps settings editable during bounded network work."""
    pass


class ArticlePreparation(models.Model):
    """Durable article upgrade and previous-body backup; never exposed in public API."""
    article = models.ForeignKey(NewsArticle, on_delete=models.CASCADE, related_name="preparations")
    status = models.CharField(max_length=20, default="queued", db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    expected_revision = models.PositiveIntegerField()
    original = models.JSONField(default=dict)
    evidence = models.TextField(blank=True)
    result = models.JSONField(default=dict)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["article"], condition=models.Q(status="queued"), name="one_queued_article_preparation")]
