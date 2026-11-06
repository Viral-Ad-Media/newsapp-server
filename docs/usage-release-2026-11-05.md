# Usage optimization release — 5 November 2026

The backend/worker and frontend were deployed directly from the tested local
working trees. The changes have not been committed or pushed to GitHub here.
Commit and push both repositories to preserve these changes in later Git builds.

| Component | Production deployment | Result |
| --- | --- | --- |
| Backend + worker | `dpl_GuiYibMTGR3kYnCiBwhHUc8RVXGy` | Ready; schema migrated through 0029 |
| Frontend | `dpl_5rshkeckb9DvZeFqTDNdtbQTudjq` | Ready |

Production URLs:

- https://newsapp-server-five.vercel.app
- https://my-news-app-blond-six.vercel.app

## Verification

- 276 isolated backend tests passed, plus migration consistency and system checks.
- 54 public browser tests and 14 admin browser tests passed.
- 24 frontend unit tests passed; lint and production Next build passed.
- Live anonymous public news returned `200`, then `x-vercel-cache: HIT` on the same
  URL. Public cache lifetime was 14 seconds at the time of verification.
- Live edition returned six latest stories for Nigeria.
- Live admin dashboard returned `401` with `private, no-store`.
- Frontend `/api/reader/article-photos/25` returned a source photograph from the
  updated backend, confirming the frontend proxy connection.
- Homepage, timelines and Live pages returned `200`. A live mobile browser
  displayed the Nigeria edition with stories, no horizontal overflow, and zero
  JavaScript page errors.
- The unused 3 MB dummy photo returned `404` after being excluded from deployment.
- New-deployment runtime error/fatal log queries returned no records at release
  verification time; that is a short observation, not a long-term guarantee.
- Production worker heartbeat was advancing. **Saved automation was OFF**, with
  interval 10 minutes. These saved settings were not changed. Google Trends
  priority is ON, with 61 regional cache rows configured but not yet synchronized:
  trend fetching waits for enabled automation or an explicit queued request.
- Direct read-only Google Trends export checks for Nigeria and the US returned
  ten recent topics each. No production publication or paid writing request was
  triggered for verification.

## What can be compared now

These are configured request/check rates, not measured billing savings:

| Operation | Previous steady idle rate | New steady idle rate |
| --- | --- | --- |
| Worker loop | Every 10 seconds | Adaptive, up to 30 seconds; earlier for a known due deadline |
| Visible idle admin panel | Every 10 seconds | Every 30 seconds |
| Hidden admin panel | Continued polling | No polling |
| Latest/timeline list | Every 30 seconds | Every 60 seconds |
| Live list | Every 30 seconds | Every 30 seconds |
| Photo discovery HTTP function | Could fetch publisher/provider pages | Returns existing data or queues worker work |

The worker remains available while automation is paused. Long-lived Daemon memory
usage is therefore not eliminated. CDN hits still count as CDN requests.

## Comparable provider-usage windows

Vercel billing charges returned `costs_not_found`; the available legacy
observability query endpoint did not return usable metrics. No actual CPU/storage
or money-saving percentage is claimed from those APIs.

Use Vercel Usage/Observability for the following equal complete windows, excluding
release day and adjusting if traffic/publication volume differs:

- Before: 4 November, 00:00–24:00 Africa/Lagos.
- After: 6 November, 00:00–24:00 Africa/Lagos, or a later full day after scheduled
  automation is enabled. Do not compare an idle publishing day to a busy one.

Record metrics separately for backend and frontend projects:

| Metric | Before | After |
| --- | --- | --- |
| Daemon active CPU seconds | not captured | pending comparable window |
| Functions active CPU seconds | not captured | pending comparable window |
| Functions/deployment storage | not captured | pending comparable window |
| CDN requests | not captured | pending comparable window |
| Function invocations / cache hit share | not captured | pending comparable window |
| Builds and retained deployments | not captured | pending comparable window |
| Pageviews / successful published reports | not captured | pending comparable window |
| Error rate / queue latency | not captured | pending comparable window |

Normalize frontend CPU/requests per 1,000 pageviews, and worker CPU per successfully
published report. Separate one-time photo repair and first Google Trends cache
warming from steady-state usage. Storage includes retained old deployments, so a
new smaller deployment does not instantly remove historical storage usage.
