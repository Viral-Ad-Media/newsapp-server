# Automatic news publishing

> Full-article update: [source-grounded article policy](full-articles.md) supersedes the summary-only publishing behavior described below. Existing summaries stay unchanged. Migration 0019, a configured writing key and a worker are required for upgrades.

The two supplied n8n workflows are references for related improvements, not a literal policy for this news app. The corrected implementation preserves the existing news model, reader layout, numeric article URLs, source-based content and unattended crawler behavior.

## Current flow

`crawl → existing source validation/deduplication → prepare missing metadata → publish`

There is no draft state, approval checkbox, review queue, manual URL registry, author-biography requirement, keyword-density threshold, minimum article length, mandatory FAQ or long-form section budget. Valid short news summaries publish immediately, and subsequent provider/category/tag changes never hide them. Existing ingestion commands continue skipping/logging malformed provider items rather than waiting for an operator.

## Automatic improvements

- Missing SEO title and description are derived from the original headline and summary, with plain-text previews and sensible display lengths.
- Missing Open Graph title/description use those previews. Automatically derived previews refresh on later provider updates; custom editorial metadata is preserved.
- Image alternative text uses the existing headline fallback only when an image exists. Missing images do not prevent publication and are not fabricated.
- The existing frontend continues generating the canonical URL, social metadata and NewsArticle structured data from saved news fields.
- `/sitemap.xml` exposes indexable news at its original `/news/<id>` URL and is advertised in `/robots.txt`. Explicit noindex settings still exclude a URL from the sitemap without hiding the story from readers.
- Existing safe Markdown rendering supports simple comparison tables when present; news is not forced into a blog/product-review structure.

The original article body, summary, headline, author, source, original link, publication timestamp, categories, tags, coverage and sentiment structure are not rewritten by metadata preparation. No new facts, biographies, source comparisons, product claims or images are generated. No paid AI or WordPress/plugin calls are needed for this processing.

## What was useful from the exports

The Blog Content Network export has 175 nodes and the Rank Math export has 18. Both exports are inactive. Useful ideas include metadata preparation, attribution, safe formatting, duplicate handling, sitemap discoverability and truthful error reporting. Those ideas are adapted to the current app rather than copying WordPress queues, Elementor/TablePress structures or long-form affiliate-writing rules.

The exports also contain defects that should not be copied: both branches of certain quality gates continue; the quality parser uses 85 while its prompt requests 95; the publish-success branch has no sitemap continuation; and the sitemap-result node unconditionally labels submission successful. No external submission or indexing success is claimed here. The existing crawler's counts/errors and actual server responses remain the evidence of processing.

## Correction migration

Migrations 0014 and 0015 are retained for compatibility if the earlier draft implementation was already applied. **0016_automatic_news_publishing**:

1. Prepares missing preview metadata for existing rows without changing source content or publication dates.
2. Removes the draft/review fields, research requirements, approval fingerprints and PublicationEvent table introduced by the earlier implementation.
3. Restores the prior article schema. Every article is again readable through the normal news queries, including records previously held as draft/review.

Run the full migration chain before writes if the earlier schema was applied. The shared Neon database is not changed by local tests or code edits. Validation uses a disposable SQLite database and includes a migration test with draft, review and published historical rows.

Set Django `PUBLISHING_SITE_URL` to the same public origin as frontend `NEXT_PUBLIC_SITE_URL` for correct sitemap URLs. This is not an approval or publishing gate.

## Validation

Tests cover immediate visibility of short imports, automatic metadata, preservation of custom metadata and original timestamps, taxonomy updates, RSS/AllSides ingestion without operator actions, normal admin publishing without approval fields, sitemap behavior and migration recovery of old pending rows.
