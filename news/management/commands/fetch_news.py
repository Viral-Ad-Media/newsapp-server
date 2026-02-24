import requests
from django.core.management.base import BaseCommand
from decouple import config
from news.models import NewsArticle, NewsSource, NewsCategory

class Command(BaseCommand):
    help = 'Fetch news articles from multiple sources and save them to the database'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting news fetch...'))

        # Example for fetching from one API (repeat as needed for additional APIs)
        url = f'https://newsapi.org/v2/top-headlines?country=us&apiKey={config("NEWS_API_KEY")}'
        response = requests.get(url)

        if response.status_code == 200:
            articles = response.json().get('articles', [])
            self.save_articles(articles)
            self.stdout.write(self.style.SUCCESS('News fetch completed.'))
        else:
            self.stdout.write(self.style.ERROR(f"Failed to fetch news. Status code: {response.status_code}"))

    def save_articles(self, articles):
        for article in articles:
            source_name = article.get('source', {}).get('name', 'Unknown')
            title = article.get('title')
            description = article.get('description')
            content = article.get('content')
            image = article.get('urlToImage', '')

            if not title or not content:
                continue

            # Save or get the source
            source, created = NewsSource.objects.get_or_create(name=source_name)

            # Create the news article
            NewsArticle.objects.create(
                title=title,
                description=description,
                content=content,
                image=image,
                source=source,
                location=article.get('country', ''),  # Example location, update as needed
            )
            self.stdout.write(self.style.SUCCESS(f"Saved article: {title}"))
