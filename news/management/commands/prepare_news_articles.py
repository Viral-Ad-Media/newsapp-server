from django.core.management.base import BaseCommand
from news.models import NewsArticle
from news.article_writing import article_quality


class Command(BaseCommand):
    help = "Audit article quality, or queue durable full-story upgrades. Audit is read-only by default."

    def add_arguments(self, parser):
        parser.add_argument(
            "--queue",
            action="store_true",
            help="Queue upgrades; requires migration 0019 and a worker.",
        )
        parser.add_argument("--article", type=int)

    def handle(self, *args, **options):
        from news.article_jobs import queue_article

        articles = NewsArticle.objects.order_by("pk")
        if options["article"]:
            articles = articles.filter(pk=options["article"])
        count = 0
        for article in articles.iterator():
            report = article_quality(article.content)
            if not report["passed"]:
                count += 1
                if options["queue"]:
                    job = queue_article(article.pk)
                    self.stdout.write(
                        f"Article #{article.pk}: preparation #{job.pk} {job.status}"
                    )
                else:
                    self.stdout.write(
                        f'Article #{article.pk}: {report["word_count"]} words; upgrade needed'
                    )
        self.stdout.write(
            f"{count} articles need preparation. Summaries and URLs unchanged."
        )
