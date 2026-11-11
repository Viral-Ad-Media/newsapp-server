# Worker CPU and deployment storage

The API runs on Render; Vercel keeps one `news-worker` daemon in `iad1`. No paid
Render worker is required or provisioned by this change.

Daemon active CPU and retained function storage are separate metrics. A project
percentage in the team dashboard is its share of usage, not live utilization.
This guide contains only implementation details and synthetic local benchmarks.

## Changes

- Read readiness in one scalar query. It includes retry/photo deadlines and
  missing-image existence checks, without loading article bodies, publishing
  rules, evidence or trend JSON. The compiled SQL is cached; results never are.
  Cross-process changes to queues and pause/source settings remain visible.
- Skip the image processor while no image work is due. Existing retry times and
  claims still govern image processing; no retries or articles are deleted.
- Back off to at most 60 seconds while idle, retaining the faster one-second
  continuation after successful work. Earlier schedule, retry and image deadlines
  shorten the wait. A newly queued manual request can wait up to 60 seconds in an
  otherwise idle worker. Set `NEWS_WORKER_IDLE_MAX_SECONDS=30` to preserve the
  former limit. Values are bounded between 5 and 120 seconds.
- Use one signal-interruptible wait per loop. SIGTERM/SIGINT wake it immediately.
  Continue supervisor progress updates and approximately one-minute heartbeats.
- Check the supervisor's child/progress file every 15 seconds instead of every
  two seconds. Crash/stall recovery remains enabled; detection can be up to 15
  seconds later. The existing 900-second stall threshold is unchanged.
- Do not initialize Google Trends regions during paused/disabled discovery.
- Emit a small activity summary every 15 minutes: loops, work batches, process
  CPU seconds and elapsed seconds. These counters contain no article/provider
  credentials. Process CPU is diagnostic, not an exact Vercel billing metric.

## Reproducible local baseline

Run `python scripts/benchmark-worker-idle.py` from a full Git checkout. It compares
the worker against commit `fc6fb7d40b2c06d1298f28f2954983321755e136`, creates an
in-memory SQLite fixture, forbids external requests and advances a fake clock
through an idle hour. It does not read production database credentials or sleep
for an actual hour. It includes loop probes, maintenance, heartbeat and photo
polling; it excludes startup, supervisor CPU, networking and useful publishing.

| Simulated idle hour | Before | After |
| --- | ---: | ---: |
| SQL statements | 672 | 126 |
| Wait wake-ups | 3,600 | 62 |
| Empty photo polls | 60 | 0 |
| Local process CPU seconds, one run | 0.3442 | 0.0404 |

This run reduced idle SQL calls by 81%, wake-ups by 98%, and local worker CPU by
88%. Do not interpret that as an 88% reduction in the whole production bill.
Real savings require comparing equal post-deploy windows with similar workload.
The readiness query is compiled with the configured database backend; tests cover
queue changes, source authorization, pause settings and image deadlines.

## Stop growing obsolete Django function bundles

`vercel.json` now selects the Other framework preset and builds a small static
worker-host page. Vercel's normal daemon builder still builds `Dockerfile.worker`
and keeps one daemon instance. The static build neither installs Django for HTTP
functions nor runs migrations. Render remains the owner of API releases and
migrations. `vercel_build.py` stays available for an explicit rollback.

Legacy `/api/`, `/admin/`, and `/health/` paths redirect to Render. Clients should
use Render directly: authenticated clients must not rely on cross-host redirects
preserving Authorization headers. The frontend already uses Render. The static
root page explicitly does not report worker health.

This prevents new duplicate Django HTTP function bundles; it does not delete
existing deployments or immediately reclaim historical storage. After validating the new
worker deployment, review old deployments and retention separately, preserving a
known rollback. No historical deployment is deleted by this change.

## Rollout and verification

1. Merge and deploy to the existing Vercel production project. Keep one daemon,
   its Neon/provider credentials and existing settings. No new paid worker.
2. Inspect the Vercel build output: the HTTP output should be static, with the
   `news-worker` daemon present and no Django function bundle. Do not promote an
   output that omits the daemon. JSON-schema and local tests cannot verify a
   platform-built container; Docker is not installed in this review environment.
3. Check the worker heartbeat in the admin UI and its 15-minute activity summary.
   Publishing/trends remain paused if paused before the deploy. Do not trigger a
   paid AI generation solely to test the optimization.
4. Compare Vercel's next 24-hour CPU usage and new deployment sizes with a similar
   baseline window. The rolling 30-day counters retain historical usage.
5. Keep the Render API and frontend serving normally. Roll back the Vercel worker
   deployment if heartbeat/job processing regresses; do not restore Neon data.
