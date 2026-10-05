from news.models import NewsArticle


def fetch_news_data(article_id):
    """Return stored coverage; no verified external bias provider is configured."""
    return NewsArticle.objects.filter(pk=article_id).first()
