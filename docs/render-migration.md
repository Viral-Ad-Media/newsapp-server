# Move the backend from Vercel to Render

Keep the existing Neon database and Cloudinary account. This is a hosting move,
not a database import. Do not seed, reset users, recreate tables, or fake migrations.
The Next.js frontend stays on Vercel.

## Resources prepared

- Render workspace: My Workspace.
- Web service: `newsapp-server`, ID `srv-dbpndivf3r2c738n6nh0`.
- API URL: `https://newsapp-server-8zzq.onrender.com`.
- Dashboard: https://dashboard.render.com/web/srv-dbpndivf3r2c738n6nh0
- `render.yaml` describes this web service and a separate `newsapp-worker`.
- Web uses the free plan; the continuous worker uses the smallest paid plan,
  `0.5c-512mb` (Starter). Review its current price before applying the Blueprint.
  Free web instances sleep when idle; use a paid web plan if always-on API latency
  is required. No Render database, Redis, or disk is needed.

The web service was created without database credentials and the original signing
key. Its initial build intentionally stops until those required variables exist.
No frontend traffic has been switched and no Render worker has been started.

## Credentials

Enter these in the Render service's Environment tab using the existing production
values. Do not put secrets in this repository or paste them into a public issue.

| Variable | Source / purpose |
| --- | --- |
| `DATABASE_URL` | Existing Neon production pooled URL; same database as Vercel |
| `DATABASE_URL_UNPOOLED` | Direct URL for that same Neon branch |
| `SECRET_KEY` | Preserve the Vercel production key to retain JWT signatures |
| `OPENAI_API_KEY` | Existing article preparation, review and translation credential |
| `NEWS_API_KEY`, `NEWS_DATA_IO_API_KEY` | Existing upstream news credentials |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | Existing uploads / photo library account |

Also preserve any configured Google OAuth, SMTP, publishing URL and model settings.
Vercel sensitive variables cannot be read back through its connector. Use the
original secret source or enter replacement credentials deliberately. Rotating
`SECRET_KEY` requires signing in again. Missing provider credentials affect their
features even when `/health/` returns 200.

The worker inherits credentials from the web service on Blueprint sync. Later
changes to referenced values require another sync. Do not assume they propagate
immediately.

## Deploy and verify before cutover

1. Merge this change to `main`. Validate with `render blueprints validate render.yaml`
   when the CLI is available. A Blueprint sync manages the existing web service
   with the matching name and provisions the paid worker: review before applying.
2. Set credentials and inspect production migration records / the existing Neon
   restore point or backup. Initial API creation does not run migrations.
3. Configure the web build as
   `pip install -r requirements.txt && python render_deploy.py build`, start as
   `python render_deploy.py serve`, and HTTP health check as `/health/`.
   The initial manually created service has inline commands; applying the
   Blueprint updates them. If configuring manually, update all three settings.
4. Deploy the web service. Its startup child uses the direct URL, acquires the same
   PostgreSQL advisory lock as Vercel, runs migrations only, and releases the lock.
   Failure stops Gunicorn. Gunicorn then retains the pooled URL, binds `0.0.0.0:$PORT`,
   and receives shutdown signals directly. Nothing seeds news or resets passwords.
5. Wait for Render's specific deploy ID to become `live`. Check `/health/`,
   `/api/news/`, a real article, `/admin/`, static admin CSS, login + token refresh,
   CORS from the actual frontend origin, media uploads and configured providers.
   `/health/` is liveness only; `/api/news/` must separately prove database access.
6. At worker handover, stop the Vercel daemon and wait for its active job to finish
   before starting the Render worker. Do not delete `vercel.json` while Vercel
   still hosts the API: removing it can restore framework defaults unexpectedly.
   The worker command is `python render_deploy.py worker`; it requires current
   migrations and then starts the existing supervised durable worker. Verify its
   heartbeat and job logs without triggering an extra paid publishing run.
7. Update the frontend Vercel production variables together:

   ```dotenv
   API_SERVER_URL=https://newsapp-server-8zzq.onrender.com/api
   NEXT_PUBLIC_API_BASE_URL=https://newsapp-server-8zzq.onrender.com/api
   NEXT_PUBLIC_API_URL=https://newsapp-server-8zzq.onrender.com
   ```

   Redeploy the frontend: public variables are compiled into its build. Verify
   feed, article pages, reader login/refresh and superadmin operations through the
   real frontend proxy. Backend CORS and publishing/reset URLs use the frontend
   origin, not the Render API origin.
8. Keep the Vercel API available during verification, with its worker stopped.
   Only retire the old backend after the Render API, worker and frontend pass.
   Auto-deploy is initially off to keep the handover deliberate; enable it after
   completion if desired.

## Rollback

Restore the three previous frontend API variables and redeploy the frontend. Stop
the Render worker before restarting Vercel's daemon. Both hosts use the same Neon
database; do not restore an old database snapshot as a hosting rollback. Confirm
schema compatibility before returning to an earlier code revision.
