# Agent Handoff

Updated: 2026-08-14

## Active objective

Recent streams were missing from the dashboard. Restore them, and stop the
pipeline from reporting success while serving stale data.

## Completed work

- Diagnosed the cause: the SullyGnome scrape has failed on every scheduled run
  since 2026-07-31 (last good data commit `7f06d44`, 2026-07-31T13:14Z).
  SullyGnome now sits behind a Cloudflare interstitial — the page and the
  `/api/tables/...` endpoint both return HTTP 403 with `<title>Just a
  moment...</title>`, so `var PageInfo = ...;` is never present.
- `scripts/build_dataset.py`:
  - `parse_sully_page_info()` tries several `PageInfo` shapes, falls back to
    recovering just the `timecode` field, and on total failure raises an error
    that includes HTTP status, page title, and the first 200 bytes — that
    diagnostic is what identified the Cloudflare challenge.
  - Browser-like `UA` (full Chrome user agent + Accept headers).
  - SullyGnome is no longer a hard gate. On failure the run falls back to the
    cached `sully_streams` from `stream-data.json` and continues, so
    TwitchMetrics and the YouTube archive still run.
  - New `backfill_recent_from_exact()` merges TwitchMetrics VOD/log rows that
    the Sully table is missing, matched on a ±15-minute window (VOD timestamps
    run a few seconds behind Sully's for the same stream).
  - Payload gains `data_through` (newest stream actually in the data) and
    `degraded_sources`; both are mirrored into `stream-data.js`.
  - Exits 2 after writing files when any source degraded.
- `.github/workflows/refresh-data.yml`: tolerates exit 2 so the partial refresh
  still commits, then fails the job so a broken source shows up red instead of
  green. Also adds `archive-grouped-dates.csv` and `title-semantics.csv` to the
  `git add` list — they were being regenerated but never committed.
- `index.html`: source note now reads "data through X · checked Y" instead of
  only "refreshed Y"; new amber `#stale-banner` appears when
  `degraded_sources` is non-empty.

## Current behavior

Running the pipeline with SullyGnome blocked recovers the 6 streams that were
missing (2026-07-31, 08-04, 08-05, 08-06, 08-07, 08-10). `last_stream` moves
from 2026-07-30 to 2026-08-10; row count 2458 → 2464, no duplicates.

## Validation

Ran the full degraded path against a scratch copy of `data/` with a urllib shim
for `requests` and the cached archive standing in for yt-dlp: exit code 2,
`data_through` = 2026-08-10T17:38:27Z, 6 streams backfilled, gap stats recompute
(2463 gaps, median 17.91h). Inline JS in `index.html` parses clean; banner text
render-checked.

Not verified: whether the GitHub runner sees the same Cloudflare challenge.
sullygnome.com returns 403 to this machine for every request, so the scrape
repair itself could not be tested locally.

## Uncommitted implementation details

All of the above is uncommitted in the working tree. Nothing committed, nothing
pushed. Base revision `5d8f694` on `main`, level with `origin/main`.

Modified (unstaged, 220 insertions / 17 deletions across 3 files):

- `scripts/build_dataset.py` — +162/-… : hardened `parse_sully_page_info()`,
  browser `UA`, cached-table fallback, `load_cached_sully_rows()`,
  `backfill_recent_from_exact()`, `data_through` / `degraded_sources` in both
  the JSON payload and the `dash` object written to `stream-data.js`,
  `sys.exit(2)` on degraded.
- `.github/workflows/refresh-data.yml` — +19 : `id: pipeline` step that accepts
  exit 0 or 2 and records `exit_code`, expanded `git add` list, trailing
  "Fail if a source was degraded" step.
- `index.html` — +56 : `.stale-banner` CSS block, `#stale-banner` markup after
  `#live-banner`, and the source-note / banner logic in the inline script.

Untracked: `README.md` (pre-existing, not mine) and `docs/` (the managed-state
directory, including this file).

No generated data files were modified — the end-to-end test wrote to a scratch
directory, so `data/` is untouched and still shows the stale Jul 30 payload
until the pipeline is actually run or the workflow fires.

Generated Git state is in `.agent/runtime/WORKTREE.md`.

## Risks and unknowns

- The SullyGnome scraper is **not** repaired. The UA and regex work do not
  defeat a Cloudflare challenge. If the runner is challenged too, the next run
  will log the exact title/status and the fallback path will carry the site.
  A real fix needs a TLS-impersonating client (`curl_cffi`, `cloudscraper`) or
  dropping SullyGnome as a source — that is a dependency decision for the owner.
- Backfilled rows have no `games`, viewer, or follower figures (TwitchMetrics
  does not expose them), so the timeline shows those streams with an empty game
  list and the UI's "Just Chatting" fallback.
- The workflow will now go red every 30 minutes while SullyGnome stays blocked.
  That is intentional, but it is noisy.

## Next concrete action

Awaiting owner decision — asked at end of session, not yet answered: commit and
push these changes, then trigger one `workflow_dispatch` run to see what
SullyGnome returns to the GitHub runner. That single run is the deciding
evidence:

- If the runner also gets `Just a moment...`, SullyGnome needs a
  TLS-impersonating client (`curl_cffi` / `cloudscraper` added to the workflow's
  `pip install`) or it should be retired as a source.
- If the runner gets a normal page, the break is markup-side after all and
  `SULLY_PAGE_INFO_PATTERNS` is the place to extend.

Either way the fallback path now keeps recent streams on the site.

## Deployment and status impact

GitHub Pages deploys from `main` on push; no other deploy target. Not deployed.

## Most relevant files

- `scripts/build_dataset.py`
- `.github/workflows/refresh-data.yml`
- `index.html` (source note ~L2245, stale banner ~L1200 and CSS ~L935)
