"""Opt-in local-only profile; never connects to the .env Neon database."""

import os
import secrets
from pathlib import Path

os.environ["DEBUG"] = "True"
os.environ["DATABASE_URL"] = ""
# Use a separate development secret, not the deployed backend's secret.
local_dir = Path(__file__).resolve().parent.parent / ".local"
local_dir.mkdir(mode=0o700, exist_ok=True)
secret_file = local_dir / "django-secret"
try:
    descriptor = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(secrets.token_urlsafe(48))
except FileExistsError:
    pass
os.environ["SECRET_KEY"] = secret_file.read_text().strip()

from .settings import *  # noqa: E402,F403

LOCAL_DEMO = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": local_dir / "admin.sqlite3",
        "OPTIONS": {"timeout": 30},
    }
}
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
PUBLISHING_SITE_URL = "http://localhost:3000"
PASSWORD_RESET_FRONTEND_URL = PUBLISHING_SITE_URL
