# Private admin implementation and operations

> Full-article update: [source-grounded article policy](full-articles.md) supersedes the summary-only publishing behavior described below. Existing summaries stay unchanged. Migration 0019, a configured writing key and a worker are required for upgrades.

> Flexible automation update: migration `0020_publishing_limits` adds persistent publishing quotas and continuation batches. Settings now support 1–1000 minute intervals and 1–100 new articles per source per interval, with source/category rules. Apply this migration before restarting the updated API/worker.

## Existing system and implementation

Frontend: Next.js 15 App Router, React 19, existing light/dark color
variables and orange/cream accents, HttpOnly JWT session proxy and `/superadmin` editorial console.
Backend: Django 5.2, DRF, Django ORM, existing staff/superuser roles, JWT,
NewsArticle/NewsCategory/NewsSource, RSS/Atom parser and provider CLI commands.
Production configuration targets Vercel for the web API and a Vercel Daemon for
the worker, with shared Neon PostgreSQL. Local development can use SQLite.

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
`/admin/sources`, `/admin/rss`, `/admin/logs`, `/admin/settings`. Protected server layouts call
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
| `runs/<id>/cancel/` | POST cancel waiting/stopped work when no worker holds the execution lock; staff only |
| `logs/` | GET paginated source, publishing, crawler and audit events |
| `settings/` | GET saved config and connectivity; PUT validated updates with revision |
| `source-coverage/` | GET saved source availability for all 61 editions and actual published category counts |

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
1–1000 minutes; tests use disposable databases and mocked clocks/providers.

## Hourly scheduler and deployment

