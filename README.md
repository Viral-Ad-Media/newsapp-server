# NewsApp Backend

Django REST API for AbokiNews: news ingestion, paginated feeds, categories, source coverage, reader preferences, saved stories, and questions grounded in stored news.

Companion frontend: [Viral-Ad-Media/my-news-app](https://github.com/Viral-Ad-Media/my-news-app).

## Requirements

- Python 3.11 or 3.12 (both covered by CI)
- Django 5.2 LTS and dependencies in `requirements.txt`
- SQLite for local development; a durable PostgreSQL database for production
- At least one news-provider key to ingest stories

## Local setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env: keep DEBUG=True locally and set a random SECRET_KEY.
python manage.py migrate
python manage.py createsuperuser  # optional
python manage.py runserver
```

API: `http://localhost:8000/api/`. Admin: `http://localhost:8000/admin/`.
Start the companion frontend at `http://localhost:3000`.

## Configuration

Use `.env` for local values and environment variables on the production host. Keep credentials out of version control.

| Variable | Purpose |
| --- | --- |
| `DEBUG` | `True` locally; `False` in production. Defaults to `False`. |
| `SECRET_KEY` | Long random Django secret; required when `DEBUG=False`. |
| `ALLOWED_HOSTS` | Comma-separated backend hostnames, without schemes or paths. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins including scheme and port, such as `http://localhost:3000` or your HTTPS domain. Also used for trusted CSRF origins. |
| `DATABASE_URL` | PostgreSQL connection URL. Set as a runtime environment variable; current settings read it through `os.getenv`, rather than loading this value from `.env`. Without it, SQLite is used. |
| `NEWS_API_KEY` | NewsAPI credential; enables this ingestion provider. |
| `NEWS_DATA_IO_API_KEY` | NewsData credential; enables this ingestion provider. |
| `OPENAI_API_KEY` | Optional; enables generated answers using stored excerpts. |
| `OPENAI_SUMMARY_MODEL` | Model used by AI helpers; default `gpt-4o-mini`. |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | Optional media configuration; all three are needed to enable Cloudinary storage. |
| `SECURE_SSL_REDIRECT` | Defaults to enabled outside debug mode. Configure HTTPS proxy forwarding correctly. |

The Linux dependency install includes `psycopg2-binary`. For PostgreSQL development on another platform, install a compatible PostgreSQL driver separately; SQLite needs no additional driver.

## API reference

All paths below are relative to `/api/`. Reader endpoints require `Authorization: Bearer <access-token>`.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `news/` | Paginated news, newest publication first. |
| GET | `news/<id>/` | Article detail; missing IDs return 404. |
| GET | `categories/` | Paginated categories with `article_count`. |
| GET | `categories/<id>/` | Category metadata. |
| GET | `category/<id>/` | Alternate category metadata route; does not return articles. |
| GET | `news/<id>/related/` | Up to four related stories, with latest-story fallback; excludes the current story. |
| GET | `news/<id>/coverage/` | Object containing source articles and stored sentiment metadata. |
| GET | `feed/` | Reader feed filtered by selected categories; all news when no categories are selected. |
| GET, PUT | `preferences/` | Read or replace category IDs using `{"categories": [1, 2]}`. |
| GET | `saved/` | Paginated reader's saved stories. |
| GET, PUT, DELETE | `saved/<id>/` | Check saved status, save a story, or unsave it. DELETE returns 204. |
| GET | `saved-status/?ids=1,2` | Batch status, returning `{"saved_ids": [1]}`; at most 100 IDs. |
| POST | `ask/` | Ask a question using `{"question": "What are the latest headlines?"}`; returns `answer` and `sources`. This backend endpoint allows anonymous access. |
| POST | `token/` | Username/password login; returns access and refresh JWTs. |
| POST | `token/refresh/` | Refresh access using `{"refresh": "<refresh-token>"}`. |
| GET | `auth/user/` | Authenticated reader profile. |
| POST | `auth/registration/` | Signup using `username`, `password1`, and `password2`. |

Additional authentication routes are supplied by `dj-rest-auth` under `auth/` and allauth under `/accounts/`.

### Filtering and pagination

`news/`, `feed/`, and `saved/` support:

| Parameter | Meaning |
| --- | --- |
| `category_id` | Exact category ID. |
| `category` | Case-insensitive substring of a category name. |
| `search` | Case-insensitive title/description search, limited to 200 characters. |
| `location` | Case-insensitive location substring; ingested country codes include `ng`, `us`, and `gb`. |
| `filter` | `last_day`, `last_week`, or `last_month` (30 days). |
| `page`, `page_size` | Page number and size; default 20, maximum 100. Also supported by category lists. |

```bash
curl 'http://localhost:8000/api/news/?location=ng&category_id=1&page_size=20'
```

Paginated lists return:

```json
{"count": 1, "next": null, "previous": null, "results": [{"id": 1, "title": "Example headline"}]}
```

Article fields include `description`, its `summary` alias, `content`, `article_url`, `image_url`, `published_at`, nested `source` and `categories`, tags, and stored sentiment fields. Images use a configured Cloudinary image or the upstream image URL. Category lists return counts rather than embedding all article IDs; use `news/?category_id=<id>` to retrieve articles.

Coverage returns an object, not an array:

```json
{
  "total_sources": 1,
  "articles": [{"id": 1, "title": "Example headline", "source": "Example Source", "url": "https://example.com/story", "published_at": "2026-10-05T12:00:00Z"}],
  "sentiment_stats": {"positive": 0, "neutral": 0, "negative": 0},
  "sentiment": "neutral"
}
```

The source count uses the stored count or one known source; `articles` currently contains the stored story's source. Zero sentiment values do not establish a measured distribution. Political-bias classification and a comprehensive multi-source comparison are not implemented.

### Authentication and limits

Access JWTs last 60 minutes; refresh JWTs last one day. The companion frontend keeps them in HttpOnly cookies and refreshes access server-side. Backend signup currently does not require email verification.

Default API throttles are 120 requests/minute for anonymous clients and 240/minute for authenticated readers. `ask/` uses its own 10/minute throttle. Questions are limited to 500 characters and up to five stored excerpts. When OpenAI is unconfigured or unavailable, the answer explicitly reports unavailability and lists stored headlines; it does not claim live news access.

## News ingestion

```bash
python manage.py fetch_news --country ng
```

The default country is `ng`. Set `NEWS_API_KEY`, `NEWS_DATA_IO_API_KEY`, or both. The command runs each configured provider, normalizes nullable data and country names, preserves valid provider publication times, and stores original story/image URLs. Stories are updated or created by title and source; valid categories and tags are retained. Output includes created, updated, and skipped counts.

Missing credentials or provider errors produce a nonzero exit code without logging credential-bearing URLs. If one provider fails, stories imported from another provider can still be saved. Scheduling is external: configure a cron job or hosting scheduler to invoke the command at an interval allowed by your provider plan.

## Validation

```bash
DEBUG=True python manage.py check
DEBUG=True python manage.py makemigrations --check --dry-run
DEBUG=True python manage.py test
```

CI runs checks and tests on Python 3.11 and 3.12. The merged repair was validated with 17 Django tests, a fresh database migration, and companion frontend integration checks. See the frontend README for the local smoke test.

## Deployment and migration

Deploy this backend and apply its migrations before deploying the companion frontend.

1. Back up the production database and inspect migration records.
2. Configure `DEBUG=False`, a random `SECRET_KEY`, runtime `DATABASE_URL`, backend `ALLOWED_HOSTS`, and the frontend HTTPS origin in `CORS_ALLOWED_ORIGINS`.
3. Install dependencies and run:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
```

4. Start the web service using the existing Procfile command:

```bash
gunicorn newsapp.wsgi --log-file -
```

WhiteNoise serves collected static files. The proxy must supply the correct `X-Forwarded-Proto` header for HTTPS. Configure provider keys and schedule ingestion if the service should refresh news automatically.

**Existing database migration caveat:** the original `0001_initial` was restored from repository history because an overwritten version was incompatible with migrations 0002–0010. Migration 0011 adds reader profiles, upstream article/image URLs, and publication-time support. If an existing database used the overwritten snapshot and faked later migrations, compare its actual schema with migration records before rollout. Do not delete the database or reset migration history to bypass this review.

## Troubleshooting

- **Startup requires `SECRET_KEY`:** set a production secret or use `DEBUG=True` for local development.
- **Local HTTPS redirect:** confirm `DEBUG=True`; inspect `SECURE_SSL_REDIRECT` and proxy headers.
- **Empty feeds:** run ingestion, confirm provider permissions/country support, and inspect active filters and reader preferences.
- **401 from reader routes:** use an unexpired access JWT or refresh it; the frontend handles refresh through its own session routes.
- **Signup/CORS failures:** add the exact frontend origin, including local port, to `CORS_ALLOWED_ORIGINS`.
- **Provider failures:** check configured keys, quotas, supported country, and network access. Credentials are intentionally excluded from error output.
- **Migration failure on an existing database:** inspect the schema/history caveat above before changing migration state.
