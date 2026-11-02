# Public readiness review — November 2, 2026

**Decision: tested core flows work locally; full public launch is not yet signed off.**
The public URLs respond, but the local fixes need release, an administrator
credential needs rotation if the old seed ran, and production writing, recovery
and worker configuration need verification.

## Story and evidence

A reader chooses a country, browses categories and articles, registers, saves
stories and follows topics. Requests cross the Next.js session proxy into Django
and its database. Staff use a separate authorized console to manage content and
schedule publishing jobs with durable quotas.

| Boundary | Status and evidence |
| --- | --- |
| Reader UI | 51 public browser cases exercised editions, navigation, articles, timelines, search, images, account dialogs, topic preferences, member gates, and responsive layouts. |
| Browser → real API → database → UI | A real Django integration case registered a disposable reader, established HttpOnly sessions, read an editor-created article, saved/unsaved it, persisted topic preferences, rendered personal/live feeds and logged out. |
| Authorization | The real reader received 403 from the admin API. Existing staff/admin checks passed. An anonymous live admin request returned 401. |
| Backend | 173 Django tests passed; migration consistency reported `No changes detected`. |
| Frontend | Production Next build, lint and 17 frontend unit tests passed. |
| Admin/account browser flows | All six real-backend browser cases passed in a complete run, including admin/settings/reset and the new reader journey. |
| Dependencies | npm production audit: 0 advisories. OSV query: 58 installed Python package versions, 0 findings. `pip check`: no broken requirements. These results apply to the audited local versions, not an unbuilt deployment image. |
| Public access | Anonymous homepage, robots, backend health and a Nigeria news request returned 200. The news response reported 19 articles. |
| Production release/automation | Not signed off: the new code is uncommitted/undeployed, and an updated deployed worker, migration state and successful production job have not been verified. |

The reader suite first exposed four stale expectations for public RSS, unsaved
hardcoded categories and old category links. After updates, the second full run
passed 50/51. The remaining issue was a test selector matching both h1 and h2;
the corrected affected files then passed all 10 cases. All 51 cases therefore
have passing evidence across the full run and focused rechecks; one final full
51-case command was not rerun after the selector-only correction.

The real-reader test initially used a password similar to its username, correctly
rejected by Django. With an unrelated test password, the entire journey passed.
Its users and articles existed only in a temporary database. No production
registration, publishing, email sends or paid AI calls were performed.

Detailed results: [public-readiness-checks.json](public-readiness-checks.json).

## Security issue fixed locally

Previously, `seed_news` created/reactivated a privileged account, granted
staff/superuser flags and reset its password to a repository-embedded value.
Because the production build invokes this command, deployment could restore that
known password and overwrite administrator account choices.

The provisioning/reset code and embedded credential have been removed from the
working tree. Seeding now imports content only. A regression test verifies that
it creates no users and preserves an existing account's password, roles and active
state. The email-login test now provisions its own disposable administrator.

**Before launch:** treat any password set by the old command as exposed and
rotate it promptly using the trusted Django management workflow. Ensure this fix
is released before another content seed/build can restore the old credential. Review
its privileges and sign-in history. Removing a credential from current files does
not remove it from Git history or secure an already provisioned account. New
administrators should be created interactively with `manage.py createsuperuser`.
No production password or account was changed by this review.

## Observed production configuration

Vercel's project-variable inventory was inspected without printing secrets or
changing configuration; it reported no hidden production-variable records.

Confirmed: backend `DEBUG=False`; both frontend API URLs point to
`https://newsapp-server-five.vercel.app/api`; backend CORS permits the public
frontend origin. Database variables and a Django secret key are present. The
successful anonymous news response demonstrates a working deployed database read.

The inventory did not list backend `OPENAI_API_KEY`, required by the implemented
full-article writing path, or SMTP `EMAIL_HOST`, `EMAIL_HOST_USER` and
`EMAIL_HOST_PASSWORD`, used by the default production recovery implementation.
Neither project listed `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`; configure
Google OAuth and authorized redirects if Google sign-in is offered at launch.
Inventory checks are not successful provider calls: live AI, SMTP, Google and
worker execution remain unverified.

Project metadata reports deployment protection, but the public aliases actually
returned 200 anonymously; metadata alone was not treated as blocked public access.
The live HTTPS responses include HSTS. Django's app-default `check --deploy`
reported missing application-level HSTS; verify that TLS/header behavior remains
correct on the final chosen domain/host.

## Required release steps

1. Commit/push the reviewed code to the intended release branches. The backend
   checkout is on `taiye-dev`, frontend on `main`; confirm the production branch
   for each Vercel project.
2. Deploy the backend first, apply the reviewed migration chain including `0020`,
   start the updated worker, then deploy the frontend. Review/back up an older
   production database before applying pending migrations.
3. Rotate any administrator password affected by the old seed promptly. Ensure
   the safe seed code is released before another build and verify later content
   seeds preserve account access.
4. Configure writing and recovery services, and Google OAuth if used. Verify
   provider calls and password-reset delivery with controlled owner accounts.
5. Confirm a worker heartbeat, feed-only test, and a small permitted publishing
   job with the intended category/quota. New catalogue feeds start disabled and
   must be enabled explicitly.
6. Verify the final public domain anonymously, reader accounts, saving/topics,
   admin/RSS authorization, CORS/cookies and sitemap against the deployed version.
7. Confirm database backups, runtime alerts and appropriate distributed auth/reset
   rate limits. Django's default local cache is not a shared throttle budget across
   serverless instances.

Local checks used Python 3.14.3. CI targets Python 3.11/3.12, but the Python 3.12
worker image was not built here. PostgreSQL multi-process fencing, deployment
recovery and production traffic/load behavior still need staging verification.
Software checks do not establish universal country/topic coverage or independent
editorial verification.

Frontend unit tests no longer require a sibling backend checkout; the frontend CI
workflow now runs them. A real Django reader test was added so critical boundaries
are not covered only by mocked UI responses.

No push, deployment, production migration, password rotation, worker startup or
production environment change was performed by this review.

The alternate REST password login endpoint now shares the stricter `login` scope
with the main JWT endpoint. Regression tests check that alternating endpoints
cannot bypass the five-attempt budget and that ordinary readers retain their
roles. This remains a per-cache-instance limiter; distributed enforcement still
needs deployment-level verification. The disposable browser harness raises only
its own login rate because all simulated users share one loopback address.
