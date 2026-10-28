# Source-grounded full articles

This policy supersedes the earlier summary-only ingestion behavior in
`admin-panel.md` and `publishing-workflow.md`. Summaries, headlines, authors,
source URLs, original publication dates and numeric reader URLs are preserved.
An upgrade replaces only the derived body and automatic SEO metadata; deliberate
editorial metadata stays intact. The previous body is retained privately.

## Working implementation

The supplied Yahoo/Fortune story is a structure reference: factual lead, useful
context, attributed details, responses where available and grounded next steps.
Its wording and facts are not copied into unrelated stories.

The writer returns schema-constrained JSON containing a lead, sections with
headings and paragraphs, a closing and SEO metadata. The application renders
Markdown with consistent spacing. The target is approximately 500–650 words;
publication requires at least 400 prose words, five substantive paragraphs,
two descriptive subheadings and paragraphs of at most 150 words. Source links
and heading text do not inflate the prose count. Text immediately below a
Markdown heading counts correctly even without an extra blank line.

The original summary is an input, not an editable model output. Unsupported
numbers, repeated paragraphs, provider truncation markers, service-error text,
link roundups and substantial copied prose are rejected. Equivalent number
formatting and apostrophes are normalized. Shared formal mission/organization
names do not alone count as copied reporting: 24 consecutive source words or
more than 20% coverage by overlapping 12-word spans block the draft.

The existing `aboki-editorial-v1` SEO analyzer is reused. Its score and
keyword-placement checks are editorial guidance, not a perfect-score publication
gate. Complete source-supported articles do not get blocked solely for missing
a focus phrase in a subheading. Metadata must be nonempty and within the supported
limits. Missing images or unrelated internal links are not invented for points.

A separate model checks material claims against numbered source passages. It
accepts faithful paraphrases and explicitly supported names, roles, dates and
quantities. It rejects factual additions, misleading attribution and unsupported
causal claims. This is an automated check, not a guarantee of accuracy; editors
remain responsible for verifying the source and its claims.

The reader displays an AI-assisted attribution label and source credit. Article
structured data distinguishes AbokiNews as publisher/author of the prepared body
from the original reporting, with `isBasedOn` and `creditText`.

## Source retrieval

The extractor recognizes article/main regions, `role="main"`, WordPress
`post-content`/`entry-content` and common article-body containers. It reads lists
and subheadings as well as paragraphs, while excluding scripts, advertising,
sharing widgets, related-story widgets and navigation. Preferred editorial
containers take precedence over surrounding page snippets.

A full RSS body can supply evidence directly. Otherwise the worker reads the
linked original article. Article fetching permits at most two redirects within
the saved publisher host/subdomains. Every hop revalidates the domain, public DNS,
HTTPS port 443, TLS hostname and pinned connection; robots policy is checked for
the target URL. Off-publisher, private-IP, credential-bearing and query-string
fetch targets are blocked. RSS feed requests still do not follow redirects.
Bodies are capped at 2 MiB with bounded socket/read timeouts. Robots 404/410 is
permitted; denial or access errors block retrieval. No paywall/permission bypass
is provided. HTTP 404/410 and publisher access restrictions produce distinct
review reasons instead of a fabricated successful import.

At least 400 words of accessible source evidence are required by the automatic
path. A generic hub, an unavailable page or a short teaser may not provide enough
information for the requested article. Such records remain for editorial review.

## Admin controls and verified supporting material

Open **Articles → View / edit → Prepare full article**, or use
**Upgrade existing short articles** for a batch. The current status and failure
reason appear in the article row as **Needs review**, and the preparation card
shows the latest attempt per article. Earlier attempts remain in the audit/job
history. Jobs are real database records, not simulated progress indicators.

When automatic retrieval is insufficient, provide **Verified source text** and,
optionally, up to two **Supporting source URLs**. These must correspond to the
provided material and use HTTPS on the same publisher host/subdomains, without
credentials or query strings. Supporting references require verified text. The
finished article cites the original source and these references (at most three
source links), rather than substituting a list of links for its body.

Only authorized staff/superusers can queue or edit preparations. Existing
same-origin mutation protection, HttpOnly sessions and Django authorization
apply. Public article responses never include source submissions, previous-body
backups or failed draft diagnostics. Submitted evidence is cleared after an
attempt; resupply it when retrying. Backups, review diagnostics and source hashes
remain private until the associated article is permanently deleted.

## Models, worker and deployment

Server-only settings:

- `OPENAI_API_KEY`: required on API and worker; never a NEXT_PUBLIC variable.
- `ARTICLE_WRITING_MODEL`: defaults to `gpt-4o-mini`.
- `ARTICLE_REVIEW_MODEL`: defaults to `gpt-4.1-mini`.
- `ARTICLE_REVISION_MODEL`: defaults to the review model.

At most three billed requests are made per attempt: writing, one bounded
structural/originality correction when needed, then independent factual review.
An explicit insufficient-evidence response is not expanded with filler. Model
requests use 35/25-second timeouts without HTTP retries. Invalid/unsupported
responses fail safely. No extra application dependency was added.

Migration `0019_source_article_preparation` added only the job/history table. It
was the sole pending migration and was applied on October 28, 2026. This fix adds
no further migration. Review any other pending migrations in a separate checkout
before deployment; the historical 0016 migration removes old publishing fields.

Use the existing persistent worker, with the same database/environment as the API:

```bash
python manage.py run_news_worker
```

It services the RSS queue and one article preparation per tick, using the existing
singleton DB lock. A crash rolls back to the queued job; revision and original
content checks protect intervening edits. RSS generation is capped at two new
writing attempts per run and defers further candidates. Use PostgreSQL in
production, one worker for local SQLite, and a generous transaction/hosting timeout
(at least 15 minutes for the configured bounds). Restart/deploy the worker when
applying these code changes. The work here did not provision or deploy a worker
host or start a new permanent scheduling service.

Read-only audit and optional queueing:

```bash
python manage.py prepare_news_articles
python manage.py prepare_news_articles --queue
python manage.py prepare_news_articles --article 123 --queue
```

**Test without publishing** checks RSS record eligibility/duplication without
billed writing calls; it does not certify that an article body is ready.

## Verified database outcome (October 28, 2026)

Of the 25 existing published records, **12 now have full source-supported bodies**:
IDs 1–10, 22 and 25, ranging from 404 to 665 prose words. A read-only comparison
against the original backups verified all 25 headlines, summaries, authors,
source IDs/URLs and publication times unchanged. No article was archived.

The remaining **13 stay visible for review**, as explicitly requested:

- IDs 11–20 and 24: original source pages unavailable (HTTP 404); no report matching
  those claims was substituted without verification.
- ID 21: the overview does not supply enough article evidence.
- ID 23: its old URL now leads to broad campaign material; generated account-security
  details not established by that material were correctly rejected.

These need verified source replacements/material, not relaxed factual checks.
The admin shows their actual reasons. No queued upgrades remain after this batch.

Live checks confirmed the prepared bodies render with paragraphs/subheadings in
`/news/1` and `/news/9`. A separate schema-constrained live preview produced a
723-word source-supported news draft without replacing an already upgraded story.
The UNESCO study guide uses its fuller official guidance with an explicit
supporting-source citation. No invalid PDF/HTML response was used as evidence.

Automated checks: full backend suite and focused regression checks, frontend unit
suite, four disposable real-Django browser tests, production Next.js build,
lint/types, Django system/migration consistency and Git whitespace checks passed.
Tests use temporary databases, an empty writing key and mocked providers; live
source/model checks and the authorized database upgrades were performed separately.
No code push or deployment was performed.
