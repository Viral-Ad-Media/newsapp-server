# Editorial desk and reader experience

Implemented November 2, 2026. Email delivery remains deferred. This change does
not send newsletters, deploy services, push commits, or create public demo news.

## Reader features

- Country homepages use active, expiring lead/supporting/feature placements.
  Empty positions use recent published articles from the same edition. Country
  and archive status are checked again on every request.
- Homepage sections use saved categories. With no configuration, up to four
  categories with actual coverage are selected. Configured disabled sections stay
  hidden; deleting all section rows restores defaults.
- Articles support news, analysis, opinion, explainer, review and interview labels,
  evidence-linked “Why this matters” context, image credits, modification dates,
  dated corrections, and signed-in error reports.
- `/stories` lists editor-curated events for the selected country. Each event has
  linked reports, optional agreement/difference/uncertainty notes and a dated
  timeline. Category similarity alone does not establish event membership.
- Following events/publishers adds reports to My Feed alongside followed topics.
  Muting removes a publisher from My Feed, while public news and saved articles
  remain accessible. Country filtering still applies. Cards explain inclusion.
- `/sources` and publisher profiles expose attribution and editorial review
  evidence without private feed URLs or fetch diagnostics. Reviews require notes
  and evidence; they do not verify every claim. Bias remains Unrated.
- Reading settings offer three article text sizes, comfortable/wide width, and
  low-data news-image suppression. Images wait for browser preferences before
  mounting, avoiding downloads when saved low-data mode is enabled.
- Reading history is opt-in per browser, limited to 100 articles per account,
  country-filtered in `/history`, and clearable across all countries.
- `/editorial-policy` explains attribution, curation, AI assistance, corrections,
  source grouping and preferences. Historical coverage URLs still work, with a
  notice. Synthetic sentiment/index defaults and invented publisher perspectives
  are removed; undocumented percentages are not presented as measured bias.

## Admin workflow

1. In **Sources**, add public descriptions, declared publisher groups if known,
   review notes and evidence URLs. Mark reviewed only after performing a review.
2. In **Articles**, set article type, image credit and evidence-linked context.
   Updates preserve original publication dates.
3. In **Editorial → Developing stories**, write a sourced overview, select reports
   about the same event in the same edition, and publish. Optional comparison
   notes are editorial assessments of linked reports; leave unknowns unknown.
4. Add **Timeline updates**, with dates and evidence reports already in the event.
   Future developments cannot be entered as past events.
5. Add **Homepage stories** with edition, position, rank, start and expiry. Add
   **Homepage sections** to name, order and enable category sections.
6. Review **Reader reports**, make article changes, add public **Corrections** when
   appropriate, then update the report status. Correction edits retain the previous
   note in the private audit log; reports stay private.
7. Inspect **Coverage** for country/category counts, new publications over 24h,
   last successful fetches, failed/untested/stale sources and repeated URL candidates.

Source counts use default category assignments; general feeds can cover additional
topics. A passing fetch is not an editorial source review. Publisher counts group
shared website hosts or declared publisher groups, without proving independent
reporting; syndicated copies may still appear across different websites.

Changing event membership moves/separates reports without deleting articles.
Moved reports disappear from the old event timeline. Draft/empty events stay
private. Story edits use revision checks (409 on conflicts). Staff can curate;
permanent deletion requires a superuser. Corrections/reports cannot be permanently
deleted through the editorial API.

## API and rollout

Public: `/api/edition/?location=ng`, `/api/stories/`, `/api/stories/{id}/`,
`/api/sources/`, `/api/sources/{id}/`.

Authenticated: `/api/reader-choices/`, per-item PUT/DELETE at
`/api/reader-choices/{sources|muted_sources|stories}/{id}/`, `/api/history/`,
`PUT /api/history/{article_id}/`, `POST /api/article-reports/{article_id}/`.

Staff: `/api/admin/coverage-monitor/`, and editorial resources under
`/api/admin/editorial/{stories|placements|sections|updates|corrections|reports}/`.
Reports are created only through the reader endpoint (10/hour scoped throttle).
Distributed production throttling still needs shared cache/edge configuration.
Next.js proxies retain HttpOnly sessions, refresh handling, same-origin mutations,
allowlists and private/no-store responses. Existing category preferences clients
do not overwrite source/event choices. New public lists omit full article bodies.

Migration `0021_developingstory_newsarticle_context_source_url_and_more` depends on
`0020_publishing_limits`. It is additive: existing articles remain published,
events start as drafts, sources as unreviewed, and preferences empty. It was applied
only to the isolated `.local/admin.sqlite3` profile:

```bash
../.venv/bin/python manage.py migrate --settings=newsapp.local_settings
```

Deploy backend migrations before the updated frontend/worker. Production builds
use `vercel_build.py`; preview databases must be migrated separately on their own
branch. Do not run normal local migrations against an unverified `.env` database.
No production migration, deployed worker verification or PostgreSQL concurrency
check was performed for this change.

Editors must populate real context, credits, reviews, events and placements.
Culture uses shared category pages, homepage sections and review/interview labels;
original reporting and licensed assets require editorial work. Empty states do
not fabricate reviews, facts, images, ratings or events.

## Executed verification

- Django: **188 tests passed**, including 15 editorial/privacy/country cases.
- Frontend unit tests: **19 passed**.
- Public Playwright suite: **51 passed** in a complete passing run.
- Real Django/admin Playwright suite: **7 passed** in a complete passing run,
  including the new editorial-to-reader workflow and widths 390/768/1440.
- Production Next build, lint/types and route generation passed.
- Django checks, migration consistency and Git whitespace checks passed.

Initial browser failures exposed missing select labels, country filtering, account
form spacing, and stale fixture expectations; corrected before passing runs.
Browser tests use disposable databases and labelled fictional fixtures, without
production publishing, real email, live feed requests or paid AI calls.
