import signal
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from news.automation import tick
from news.article_jobs import tick_article


class Command(BaseCommand):
    help = "Process the durable news queue and hourly schedule. Deploy as a separate persistent worker."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one queued or due run, then exit (cron).",
        )

    def handle(self, *args, **options):
        stopped = False

        def stop(*_):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopped:
            try:
                run = tick()
                if run:
                    self.stdout.write(f"Run {run.pk}: {run.status}")
                prepared = tick_article()
                if prepared:
                    self.stdout.write(f"Article preparation {prepared.pk}: {prepared.status}")
            except Exception:
                self.stderr.write(
                    "Worker tick failed; check database connectivity and migrations."
                )
                if options["once"]:
                    raise
            finally:
                close_old_connections()
            if options["once"]:
                return
            for _ in range(10):
                if stopped:
                    break
                time.sleep(1)
