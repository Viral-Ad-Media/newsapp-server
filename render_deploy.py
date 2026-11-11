"""Render lifecycle commands. Never seed data or reset users during deployment."""
import os
import subprocess
import sys
from urllib.parse import urlsplit

DEPLOY_LOCK = 726491082  # Shared with vercel_build.py during the handover.


def validate_environment(*, direct=False):
    for key in ("SECRET_KEY", "DATABASE_URL"):
        if not os.environ.get(key, "").strip():
            raise RuntimeError(f"Set {key} in the Render service environment.")
    key = "DATABASE_URL_UNPOOLED" if direct else "DATABASE_URL"
    value = os.environ.get(key, "").strip()
    url = urlsplit(value)
    if url.scheme not in ("postgres", "postgresql") or not url.hostname:
        raise RuntimeError(f"{key} must be a PostgreSQL URL.")
    if direct and "-pooler." in url.hostname:
        raise RuntimeError("DATABASE_URL_UNPOOLED must use the direct Neon endpoint.")
    return value


def migrate_database():
    # Select the direct connection before Django initializes its settings.
    os.environ["DATABASE_URL"] = validate_environment(direct=True)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "newsapp.settings")
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    try:
        if connection.vendor != "postgresql":
            raise RuntimeError("Render deployments require PostgreSQL.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [DEPLOY_LOCK])
            locked = cursor.fetchone()[0]
        if not locked:
            raise RuntimeError("Another database deployment is running. Retry later.")
        try:
            call_command("migrate", interactive=False)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [DEPLOY_LOCK])
    finally:
        connection.close()


def main(command):
    if command == "release":
        migrate_database()
    elif command == "build":
        validate_environment()
        subprocess.run([sys.executable, "manage.py", "check"], check=True)
        subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"], check=True)
    elif command == "serve":
        validate_environment()
        # Free web services have no pre-deploy command. The child uses the direct
        # URL; Gunicorn retains the parent's pooled URL after migrations succeed.
        subprocess.run([sys.executable, __file__, "release"], check=True)
        os.execvp("gunicorn", ["gunicorn", "newsapp.wsgi:application", "--bind",
                              "0.0.0.0:" + os.environ.get("PORT", "10000"),
                              "--workers", "2", "--threads", "2", "--timeout", "120",
                              "--access-logfile", "-", "--error-logfile", "-"])
    elif command == "worker":
        # A direct session is also appropriate for durable worker coordination.
        os.environ["DATABASE_URL"] = validate_environment(direct=True)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "newsapp.settings")
        import django
        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        try:
            executor = MigrationExecutor(connection)
            if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                raise RuntimeError("Deploy the web service migrations before starting the worker.")
        finally:
            connection.close()
        os.execvp(sys.executable, [sys.executable, "worker_supervisor.py"])
    else:
        raise RuntimeError("Expected build, release, serve, or worker.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) == 2 else "")
