# Real RSS publishing on an isolated local backend

This profile **never uses the Neon URL in `.env`**. It overrides the database with
`newsapp-server/.local/admin.sqlite3`, uses its own private development secret,
and binds the server to loopback. The existing SQLite database, Neon database,
production settings remain unchanged. The local frontend can be pointed at this
backend independently of production.

The local source is NASA's official feed:
`https://www.nasa.gov/news-release/feed/`. It uses the Science category and the
United States (`us`) edition. Headline, publication time and original source link
are preserved. Only summaries are imported. No fictional articles are seeded.

## Start or resume

Use these commands from `newsapp-server`, with the project's virtual environment:

```bash
../.venv/bin/python manage.py migrate --settings=newsapp.local_settings
../.venv/bin/python manage.py setup_local_feed --settings=newsapp.local_settings
../.venv/bin/python manage.py changepassword support@viraladmedia.com --settings=newsapp.local_settings
```

Migrations run only against the new isolated SQLite file. The setup command refuses
the normal database profile. It configures a local superuser and NASA feed without
fetching/publishing. There is no shared/default account password: choose one using
the interactive command above. This local account is separate from the Neon user.

Start the server and worker in separate terminals:

```bash
../.venv/bin/python manage.py runserver 127.0.0.1:8001 --noreload --settings=newsapp.local_settings
```

```bash
../.venv/bin/python manage.py run_news_worker --settings=newsapp.local_settings
```

Run only one worker for this SQLite profile. Scheduled automation starts disabled.
The persistent local process still handles explicitly queued manual jobs. Stop
these terminal processes with Ctrl+C when finished.

## View the result

- Django admin: `http://127.0.0.1:8001/admin/`
- Published US feed: `http://127.0.0.1:8001/api/news/?location=us`
- First imported article: `http://127.0.0.1:8001/api/news/1/`
- Outbound RSS: `http://127.0.0.1:8001/api/rss/?location=us`

The frontend at `http://localhost:3000` has been restored to its original backend
at `http://localhost:8000` through `.env.development.local`. The isolated demo
remains available directly on port 8001; its articles are no longer served by the
frontend. The previous environment snapshot remains private in
`.local/frontend-env.before-local-feed` (gitignored, mode 0600).

During the demo, the imported story was verified at `/news/1` and on the United
States homepage while the frontend pointed at port 8001. That local article ID
should not be assumed to identify the same article on the original backend.

To repeat a dry run without an admin browser, queue through the shared service:

```bash
../.venv/bin/python manage.py shell --settings=newsapp.local_settings -c 'from news.automation import queue_run; print(queue_run(dry_run=True).pk)'
```

For actual local publication, use `dry_run=False`. The running worker processes
the queued job; existing canonical URLs/headlines are skipped as duplicates.
After a local password is chosen, the same credentials can obtain a JWT via the
existing `/api/token/` endpoint for the private `/api/admin/` operations.

## Actual verification

- First live fetch exposed a missing default certificate authority bundle in the
  macOS Python environment. The fetcher now explicitly uses Requests' installed
  trusted CA bundle while retaining `CERT_REQUIRED`, hostname verification, DNS
  pinning, and redirect/private-network blocking. HTTPS verification was not disabled.
- A genuine network request parsed 10 NASA RSS entries.
- Local run #2: success; fetched 1, eligible 1, created 0, published 0.
- Local run #3: success; fetched 1, created 1, published 1.
- Imported headline: “How a Johnson Mentor Shapes the Next Generation of NASA Talent”.
- Original publication time: October 27, 2026, 07:07:51 UTC.
- Article detail, public news feed and RSS endpoints returned HTTP 200. The RSS
  contains the original NASA article URL.
- The failed first run remains in local history rather than being hidden.

The backend and worker were left running for local testing. No production
publication, production migration or deployment occurred. Only the local frontend environment
was temporarily switched to the isolated backend, then restored to the original backend on request.
