# Private admin implementation and operations

> Full-article update: [source-grounded article policy](full-articles.md) supersedes the summary-only publishing behavior described below. Existing summaries stay unchanged. Migration 0019, a configured writing key and a worker are required for upgrades.

## Existing system and implementation

Frontend: Next.js 15 App Router, React 19, existing light/dark color
variables and orange/cream accents, HttpOnly JWT session proxy and `/superadmin` editorial console.
Backend: Django 5.2, DRF, Django ORM, existing staff/superuser roles, JWT,
NewsArticle/NewsCategory/NewsSource, RSS/Atom parser and provider CLI commands.
Production configuration points at Vercel and Neon PostgreSQL; a worker host is
not configured in this checkout. Local development can use SQLite.

The implementation extends these tables and the RSS parser, adds a persistent
queue/configuration/run/audit system, and adds six private sections. The original
reader homepage, category routes, source attribution, immediate publishing and SEO
metadata preparation are retained. Archived articles are excluded by the public
article manager, including public details, feeds, RSS and sitemap. Category counts
exclude archived articles. There is no draft/review gate in the existing model;
publication controls are Publish (restore) and Archive. Category activation and
full-text scraping are not introduced.

## Routes and endpoints

Frontend routes: `/admin/login`, `/admin`, `/admin/articles`, `/admin/categories`,
`/admin/sources`, `/admin/logs`, `/admin/settings`. Protected server layouts call
Django authorization before rendering. Admin navigation is separate from public
navigation. An unavailable backend produces an admin-only error state.

Same-origin `/api/admin/*` forwards to backend `/api/admin/*` using HttpOnly
sessions, refreshes JWTs, disallows cross-origin mutations and disables caching.
No browser-stored bearer tokens, credentials or provider keys are returned.

| Backend route (under `/api/admin/`) | Methods and purpose |
| --- | --- |
| `me/` | GET authorization and delete capability |
| `dashboard/` | GET real counts, recent activity and run state |
| `articles/`, `articles/<id>/` | GET paginated lists/detail, POST create, PUT validated edit, DELETE superuser only |
| `categories/`, `categories/<id>/` | GET, POST, PUT; DELETE only when unused, superuser only |
| `sources/`, `sources/<id>/` | GET, POST, PUT feed configuration and actual health |
| `sources/<id>/test/` | POST durable connection/validation test without publishing, including disabled sources |
| `runs/` | GET paginated history; POST queue full run (`dry_run: true` supported) |
| `runs/<id>/` | GET run detail; POST queue fetching for source ID (when enabled) |
| `logs/` | GET paginated source, publishing, crawler and audit events |
| `settings/` | GET saved config and connectivity; PUT validated updates with revision |

Staff or superuser authorization is checked by Django on every operation. Reader
registration cannot set these role flags. Login is throttled to five attempts per
minute per client IP by the existing DRF scoped throttle. Deploy an edge rate limit
for distributed deployments because Django's default local cache is per process.
Legacy superadmin role changes and permanent deletions now require superuser.
Legacy ingestion actions queue the worker instead of executing sample imports.

Articles: search title/summary, status/category/source/date filtering, title/addition/
publication ordering, pagination capped at 100, duplicate IDs from URL or same
source/headline, public/original links, revision protection. Imported source fields
are read-only. Category names/slugs are validated; unique slugs and unique feed
URLs have DB constraints. Existing categories may have null slugs; editing assigns
one without changing public numeric category URLs. Existing attribution-only
sources stay disabled until a permitted feed is configured.

Logs support date, severity, type, source, run and status filters and search.
Run histories carry duration and source/fetch/invalid/duplicate/create/publish/
failed-source counts. General web-server logs remain in the hosting log service;
the panel displays persisted crawler, publication and administrative events.

## Migrations and safe rollout

New migrations: `0017_private_admin_automation`, `0018_worker_lock`. They add fields
and models without deleting data. Apply to a fresh development database first.
Do not reset tables, fake migrations or point testing commands at production.

The existing uncommitted migrations 0014–0016 predate this work and **0016 removes
old publishing fields and the old PublicationEvent model**. Review those separately
and back up production before approving a migration rollout. No migration has been
applied to your configured local or production database by this implementation;
all executed migration tests used disposable databases. The existing Vercel build
hook applies pending migrations, so review the whole migration chain before any
future production deployment.

## Local setup and account provisioning

Use an isolated development database (empty `DATABASE_URL` uses local SQLite):

