import os
from pathlib import Path
import signal
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connections
from news.automation import tick
from news.article_jobs import tick_article
from news.publishing_retries import tick_retries


class Command(BaseCommand):
    help = "Continuously process the durable news queue and configured minute interval. Deploy as a persistent worker."

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

        def progress():
            filename = os.environ.get('NEWS_WORKER_PROGRESS_FILE')
            if filename:
                Path(filename).touch()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopped:
            try:
                run = tick()
                progress()
                if run:
                    self.stdout.write(f"Run {run.pk}: {run.status}")
                retried = tick_retries()
                progress()
                if retried:
                    self.stdout.write(f"Retry {retried.pk}: {retried.status}")
                prepared = tick_article()
                progress()
                if prepared:
                    self.stdout.write(f"Article preparation {prepared.pk}: {prepared.status}")
            except Exception as exc:
                from news.worker_diagnostics import safe_worker_error
                self.stderr.write(safe_worker_error(exc))
                connections.close_all()
                if options["once"]:
                    raise
            finally:
                close_old_connections()
                progress()
            if options["once"]:
                return
            for _ in range(10):
                if stopped:
                    break
                time.sleep(1)