The production worker runs as a [Vercel Daemon](https://vercel.com/docs/daemons),
alongside the Django web API in the backend Vercel project. `vercel.json` declares
one `news-worker` instance in `iad1`, built from `Dockerfile.worker`. Set the
project Root Directory to `newsapp-server`. Choose a supported region close to
Neon before deployment if `iad1` is not appropriate for the database.

The container starts the existing persistent Python command:

```bash
python manage.py run_news_worker
```

Set the backend Production environment variables on Vercel, including
`DATABASE_URL`, `SECRET_KEY`, `DEBUG=False`, and the configured article-writing
provider credentials. The daemon inherits project environment variables and
must use the same durable database as the web API. Secrets and local SQLite
files are excluded from the container build context. The worker image does not
run migrations; the existing production build hook handles migration rollout.

Vercel Daemons are in Public Beta and run only in production deployments.
Deploying a preview does not start a worker. Vercel maintains the configured
instance count and restarts exited processes. Logs appear in the Vercel project
logs. The configured shutdown grace period is 3600 seconds so bounded work can
finish after SIGTERM; interrupted transactions still rely on database rollback
and retry. No HTTP liveness probe is configured because this command has no HTTP
listener; check the persisted worker heartbeat in Settings after deployment.
See the [daemon configuration reference](https://vercel.com/docs/daemons/config-reference).

For other hosting platforms, an external managed scheduler can invoke
`python manage.py run_news_worker --once` every minute on a persistent job runner.
It processes at most one queued/due run; the DB settings enforce the 60-minute
default interval. No unauthenticated HTTP cron endpoint or shared cron secret is
needed. More frequent worker ticks improve manual-job latency without increasing
the news publishing frequency. Do not run this alternative alongside the Vercel
daemon unless deliberately testing concurrent-worker fencing.

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
restart fencing; max 20 feeds per run, 100 entries/feed, 2 MiB responses and 2–15s
socket limits bound work. The worker is sequential (concurrency 1). Review database
connection budgets before scaling web and worker instances.

Collections may contain more than 20 enabled feeds. Each worker batch considers
up to 20 least recently attempted feeds, including never-attempted feeds first.
The existing model allowance remains two calls per batch. Candidate/evidence work
is separately limited to six items per batch and two per publisher; failed evidence
does not spend a model call. Unfinished sources are persisted in a queued
continuation and processed on later worker ticks within the same interval.
Completed batches and their quotas commit together, so a 100-story target does
not require one long transaction. Source-specific feed checks still target the
requested source, and dry runs use no model calls or persistent publishing quota.

## Publishing rules and flexible intervals

Dashboard **Run now** and **Test without publishing** stay clickable while a job
is pending; they are disabled only while submitting a request. Run now queues a
publishing run, while Test without publishing queues `dry_run=true` and cannot
create articles. A duplicate request returns a clear conflict naming the current
run, and the dashboard refreshes its queue state. Both still require the deployed
worker to execute; a missing recent heartbeat is shown explicitly.

Use **Cancel pending run** to clear a waiting or stopped job before choosing a
different action. This preserves run history, published articles and quota counts.
The backend must acquire the same worker execution lock without waiting before
cancelling, so it refuses to cancel a batch that is actually executing. This is
a staff-only POST action; reading a URL cannot cancel work. Cancelling a pending
job does not turn off scheduled automation. Admin requests also have a client
timeout so a stalled network request cannot leave the buttons busy indefinitely.

Settings is a dedicated control page with schedule, limits, source/category rules,
delivery totals, worker status and advanced feed controls. The scheduled automation
switch is orange when on and white when off; it is keyboard accessible and saves
with the other settings. The interval accepts any whole number from 1 to 1000
minutes, including 60 for hourly publishing.

With no custom rules, every enabled source uses **Default publish limit per
source** (1–100, default 2). When rules exist, **only listed sources and categories
participate**. Add several rows for different sources or categories. Each row has
`source`, `category` (null means all categories), and `limit` (1–100). A source's
combined configured limits cannot exceed 100. All-categories and category-specific
rows cannot be mixed for the same source, and duplicate source/category rows are
rejected. Sources must also have an enabled feed in Sources; a rule does not
silently enable a paused feed.

For one Sports story from Nairametrics per hour:

1. Enable the permitted Nairametrics feed in Sources.
2. In Settings, set **Interval minutes** to `60` and add a rule selecting
   Nairametrics, Sports, and a publish limit of `1`.
3. Add rows for any other sources/categories you want included. Unlisted sources
   stay out of scheduled publishing while rules are active.
4. Turn on **Scheduled automation enabled** and save. Check actual results in Logs.

Limits are ceilings, not guaranteed output. Topic matching uses publisher labels
and the source's configured fallback category; selecting Sports does not relabel
a Business story. Publication still requires enough matching new stories,
permitted source evidence, successful preparation and available processing time.
The advanced feed scan limit automatically increases when needed to accommodate
the selected publishing target, up to 100 entries per source.

`SourcePublishingWindow` stores successful counts by source/category, the interval
start, and bounded attempted-item hashes. Manual runs, retries and continuations
share those counters. Duplicates and failed items do not spend publishing quota;
failed item retrievals are not repeatedly retried in every batch of the same
window. Changing rules does not reset counters, and a hard ceiling of 100 new
articles per source per interval still applies across rule changes. A source can
renew its quota after the configured interval has elapsed. Manual runs and feed
checks do not postpone the saved scheduled due time.

Continuations carry the original cycle start and selected pending sources. An
expired continuation is cancelled instead of spilling its old workload into a new
interval. Disabling scheduled automation cancels future scheduled continuations;
an already executing batch can finish. Explicit manual jobs can still run while
automation is paused and obey the same quotas. Category deletion is blocked while
the category is referenced by a publishing rule.

Migration `0020` adds the configuration fields, quota ledger, and continuation
fields without deleting articles. Historical intervals above 1000 are normalized
to 1000 with a refreshed due time/revision. It has been applied to the isolated
local profile; production remains untouched. The production Vercel build's
existing migration hook will apply it before the updated worker starts.

## Private RSS, shared categories and checked source catalogue

RSS is an administrative tool at `/admin/rss`. Public header/footer links and
RSS autodiscovery metadata have been removed. `/rss` redirects into the protected
admin area. `/admin/rss.xml`, the legacy `/rss.xml`, and Django `/api/rss/` require
staff/superuser authorization on every request and return private, no-store
responses. Public news, category pages, saved stories and normal reader feeds
retain their existing access rules.

Public category navigation, live-news filters, topic preferences and admin forms
all read the saved category directory. Links use stable category IDs, so renaming
a category does not invalidate its category page. The configurator adds missing
standard topics without replacing existing category IDs, names, aliases or custom
topics. RSS imports now match explicit publisher topic labels to existing
categories, with the source's default category as fallback. Unknown publisher tags
do not create new categories automatically.

`news/data/rss_sources.json` contains 91 distinct feeds checked on November 2,
2026, spanning all 17 standard topics and all 61 country editions. Each included feed
had strict-parseable entries and at least one article sample with 400+ readable
words under the existing robots/security rules. Full check results, including
rejected URLs and rate limits, are in [rss-verification.json](rss-verification.json).
These are dated accessibility checks; they do not establish publisher permission
or guarantee future access to every article. No article generation or publishing
was performed during verification.

Add the configuration to an initialized database with:

```bash
python manage.py configure_news_sources --dry-run
python manage.py configure_news_sources
```

The Vercel production build runs the configurator after migrations and the
existing editorial seed commands. It is idempotent and preserves all existing
source edits, health results and enabled flags. New sources are disabled and
have no fabricated runtime health timestamps. No schema migration is needed for
this configuration update. For the isolated local database, append
`--settings=newsapp.local_settings` to both commands.

Review and enable selected sources in Sources, then use Test feed to check RSS
reachability through the deployed worker. For a read-only check of RSS plus up
to two full-article evidence samples, use:

```bash
python manage.py verify_news_source SOURCE_ID
```

This command makes no AI calls or database writes. Do not repeatedly probe a
publisher that is returning rate limits. Individual short, inaccessible or
unsupported articles remain blocked by the existing full-article policy.

Source edition choices use the same 61 countries as the frontend. General
international/topic feeds use `world`; a country-specific feed uses its country.
Explicit country tags in individual feed items can refine that assignment;
global articles are never duplicated into every country edition. Admin RSS shows
configured/enabled/recently checked feed counts and actual published stories per
category for the selected country. Zero counts are shown as coverage gaps.

The checked catalogue includes at least one country feed for all 61 frontend
editions, plus global topic feeds and independent publisher alternatives. A country
feed can contain several topics; this does not establish daily reporting in every
country/topic combination. Empty combinations remain visible as coverage gaps.
One publisher is not assumed to report every category in every country. The
verification log retains initial failures and the paced successful follow-up after
cooldown, including corrected Sri Lanka and UAE URLs.

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

## Editorial and reader upgrade (November 2, 2026)

The **Editorial** and **Coverage** workspaces, event following/comparison,
publisher preferences, article transparency and reading controls are documented in
[editorial-reader-upgrade.md](editorial-reader-upgrade.md), including migration,
rollout steps, operational boundaries and verification results.
