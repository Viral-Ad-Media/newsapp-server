# Editorial SEO tools

Posts now have optional search and sharing metadata. Existing posts keep their
current titles, summaries, images and `/news/<id>` URLs unless an editor supplies
overrides. This is an AbokiNews feature inspired by common content-analysis
workflows, not an installation of the WordPress Rank Math or AIOSEO plugins.

## Stored fields

- `focus_keyword`: one phrase used by the editorial checks, never a meta-keywords tag.
- `seo_title`, `seo_description`: optional search title/description overrides.
- `canonical_url`: absolute HTTP(S) preferred version; empty means this post.
- `og_title`, `og_description`, `og_image_url`: optional social sharing overrides.
- `image_alt`: description of the actual story image.
- `seo_noindex`, `seo_nofollow`: explicit boolean robots settings, false by default.
- `schema_type`: `Article`, `NewsArticle` (default), or `BlogPosting`.

The superadmin create/update endpoints validate these fields before writing.
Empty strings clear overrides. Ordinary readers cannot write them or run analysis.
The public article serializer includes metadata for the frontend renderer.

`POST /api/superadmin/seo/analyze/` evaluates an unsaved draft and returns a score,
check-level results and points, word/title/description counts, link counts and the
indexing state. It does not save a post, call an AI provider, fetch links, check
live search rankings or change users. Analysis accepts up to 200,000 body characters.
The frontend proxies this endpoint through the existing same-origin session route.

## Score: aboki-editorial-v1

| Check | Points |
| --- | ---: |
| A focus phrase is set | 5 |
| Effective search title has 30–60 characters | 10 |
| Effective description has 120–160 characters | 10 |
| Focus phrase in title | 10 |
| Focus phrase in description | 10 |
| Focus topic in the first 100 words | 10 |
| Focus topic in a `##` or `###` subheading | 5 |
| At least one related internal link | 5 |
| At least one external reference link | 5 |
| Story image with a description | 10 |
| Nonempty paragraphs at most 150 words each | 10 |
| Body has at least 300 words | 10 |
| **Total** | **100** |

These are transparent review prompts, not Google ranking rules. Short news briefs
can be complete below 300 words; do not pad them or insert irrelevant images and
links for points. There is no ideal keyword-density target. Verify accuracy,
original reporting and relevance independently. Search engines may rewrite titles
and snippets, and structured data does not guarantee an enhanced search result.
Noindex is displayed separately: a high score does not override it.

## Writing format and deployment

The public reader safely renders plain paragraphs, Markdown `##`/`###` headings,
`[label](/news/123)` related links, and HTTP(S) reference links. Raw HTML is shown
as text; scripts and unsafe link schemes are not executed. The analysis counts
those supported links rather than unrendered HTML anchor tags.

Merge/deploy the backend migration **before** the frontend SEO editor. Configure
`NEXT_PUBLIC_SITE_URL` on the frontend to the public canonical site origin, for
example `https://your-news-domain.example`; it defaults to the existing Vercel
frontend. Never set this to the backend API origin. Rebuild when this public
variable changes. The frontend applies per-post canonical, robots, Open Graph,
Twitter card and Article-family JSON-LD metadata; it uses the public headline and
actual publication date in structured data, without inventing an updated date.
Existing numeric routes are preserved; no slug/redirect migration is included.

## References

- [Rank Math content checks](https://rankmath.com/kb/seo-score-vs-content-ai-score/)
- [AIOSEO TruSEO recommendations](https://aioseo.com/docs/understanding-the-truseo-page-analysis-recommendations/)
- [Google title guidance](https://developers.google.com/search/docs/appearance/title-link)
- [Google snippet guidance](https://developers.google.com/search/docs/appearance/snippet)
- [Google Article structured data](https://developers.google.com/search/docs/appearance/structured-data/article)
