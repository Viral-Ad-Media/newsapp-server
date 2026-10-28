# Reader experience and account recovery

## What changed

- `/topics` is now the common category browsing and following interface. All
  configured categories are loaded across pages, alongside the existing standard
  category directory. The selected category is stored in `?category=...` and
  highlighted orange. The category links beneath Ask Aboki AI navigate there.
- My Feed, footer and category-page preference links open
  `/topics?manage=1#followed-categories`. The old `/news/custom` route redirects
  there. The updated following form is shared with the reader dashboard and saves
  real category IDs through the existing preferences endpoint.
- The reader dashboard uses the homepage palette and news cards. Its three
  sections read the real personalized feed, saved list and followed categories.
  Errors and guest sessions have explicit states. It no longer invents a reading
  spectrum from empty data or duplicate bookmarks.
- Briefing Recap is directly beneath Ask Aboki AI. Its Full timeline link preserves
  the selected window.
- The backend no longer anchors empty time windows to the newest old article.
  Last 24 Hours, Past 7 Days and Past 30 Days use the actual current time and
  article `published_at`, with future-dated items excluded. The frontend enforces
  the same boundary for compatibility during backend rollout. Empty periods show
  an empty state; old articles are never relabelled as recent.

## Ask Aboki AI

`POST /api/ask/` continues to accept `{question, location}`. Responses distinguish:

- `kind: "news"`: real matching article IDs/titles/source timestamps, empty
  `answer`, and no generated article URLs. The frontend renders only those links.
  Source/headline/summary/category matches are ranked, limited to five, and scoped
  to the selected edition. Archived articles are excluded. Specific topic queries
  never fall back to unrelated headlines. News requests for 24 hours/today use
  strict publication windows. This retrieval path works without an OpenAI key.
- `kind: "general"`: a plain-text answer and an empty source list. General
  questions use the existing OpenAI integration, a bounded response and a 12-second
  provider timeout, without pretending to have browsing or private account access.
  The password-recovery help question has a factual local answer.
- Unavailable general AI returns HTTP 503 and an actionable message, without
  fabricated answers or unrelated article links. Empty news matches have a clear
  empty state. The UI cancels stale edition requests, prevents repeat submissions,
  limits input to 500 characters and handles failures without losing the question.

News intent uses explicit news wording and local relevance; general explanatory
questions use text mode. Ambiguous wording can still require a more specific
question. This is not a claim of unlimited accuracy or live internet knowledge.
`OPENAI_API_KEY` is server-only. `OPENAI_SUMMARY_MODEL` selects the existing model.
Provider failures and secrets are not included in API responses. The existing
10/minute Ask throttle remains. Configure an edge rate limit and shared Django
cache for multi-instance production hosting: local-memory throttles are per
process, and anonymous requests through a shared web proxy share its backend IP.

## Password reset

New complete flow:

1. `/forgot-password` posts an email or username through
   `/api/account/reset-request` to backend `/api/password-reset/`.
2. Django sends a plain-text transactional email with a link to the configured
   frontend. The URL is never derived from the incoming Host header. Unknown,
   inactive and passwordless accounts receive the same generic acknowledgment.
3. `/reset-password/<uid>/<token>` collects the new password twice and posts
   through `/api/account/reset-confirm` to `/api/password-reset/confirm/`.
4. Django locks the user row, verifies the default Django token, runs the existing
   password validators, and changes only the password. The token expires after
   one hour and becomes invalid immediately on use or another password change.
5. The browser clears its session. Existing access tokens and refresh tokens are
   rejected after a password change. The reader signs in with the new password.

Reset pages have `noindex`, `nofollow`, and `no-referrer` metadata. The same-origin
proxy rejects cross-origin mutations and does not forward stale bearer tokens.
The request and confirmation endpoints are throttled (10/hour and 20/hour per
client identity/IP under the existing DRF configuration). Raw passwords, reset
URLs and provider credentials are never logged by application code. Account email
is sent only to its saved address, never an arbitrary address supplied separately.

### Required SMTP configuration

