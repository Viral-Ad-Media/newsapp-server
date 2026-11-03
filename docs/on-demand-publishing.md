# Admin controls, scheduled publishing and reader languages

## Keep the existing admin layout

Dashboard retains News automation, recent activity and the latest articles table.
Articles retains the original article list and Create article editor. Sources
retains its existing table and per-source actions. The added publishing-mode
panels and bulk-settings sections have been removed at the operator's request.

Create article, Create category and Edit open the existing editor below the list,
scroll it into view and focus its first available field. Repeated clicks also
work. Focus happens synchronously on opening: background refreshes never move the
cursor while an operator is typing. Save/run/archive actions do not depend on
native confirmation popups. Deletion retains the in-page confirmation dialog.

## All sources and bulk source actions

Settings → Source & category rules uses the original dropdown, with **All sources**
in place of the empty Choose a source option. Category and quantity controls stay
as before. Save changes applies the rule. All sources is stored as source `0`,
resolved dynamically against every enabled RSS source, including sources enabled
later. It is not expanded into hundreds of rules. Use All sources rules or
individual-source rules, not both in one plan. All categories applies the chosen
1–100 limit separately to each matching category per source and interval. Individual category rules still total at most 100 per
source and interval. All sources does not enable paused feeds.

For example, All sources → All categories → limit 1 → interval 60 means up to one Sports, one World, one Entertainment and one
article in each other matching category from each enabled publisher every hour.
No extra category rules are required. New categories are included automatically.
Only publisher metadata/default assignments determine categories; unmatched items
are not relabelled. A report tagged with multiple categories consumes each matching
category quota, so the same report is not published repeatedly. With no custom rules,
the existing default publish limit remains a shared total per source.

All categories scans up to 100 feed entries to avoid a low legacy scan limit
preventing other categories from being considered. The maximum shown in Settings
accounts for the number of categories; actual publication depends on matching
new reports in each source feed. Source/category quotas persist across bounded
worker continuations and restart, then renew at the chosen interval.

Sources → Source action offers **Enable all** and **Disable all**, followed by
**Apply**. These actions cover every source page, regardless of search filters.
Enable all enables configured feeds and restores unambiguous missing feeds from
the existing catalogue. Records without a known feed remain disabled and are
reported. Disable all pauses every source. Neither action changes the schedule,
category rules, interval or existing articles.

An individual Enable action first checks the catalogue if an old record lacks a
feed. If no unambiguous match exists, it tries an RSS/Atom link advertised by that
publisher's homepage using the existing HTTPS/private-network protections.
Foreign-domain feed links are not fetched during discovery. Existing configured
feeds are preserved. An unavailable or undiscoverable feed produces an explicit
validation message; a source name or website URL alone cannot supply news.
Duplicate configured feeds are reported rather than silently merging source
histories. Disabling an unreachable feed never requires a successful network check.

## Saved intervals and the live worker failure

The interval remains configurable from **1–1000 minutes**. Saving an unrelated
setting preserves the existing next due time. Turning the schedule on or changing
the interval schedules the next cycle using the newly saved interval. No interval
is hard-coded to 20 minutes. The worker polls independently; no manual Run now click
is required for an enabled due schedule. Matching new articles, accessible source
evidence and the configured writing service remain necessary to publish.

Read-only production inspection on November 3 found the old deployed worker
failing every ten seconds. Migration 0022 had been applied, adding NOT NULL
`request_state` without a database default. The older deployed worker omits that
column when inserting a scheduled run, preventing job creation before feed fetch.
Migration **0024_alter_crawlrun_request_state** supplies the database default `{}`.
Migration 0022 also includes the default for new installations. A regression check
inserts a job with the historical pre-0022 model against the current schema.

Worker failures now identify safe exception classes/schema/connectivity causes
without printing credentials. Broken connections are closed before retrying.
This repair has not changed production data or deployed a release.

## Automatic reader translation

