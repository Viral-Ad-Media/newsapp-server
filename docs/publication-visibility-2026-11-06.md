# Public publication dates, visibility and photographs

The last twelve worker reports were stored with `location=world`. The public
country selector had no World option, and country filters correctly excluded those
reports. This release adds **World / All news** to the edition selector. Its feed
and briefing include every published report; individual country editions retain
precise attribution. Member Live defaults to **All published news**, with an
optional selected-country filter. Archived stories remain excluded.

Publication dates remain visible on list, briefing, category, report and article
cards. Worker publication uses the time a report becomes public on AbokiNews;
`source_published_at` separately preserves the original publisher's date. Older
reports are not made artificially recent. Public lists refresh while visible and
shared caches expire/invalidate using the existing revision mechanism.

New worker articles require a publisher photograph or an individually reserved
real topic photograph before committing. A missing photo produces a transient
`photo_unavailable` retry. Prepared article text is retained with an evidence
fingerprint so a photo-only retry does not repeat a paid rewrite. Topic photos
remain attributed, labelled illustrative and uniquely allocated to one report.

The background worker validates photo MIME, format, dimensions and download limits,
then copies source/topic photos to the existing Cloudinary account. Safe HTTPS/DNS
checks apply to every download/redirect. Browser requests only read or queue work.
A broken source image can reserve a cached real topic photo immediately; external
photo discovery remains in the worker. Browser retries back off and pause in hidden
tabs. An image CDN outage keeps an available publisher photo and schedules a retry.
Photo maintenance errors cannot prevent scheduled work or worker heartbeats.

Migration **0030_sourcephotocheck_hosted** records successful CDN copies. The
PostgreSQL reservation locks only the photo-check row, not the nullable joined
asset. Live PostgreSQL verification is required in addition to SQLite unit tests.

Validation: 55 public browser checks, 14 admin browser checks, 24 JavaScript unit
checks, production Next build and lint passed. **286 backend tests passed**, including photo-only retries, world publication
visibility and a photo failure that preserves the worker heartbeat. On the deployed frontend, report #41 appeared in World Latest,
its `2026-11-03T17:34:31.113566Z` publication date was rendered, and its publisher
photo decoded at 1200px width. Mobile had no horizontal overflow or page errors.

Frontend production: `dpl_BMvEKLQC8gDfkrG4e1MGxm8ZfjYH`.
Backend initial production: `dpl_DFmxXNrXrePdXE5QJ5RDynpzFrrq`; migration 0030
applied. Backend correction: `dpl_8M8JMHQT4hXDSzPbcmhPMEXGAdrW`, correcting the PostgreSQL
lock identified by live verification and isolating photo maintenance failures. Automation's existing OFF setting is preserved.

These releases deploy the tested local worktrees. Commit and push both repositories
to retain the same source in GitHub and avoid a later Git deployment reverting it.


The complete public-photo check initially decoded 40 of 41 photographs and found
publication dates on all 41. The remaining historical grid-interconnector report
now matches the electricity-transmission library. Missing-photo requests can
reserve an unused cached real photo immediately, without waiting for an external
provider or an older retry deadline. This is covered by an attribution/unique
allocation API regression test.

Final backend production: `dpl_9irHyzaxpvrkoWjj29sFMXbmxGeW`, adding the final
historical-photo match and immediate cached allocation for missing-photo reads.

Final production check: **41/41 public reports have publication dates and 41/41
photographs decoded successfully in Chrome**, with no photo failures. Report #19
returned its reserved real grid photograph with HTTP 200. CDN mirroring continues
in the background; a temporary copy failure retains the available public photo.
The latest frontend/backend deployments both reached Ready. Worker automation
remains OFF by existing configuration. No Git commits or pushes were performed.
