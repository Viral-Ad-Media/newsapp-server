from django.db import models
from cloudinary.models import CloudinaryField
from taggit.managers import TaggableManager  # Import the TaggableManager for tagging

class NewsSource(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField()

    def __str__(self):
        return self.name


class NewsCategory(models.Model):
    name = models.CharField(max_length=100)
    image = CloudinaryField('image', blank=True, null=True)

    def __str__(self):
        return self.name


class NewsArticle(models.Model):
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField()
    content = models.TextField()
    image = CloudinaryField('image', blank=True, null=True)
    categories = models.ManyToManyField(NewsCategory, related_name='articles')
    location = models.CharField(max_length=100, blank=True, null=True)
    published_at = models.DateTimeField(auto_now_add=True)
    source = models.ForeignKey(NewsSource, on_delete=models.CASCADE)

    # Coverage-related fields
    total_sources = models.IntegerField(default=0)

    # Sentiment-related fields
    sentiment = models.CharField(
        max_length=10,
        choices=[('positive', 'Positive'), ('neutral', 'Neutral'), ('negative', 'Negative')],
        default='neutral'
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
