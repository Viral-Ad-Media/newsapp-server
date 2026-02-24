import requests
from .models import NewsArticle, NewsSource
from django.utils import timezone

def fetch_news_data(article_id):
    """
    Fetch news coverage details from multiple APIs and update NewsArticle and LopsidedStory models.
    """
    try:
        article = NewsArticle.objects.get(id=article_id)
    except NewsArticle.DoesNotExist:
        return None

    # Placeholder for API calls (implement actual API URLs and parameters)
    left_sources = 0
    right_sources = 0
    total_sources = 0

    # Example: Fetch data from each API
    external_apis = [
        'https://newsapi1.com/data',
        'https://newsapi2.com/data',
        # Add more APIs here
    ]

    for api in external_apis:
        response = requests.get(api, params={'query': article.title, 'published_at': article.published_at})
        if response.status_code == 200:
            data = response.json()
            for source in data.get('sources', []):
                if source['bias'] == 'left':
                    left_sources += 1
                elif source['bias'] == 'right':
                    right_sources += 1
                total_sources += 1

    # Update article coverage data
    article.update_coverage_data(left_sources, right_sources, total_sources)
    return article
