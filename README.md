# NewsApp Backend

NewsApp is a Django REST backend for aggregating and serving news content with category filters, related-story lookup, sentiment metadata, and JWT-based authentication.

## Table of Contents

1. Overview
2. Features
3. Tech Stack
4. Project Structure
5. Data Model
6. API Reference
7. Authentication
8. Environment Variables
9. Local Development Setup
10. Running Tests
11. Background Data Fetching
12. Deployment Notes
13. Troubleshooting

## Overview

This project exposes REST endpoints for:

- Listing and filtering news articles
- Fetching single article details
- Returning sentiment/coverage metadata for an article
- Returning related articles by shared category
- Listing categories

It uses:

- Django + Django REST Framework
- `dj-rest-auth` / `allauth` / `simplejwt` for auth
- Cloudinary for media storage (optional)
- SQLite locally, PostgreSQL in production via `DATABASE_URL`

## Features

- News article listing with filters:
  - `category`
  - `location`
  - `filter` (`last_day`, `last_week`, `last_month`)
- Related article endpoint with fallback logic
- Coverage/sentiment endpoint
- Category endpoints via DRF router and direct detail route
- JWT token issue/refresh endpoints
- Admin interface for `NewsSource`, `NewsCategory`, and `NewsArticle`

## Tech Stack

- Python
- Django `4.2.x` on Python `<3.10`, Django `5.1.x` on Python `>=3.10`
- Django REST Framework
- django-allauth
- dj-rest-auth
- djangorestframework-simplejwt
- django-taggit
- requests
- cloudinary + django-cloudinary-storage
- gunicorn + whitenoise

## Project Structure

```text
newsapp/
├── manage.py
├── Procfile
├── requirements.txt
├── news/
│   ├── models.py
│   ├── serializers.py
│   ├── views.py
│   ├── urls.py
│   ├── tests.py
│   ├── admin.py
│   ├── management/commands/fetch_news.py
│   ├── api_integration/api_integration.py
│   └── utils/ai_summarizer.py
└── newsapp/
    ├── settings.py
    ├── urls.py
    ├── wsgi.py
    └── asgi.py
```

## Data Model

### `NewsSource`

- `name` (char)
- `url` (URL)

### `NewsCategory`

- `name` (char)
- `image` (Cloudinary field, optional)

### `NewsArticle`

- `title`, `author`, `description`, `content`
- `image` (Cloudinary field, optional)
- `categories` (many-to-many to `NewsCategory`)
- `location`
- `published_at`
- `source` (foreign key to `NewsSource`)
- Coverage field:
  - `total_sources`
- Sentiment fields:
  - `sentiment`
  - `sentiment_positive`
  - `sentiment_neutral`
  - `sentiment_negative`
- Tags:
  - `tags` (`django-taggit`)

## API Reference

Base path: `/api/`

### Health of Core News Endpoints

1. `GET /api/news/`
2. `GET /api/news/<pk>/`
3. `GET /api/category/<category_id>/`
4. `GET /api/categories/`
5. `GET /api/categories/<pk>/`
6. `GET /api/news/<article_id>/coverage/`
7. `GET /api/news/<article_id>/related/`

### `GET /api/news/` Query Parameters

- `category` (string, case-insensitive category name match)
- `location` (string, case-insensitive location match)
- `filter`:
  - `last_day`
  - `last_week`
  - `last_month`

Example:

```bash
curl "http://127.0.0.1:8000/api/news/?category=Politics&filter=last_week"
```

### Example Article Response (shape)

```json
{
  "id": 1,
  "title": "Example headline",
  "author": "Reporter",
  "description": "Short summary",
  "content": "Full article body...",
  "image": "cloudinary-field-value",
  "image_url": "https://res.cloudinary.com/...",
  "categories": [
    { "id": 2, "name": "Politics", "image_url": "https://..." }
  ],
  "location": "US",
  "published_at": "2026-02-24T12:00:00Z",
  "source": {
    "id": 1,
    "name": "Example Source",
    "url": "https://example.com"
  },
  "total_sources": 0,
  "sentiment": "neutral",
  "sentiment_positive": 0.0,
  "sentiment_neutral": 0.0,
  "sentiment_negative": 0.0,
  "tags": ["world", "policy"]
}
```

## Authentication

Configured auth routes:

- `POST /api/token/` (obtain access/refresh JWT pair)
- `POST /api/token/refresh/` (refresh access token)
- `GET|POST /api/auth/...` (`dj-rest-auth` endpoints)
- `POST /api/auth/registration/...` (registration endpoints)
- `/accounts/...` (allauth routes)

Default API auth class:

- `rest_framework_simplejwt.authentication.JWTAuthentication`

## Environment Variables

Create a `.env` file in project root.

Required for production:

```env
SECRET_KEY=change-me
DEBUG=False
ALLOWED_HOSTS=newsapp-najw.onrender.com,example.com

DATABASE_URL=postgresql://user:password@host:5432/dbname

NEWS_API_KEY=your_newsapi_key
NEWS_DATA_IO_API_KEY=your_newsdata_key

OPENAI_API_KEY=your_openai_key
OPENAI_SUMMARY_MODEL=gpt-4o-mini

CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_cloudinary_key
CLOUDINARY_API_SECRET=your_cloudinary_secret
```

Notes:

- If `DATABASE_URL` is not set, local SQLite (`db.sqlite3`) is used.
- If Cloudinary vars are missing, Cloudinary storage is not enabled.
- Keep `DEBUG=True` in local development to avoid forced HTTPS redirect behavior.

## Local Development Setup

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure environment

```bash
touch .env
# then populate it with the variables from the "Environment Variables" section
```

### 4. Run migrations

```bash
python manage.py migrate
```

### 5. Create superuser (optional)

```bash
python manage.py createsuperuser
```

### 6. Start development server

```bash
python manage.py runserver
```

Server URL:

- `http://127.0.0.1:8000/`

Admin URL:

- `http://127.0.0.1:8000/admin/`

## Running Tests

Project tests currently live in `news/tests.py`.

```bash
python manage.py test
```

Or app-specific:

```bash
python manage.py test news
```

## Background Data Fetching

Primary management command:

```bash
python manage.py fetch_news
```

What it does:

- Pulls top headlines from NewsAPI (`country=us`)
- Creates/updates `NewsSource`
- Upserts `NewsArticle` by `(title, source)`
- Prints created/updated counts

Prerequisite:

- `NEWS_API_KEY` must be configured

## Deployment Notes

- `Procfile` uses:

```bash
web: gunicorn newsapp.wsgi --log-file -
```

- Static files served via WhiteNoise middleware.
- SSL redirect is enabled automatically when `DEBUG=False`.
- PostgreSQL is expected in production via `DATABASE_URL`.

## Troubleshooting

### `ModuleNotFoundError: No module named 'django'`

- Activate virtual environment:

```bash
source .venv/bin/activate
```

- Reinstall dependencies:

```bash
pip install -r requirements.txt
```

### Redirect loop / HTTPS issues in local dev

- Ensure:

```env
DEBUG=True
```

### News fetch command returns API errors

- Verify:
  - `NEWS_API_KEY` is set
  - Network egress is available from your runtime
  - Your API key has not exceeded rate limits

### `pg_config executable not found` during install

- This project now installs `psycopg2-binary` only on Linux by default.
- On macOS local development, SQLite is used unless you explicitly configure PostgreSQL.
- If you need PostgreSQL on macOS, install libpq and add `pg_config` to PATH before installing `psycopg2-binary`.