Selecting a supported language now translates public headlines, summaries and the
full report automatically. Selecting English restores the original. Shared browser
requests avoid translating the same article multiple times on one screen. Rapid
language changes ignore stale responses. Translation failures explicitly retain
the English original; a failed request is never presented as translated text.

Staff-protected content is not exposed: translation reads only currently public
articles, accepts a supported language and article ID, and never accepts arbitrary
visitor-supplied prompts or article text. Requests are rate limited. Model output
is validated, source URLs are preserved, and article Markdown remains safely
rendered. Originals are never overwritten. Translations are machine generated and
labelled on full reports.

Migration **0023_articletranslation** stores content-versioned translations in the
database. A short claim prevents concurrent readers from generating the same
translation; abandoned claims expire. Changed article text gets a fresh cache key.
The backend uses `OPENAI_API_KEY` and optional `ARTICLE_TRANSLATION_MODEL` (default
`gpt-4o-mini`). No new provider or frontend key is needed. Translation cache misses
use the configured provider and can incur API usage.

## Continuous worker recovery

Vercel configuration retains one `iad1` worker instance. The worker container now
starts `worker_supervisor.py`. A crashed worker child is restarted automatically;
a child that makes no loop progress for 900 seconds is terminated, reaped, then
replaced. No two children overlap. A completed or failed tick updates an ephemeral
progress file, independent of the database health indicator. Database errors still
close failed connections and retry; interrupted batches retain the existing durable
rollback/retry behavior. Signals stop the child before the supervisor exits.

The process stays available while automation is paused so it can see a later
Enable action. Scheduled publication still requires the saved switch to be on.
`NEWS_WORKER_STALL_SECONDS` optionally changes the default 900-second timeout (at
least 120 seconds). The default allows normal bounded feed/writing work to finish.
A supervisor cannot resolve missing migrations, an unavailable database/provider,
or a hosting outage. The pending schema fix must be deployed; local passing tests
do not mean the currently deployed old worker has recovered.

## Distinct topic illustrations

The public image selector no longer uses shared local stock photographs as news
placeholders. It rejects those paths and known legacy placeholder/title cards.
Actual source photographs are preferred. If no valid source image is present, or
all candidates fail, the frontend displays a topic illustration generated by the
same-origin `/api/news-illustration` route. Every article ID gets a different URL
and story-specific title/pattern; repeated appearances of the same article stay
consistent. Saved category metadata takes priority over incidental title words.
Sports, entertainment, health, science and other topics have their own symbols.

These are explicitly labelled illustrations rather than photographs of an event.
No external random-photo service is called. Story text is escaped; the SVG cannot
load external assets or scripts, and its route sends sandbox/CSP/nosniff headers.
Low-data mode still skips image loading. Browser tests verify real photo retention,
broken-photo recovery, different fallbacks for stories in the same category,
correct topic selection, SVG loading and mobile widths. A unit check verifies
2,000 different IDs cannot reuse the same fallback URL.

## Automatic retries and clear outcomes

Migration **0025_writingservicehealth_newssource_feed_quality_and_more** adds the
persistent article/feed retry queue, observed writing-service health and measured
feed text availability. Apply it before deploying the updated API and worker.

Temporary network/timeouts, publisher HTTP 429/5xx, AI rate limits/timeouts/outages,
and invalid service responses receive increasing delays: 1, 5, 15 and 60 minutes,
with at most five failed attempts per series. A longer Retry-After is respected,
bounded to 24 hours. Jobs survive process restarts. Exhausted attempts and permanent
blockers remain visible for review. Invalid credentials, exhausted quota/balance,
publisher 401/403, missing pages, robots restrictions and insufficient evidence
receive safe, specific reason codes and messages. Raw exception/provider bodies,
URLs with credentials and key values are never written into failure messages.

The dashboard reports the last observed writing-service result and last successful
article request; it does not claim to know the provider's current account balance
without an actual response. A shared provider cooldown prevents every source from
immediately repeating a known service failure. No extra credentials or billable
health-probe requests are introduced.