```bash
cd newsapp-server
source ../.venv/bin/activate
# Configure .env for DEVELOPMENT only. Do not reuse production credentials.
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Create the administrator interactively; no passwords are shipped. Django's existing
`/admin/` on the backend manages staff/role flags. Ordinary staff can administer
news; superusers can permanently delete unused categories/articles and change roles.
The frontend login is on its own origin at `/admin/login`.

```bash
cd my-news-app
# .env.development.local:
# API_SERVER_URL=http://127.0.0.1:8000/api
# NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api
npm run dev
```

Start a second backend terminal with the same development database:

```bash
python manage.py run_news_worker
```

Add a permitted public HTTPS RSS/Atom feed in Sources, choose a default category,
and run Test feed. Saving checks URL policy; health stays Untested until an actual
successful check. Test without publishing on Dashboard queues a dry run. Inspect
its run counts in Logs before enabling publishing. Dry runs report eligible
records separately and keep actual created/published counts at zero. Run now performs a real import.
The safe manual test needs no rapid production schedule. Supported interval is
60–1440 minutes; tests move the clock/due time instead of changing production limits.

## Hourly scheduler and deployment

Do not run a perpetual loop inside Vercel requests. Deploy a separate persistent
Python worker using the same backend code, environment and durable PostgreSQL
as the web API:

```bash
python manage.py run_news_worker
```

Alternative: an external managed scheduler invokes
`python manage.py run_news_worker --once` every minute on a persistent job runner.
It processes at most one queued/due run; the DB settings enforce the 60-minute
default interval. No unauthenticated HTTP cron endpoint or shared cron secret is
needed. More frequent worker ticks improve manual-job latency without increasing
the news publishing frequency. Configure restart-on-failure on the worker host.

The worker polls every ten seconds. Automation settings are persistent, default
disabled, and schedule intervals are measured from run start. A disabled schedule
prevents future scheduled jobs; explicitly requested manual jobs still run. Restart
performs one overdue execution rather than replaying every missed hour.

A separate singleton row is locked with `SELECT FOR UPDATE` for the entire bounded
run. PostgreSQL fences concurrent worker instances, including managed invocations,
and releases the lock on process/connection loss. Imports and run history commit
together; an interrupted worker rolls back to the durable queued job, which can be
retried. Settings use another row, so they remain editable during network work.
Queued status is visible while the worker transaction executes; final results and
start/finish times become visible on commit. The UI labels that state as queued or
executing. There is no fabricated intermediate progress percentage.

Use PostgreSQL for production; SQLite is for a **single local worker only**, since
it does not implement row-level `SELECT FOR UPDATE`. Prefer the direct Neon
connection on the worker and configure transaction/hosting timeouts above the
bounded run duration. Network work holds a DB transaction deliberately for safe
restart fencing; max 20 enabled feeds, 100 entries/feed, 2 MiB responses and 2–15s
socket limits bound work. The worker is sequential (concurrency 1). Review database
connection budgets before scaling web and worker instances.

Configure backend `SECRET_KEY`, `DEBUG=False`, `DATABASE_URL`, `ALLOWED_HOSTS`,
`CORS_ALLOWED_ORIGINS`, and HTTPS proxy headers. Frontend `API_SERVER_URL` must
reach the correct backend; `NEXT_PUBLIC_API_BASE_URL` serves public reads.
Production frontend cookies are Secure, HttpOnly, SameSite=Lax. Keep all provider,
Google, database and service secrets server-only. No additional service credential
is required for public RSS; publisher permission still must be established.

Verify deployment by logging in, checking Settings for database connected and a
recent worker heartbeat, testing a feed without publishing, checking persistent
run results and then enabling automation. The next run timestamp is a due time,
not evidence that the worker host is currently running. Web liveness alone does
not validate the scheduler.

## Ingestion and security rules

The existing RSS parser normalizes entries. Scheduled import validates headline,
summary, HTTP(S) source link, valid publication date and future timestamps. It
stores bounded summary text and a source link, never full publisher articles,
synthetic source perspectives, made-up bylines or fabricated sentiment scores.
Canonicalization removes fragments and known tracking parameters, preserves other
query parameters, and computes a unique URL hash. Existing matching URL or same
source/title is skipped, including archived articles. Admin changes are not
replaced during repeated RSS ingestion. Provider CLI updates also skip records
that have an admin revision or are archived. Existing historical duplicates are
not merged or deleted; the article table identifies candidates for review.

Source URLs allow public HTTPS on port 443, with no query string, embedded
credentials, fragment or redirects. DNS resolves once; **all** returned IPs must
be global. The TLS connection is pinned to the checked IP while verifying the
original hostname. Proxy environment variables are not used. Requests disable
redirects, require successful status, limit time/body size and reject XML entities
and compressed responses. Configurations requiring redirects, private feeds or
credential query strings must be adapted by the source owner; no permissive
bypass is exposed. Each failed source is recorded without blocking other feeds.
Errors stored in logs are sanitized and never include remote exception strings.

Admin audit and run events have 90-day retention, cleaned on completed worker
runs. Provider API credentials are not accepted in source forms. Existing NewsAPI/
NewsData commands remain CLI integrations; scheduled source management in this
implementation supports RSS/Atom. Category activation, arbitrary schedule
expressions, provider-specific API configuration and a new user-management UI
are intentionally absent; the existing Django admin handles accounts.

## Tests and remaining operational checks

```bash
cd newsapp-server
DATABASE_URL='' DEBUG=True ../.venv/bin/python manage.py test --noinput
DATABASE_URL='' DEBUG=True ../.venv/bin/python manage.py check
DATABASE_URL='' DEBUG=True ../.venv/bin/python manage.py makemigrations --check --dry-run
cd ../my-news-app
node --test tests/*.test.cjs
npm run build
CHROME_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npx playwright test --config playwright.admin.config.ts
```

Django test DBs are temporary. The browser harness creates a new SQLite DB in a
temporary directory and explicitly overrides all `.env` database settings; its
static test-only passwords exist only in that disposable process. Unit crawler
tests mock network responses to avoid real publishing or paid-provider usage.

Live publisher permission/reachability, PostgreSQL multi-process lock behavior,
production credentials, worker provisioning and deployed CORS/cookie behavior
require staging verification. They have not been claimed as successful here. No
push, deployment, production publishing or existing database migration was run.

Troubleshooting: queued jobs that never finish usually mean no worker, a DB
mismatch, a stopped process, or a transaction/network timeout. Check heartbeat and
worker-host logs. Failed feed tests can indicate private DNS, redirects, malformed
XML, size limits, rate limiting or publisher restrictions. Enable only a feed that
has passed a genuine test. A 409 on edits means refresh the article/settings before
saving; deleting an in-use category requires explicit reassignment first.

## Exact files touched by this admin implementation

Backend new files:
- `news/admin_api.py`
- `news/automation.py`
- `news/safe_fetch.py`
- `news/management/commands/run_news_worker.py`
- `news/migrations/0017_private_admin_automation.py`
- `news/migrations/0018_worker_lock.py`
- `news/test_admin.py`
- `docs/admin-panel.md`

Backend extended files (pre-existing working-tree edits preserved):
- `news/models.py`
- `news/serializers.py`
- `news/urls.py`
- `news/views.py`
- `news/utils/rss_ingestor.py`
- `news/management/commands/fetch_news.py`
- `news/management/commands/fetch_rss.py`
- `news/test_publishing_migration.py` (migrate to current leaf before current-model assertions)
- `newsapp/settings.py`
- `README.md`

Frontend new files:
- `src/app/admin/layout.js`
- `src/app/admin/admin.css`
- `src/app/admin/login/page.js`
- `src/app/admin/(protected)/layout.js`
- `src/app/admin/(protected)/page.js`
- `src/app/admin/(protected)/[section]/page.js`
- `src/app/api/admin/[...path]/route.js`
- `src/components/AdminPanel.js`
- `src/components/AdminShell.js`
- `src/components/SiteChrome.js`
- `playwright.admin.config.ts`
- `tests/admin-e2e-backend.py`
- `tests/admin-ui/admin.spec.ts`

Frontend extended files: `src/app/layout.tsx`, `src/app/robots.js`, `src/components/SuperadminDashboard.js` (ingestion links), `README.md`.
Other dirty/untracked files in both repositories already existed when inspection
began and were not reverted or staged. Branches remain frontend `main`, backend
`taiye-dev`. No dependencies were added.


## Executed verification (October 26, 2026)

- Full Django suite: **81 passed** on a temporary SQLite database, including 18
  new admin/automation/security cases and the existing publishing migration test.
- Frontend unit suite: **13 passed** (`node --test tests/*.test.cjs`).
- Existing public/reader/editor browser suite: **42 passed** using local fixtures.
- New browser suite with an actual disposable Django server: **3 passed**,
  including protected-route redirects, reader denial, HttpOnly cookies, category/
  source/article persistence, archive, settings persistence, durable queue, logout,
  and dashboard widths 390/768/1440.
- Production Next build passed (compilation, lint/types, route generation).
- Django system check and migration consistency check passed.
- Git whitespace checks passed. Initial failures uncovered a historical migration
  test assumption, form accessibility, blank-category handling and test selector
  ambiguity; these were corrected before the passing runs above.

Warnings: Django notes that collected static files are absent in the disposable
local test environment. Run the documented collectstatic deployment step on the
real backend. No live publisher requests or PostgreSQL concurrency test were run.


Reader navigation, strict time windows, Ask Aboki response modes and the complete
email-reset flow are documented in [reader-experience.md](reader-experience.md).
