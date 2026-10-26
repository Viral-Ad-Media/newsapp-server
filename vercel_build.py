"""Initialize the production PostgreSQL database before a Vercel deployment."""

import os

import django
from django.core.management import call_command
from django.db import connection

# Session-level advisory lock: serialize this app's production build jobs.
DEPLOY_LOCK = 726491082


def main():
    if os.environ.get("VERCEL_ENV") != "production":
        print("Skipping database changes outside Vercel production.")
        return
    if not os.environ.get("DATABASE_URL", "").strip():
        raise RuntimeError("Set DATABASE_URL on the Vercel production environment.")

    direct = os.environ.get("DATABASE_URL_UNPOOLED", "").strip()
    if not direct:
        raise RuntimeError(
            "Set DATABASE_URL_UNPOOLED to the direct Neon connection URL "
            "for production migrations."
        )
    # Only this build process uses the direct URL; runtime uses the pooled URL.
    os.environ["DATABASE_URL"] = direct
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "newsapp.settings")
    django.setup()
    if connection.vendor != "postgresql":
        raise RuntimeError("Production database setup requires PostgreSQL.")

    # Use the direct Neon URL for this process: a session lock cannot be held
    # reliably through a transaction-mode pooler across management commands.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [DEPLOY_LOCK])
        locked = cursor.fetchone()[0]
    if not locked:
        raise RuntimeError("Another database deployment is running. Retry this build.")
    try:
        call_command("migrate", interactive=False)
        call_command("seed_news")
        call_command("seed_blog_posts")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [DEPLOY_LOCK])
        connection.close()


if __name__ == "__main__":
    main()