Dashboard → **Retry failed articles**, or the same button in Logs, queues stored
article failures for worker processing. Clicking repeatedly does not duplicate
queued work. A manual retry starts a fresh bounded attempt series after a fix;
it is an explicit publication request even while scheduled automation is paused.
Automatic scheduled retries respect both the automation and source switches.
On-demand requests retain their original category quantities and 24-hour deadline.
A queued automatic feed retry also rechecks those switches before fetching.

Logs → Article retries and blockers has status filters and pagination, showing
report/source, attempts in the current series, next attempt and exact blocker.
API: staff-only GET `/api/admin/retries/`, POST `/api/admin/retries/retry/`.
The API does not return stored feed bodies or source evidence. The POST stages
work only; no network/model work runs in the web request. Existing pre-migration
logs lack item snapshots, so historical failures are not retroactively invented
as retry jobs; new runs capture recoverable item data.

Every publication now commits in its own worker transaction with its quota and
attribution. The worker cannot begin another article's network/writing work before
that commit. Other valid stories continue through durable batches when one fails.
Failed attempts do not consume publishing quantities. Retries recheck duplicates,
source/category selection, quotas, cancellation and request expiry. Request retries
wait for existing continuations to finish, preventing stale quota snapshots from
overwriting recovered progress. Targets already fulfilled are skipped explicitly.
A stopped current transaction still rolls back safely; prior publications remain.

Duplicates, category mismatches, full quotas, invalid feed records and items already
waiting for retry/review have distinct **skipped** reasons. They are not relabelled
as failed publications. Counts distinguish failed articles, failed feeds, blocked
items and waiting retries. Feed retry success is **resolved**, not published.

Feed-provided full text is preferred over a short RSS summary, including XHTML
Atom content. Rich entries are tried first; adequate feed evidence avoids a blocked
page fetch. The same full-article/source-support checks still apply. Sources show
how many inspected entries contain at least 400 words; this is measured text
availability, not a claim about editorial trustworthiness. No unrelated publisher
is substituted. Successful parsed feeds are cached within a cycle for at most five
minutes (or the shorter configured interval) to avoid refetching RSS for every
one-article continuation. Explicit Test feed always performs a fresh fetch. Cache
loss on restart is safe because articles, retry jobs and quotas remain in SQL.

## Deployment order

Push backend changes, deploy/migrate through **0025**, and restart/redeploy the
worker against that schema. Then push and deploy the frontend. Redeploying the old
Git commit does not include these local fixes. Confirm worker heartbeat advances,
wait for the configured next due time, then inspect run counts for actual published
articles. A successful feed fetch alone is not proof of publication.

The existing staff-only one-off publishing API remains compatible for callers:
GET/POST `/api/admin/publishing/` and GET `/api/admin/publishing/{run_id}/`.
Its source IDs must be explicit; it does not change the saved schedule. The
removed Dashboard/Articles publishing-mode panels are no longer part of the UI.

## Verification

- Latest full isolated Django suite: **250 passed**, including retry backoff and
  exhaustion, provider classification, quota/duplicate preservation, per-article
  commits, cache reuse, manual retry authorization, pause/cancellation, rich Atom
  evidence, legacy migration compatibility and original reader/admin behavior.
- Worker recovery/progress checks: **4 passed**, including progress updates between
  scheduling, retry and upgrade phases so the supervisor sees actual loop progress.
- Frontend unit suite: **23 passed**.
- Admin browser suite: **13 passed**. A focused follow-up verifies retry status
  filtering and pagination alongside the complete recovery workflow.
- Public browser suite: **52 passed** with the topic-image changes; this retry
  update retains the same public interface.
- Production build/lint/types, migration consistency and whitespace checks pass.

Browser tests use a disposable Django database. Only publisher/writer responses
are stubbed; the real retry models, admin API, worker quota checks and article
writes run against that isolated data. No production changes, external feed
replacement, live publication or paid model calls were made during this work.