Use your existing transactional provider's SMTP service. No provider account,
DNS record, email delivery or live credential was created or changed here.

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.your-provider.example
EMAIL_PORT=587
EMAIL_HOST_USER=your-provider-username
EMAIL_HOST_PASSWORD=your-provider-secret
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=AbokiNews <support@your-verified-domain.example>
PASSWORD_RESET_FRONTEND_URL=https://your-frontend.example
```

Set these on the **backend**, not in `NEXT_PUBLIC_*` variables. For implicit TLS
port 465, use `EMAIL_USE_SSL=True` and `EMAIL_USE_TLS=False`. Verify your sending
domain and provider SPF/DKIM records before production; configure DMARC according
to your provider/domain policy. SMTP acceptance does not itself prove inbox
arrival. Use provider delivery logs to diagnose spam, bounces and authentication.

Development may explicitly use
`EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` and
`PASSWORD_RESET_FRONTEND_URL=http://localhost:3000` with `DEBUG=True`. The reset
email then appears in the development console and is **not delivered**. Production
rejects console/dummy/memory transports and missing SMTP configuration rather than
claiming an email was sent. Transport outages return a sanitized service error.

Local settings currently have no OpenAI key or SMTP credentials. General AI and
real email delivery need those values configured by the owner; no live success
has been asserted. News-link search, UI, and token logic can be tested without them.

### Deployment

No schema migration is required for this reader update. Deploy the backend before
the frontend. Preserve the existing database and migration review described in
`admin-panel.md`; this task did not apply pending migrations or publish anything.
Configure frontend `API_SERVER_URL` and public API origin as before. Because JWTs
now include a password-revocation claim, existing sessions minted before this
change need a fresh sign-in once. Reset tokens never provide access by themselves
until the new password is submitted and validated.

## Verification

Backend tests use temporary SQLite and Django's in-memory email outbox, validating
complete reset email links, password changes, weak/mismatched/expired/replayed
links, inactive/unknown accounts, malicious request hosts, SMTP errors, and access/
refresh token invalidation. AI provider calls are mocked; no paid calls or emails
to actual users are made. Browser tests exercise links, selected category styling,
following, strict timeline windows, AI presentation/failures, dashboard mobile
layout and reset UI/proxy protections. General AI quality and inbox delivery
require checks with the configured external services.


### Actual local results

- Full Django suite: **93 passed**. The 12 new reader/AI/recovery cases also passed
  a focused rerun after final routing refinements.
- Frontend unit suite: **14 passed**, including exact date cutoffs, future dates
  and invalid timestamps.
- Public/reader/editor browser suite: **47 passed**; new reader-flow checks cover
  recap placement, category highlighting/navigation, following, timeline windows,
  link-only news answers, text-only general answers, errors and mobile dashboard.
- Disposable real-Django browser suite: **4 passed**, including the existing admin
  checks and a complete reset email → frontend → changed password → sign-in flow.
  Reusing the emailed link was rejected. This used only an in-memory email outbox;
  no email was sent to an actual mailbox.
- Production Next.js build, lint/type validation, Django check, migration
  consistency and Git whitespace checks passed.

The test-only mailbox URL is registered dynamically inside
`my-news-app/tests/admin-e2e-backend.py`, bound to the disposable loopback server.
It is not part of production Django routing.

## Category RSS and reader account navigation (October 27, 2026)

The RSS directory uses the shared full topic list and loads every saved backend
category, including additional categories beyond the standard list. Each has a
category-specific XML link. Empty categories return valid empty RSS; upstream
failures return 503 instead of a misleading empty successful feed. Category
aliases use the same filtering in the article API and Django RSS endpoint.

Every normal password or Google sign-in opens `/dashboard`, including staff.
The reader header shows a visible orange Dashboard link with a person icon at
mobile and desktop widths. Reader navigation and dashboard no longer advertise
admin links. Authorized developers can still open `/superadmin` directly or log
in at `/admin/login`; the legacy editor now verifies authorization on the server
before rendering, in addition to existing protected APIs.

The frontend development environment has been restored to its original backend
on localhost:8000. The isolated NASA demo remains on port 8001 independently.
No publisher ingestion feeds or database records were added in this update.

Verification: 96 Django tests, 15 frontend unit tests, 51 public browser tests and
4 disposable real-Django browser tests passed. Production build and lint passed.
The added browser cases verify all category RSS links, mobile/desktop Dashboard
visibility, normal staff login routing and private editor authorization. The
real-Django suite also verifies authorized legacy editor access.

Browser test servers now use `.next-test` separately from the regular `.next`
directory so verification does not overwrite the active frontend build cache.
