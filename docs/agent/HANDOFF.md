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

Shipped as `4be0f7f` on `main`, deployed to https://cyr.mom via Pages.

The site now shows all 6 previously-missing streams (2026-07-31, 08-04, 08-05,
08-06, 08-07, 08-10). `last_stream` moved 2026-07-30 → 2026-08-10, row count
2458 → 2464, no duplicates. `data_through` is served alongside `generated_at`,
and the amber "DATA BEHIND" banner is live because `degraded_sources` is
non-empty.

The workflow now reports **failure** on every run while SullyGnome stays
blocked, after committing the partial refresh. That is intended.

## Validation

Ran the full degraded path against a scratch copy of `data/` with a urllib shim
for `requests` and the cached archive standing in for yt-dlp: exit code 2,
`data_through` = 2026-08-10T17:38:27Z, 6 streams backfilled, gap stats recompute
(2463 gaps, median 17.91h). Inline JS in `index.html` parses clean; banner text
render-checked.

Then verified for real on the runner via `workflow_dispatch` (run
31782424083, 2026-08-14T08:04Z):

```
SullyGnome FAILED: ... (http 403, 5690 bytes, title='Just a moment...')
SullyGnome: falling back to 2458 cached streams
TwitchMetrics stream logs: 15 / VODs: 31
Backfilled 6 recent stream(s) from TwitchMetrics: 2026-07-31 … 2026-08-10
YouTube archive: 1123 segments
Data through: 2026-08-10T17:38:27Z
DEGRADED: sullygnome: ...
```

Committed `d279488`, job went red at the "Fail if a source was degraded" step,
Pages deployed. Confirmed against the live site: `cyr.mom/data/stream-data.js`
serves `data_through` 2026-08-10 and a non-empty `degraded_sources`.

**This settles the open question:** the GitHub runner gets the same Cloudflare
challenge this machine does. It is not a network or markup issue.

## Uncommitted implementation details

None of the implementation work is uncommitted — it shipped as `4be0f7f`
(`scripts/build_dataset.py`, `.github/workflows/refresh-data.yml`,
`index.html`, plus `docs/agent/`), followed by the workflow's own data commit
`d279488`.

Still untracked and deliberately left alone: `README.md`. It predates this
session and is not mine to commit.

Uncommitted right now: this file's post-deploy update.

Generated Git state is in `.agent/runtime/WORKTREE.md`.

## Risks and unknowns

- The SullyGnome scraper is **not** repaired, and cannot be by UA/regex work —
  confirmed Cloudflare challenge on the runner. A real fix needs a
  TLS-impersonating client (`curl_cffi`, `cloudscraper`) or dropping SullyGnome
  as a source. That is a dependency decision for the owner and was not taken.
- While SullyGnome stays blocked the historical table is frozen at its 2458
  cached rows. Recent streams keep flowing in via TwitchMetrics backfill, but
  **viewer/follower stats and per-stream game lists stop updating**, and the
  gap/day-of-week/CDF models slowly drift as backfilled rows carry no games.
- Backfilled rows have no `games`, viewer, or follower figures, so the timeline
  renders those streams via the UI's "Just Chatting" fallback. Six such rows are
  live now — anything the dashboard infers from game category is degraded for
  them.
- The workflow goes red every 30 minutes while SullyGnome stays blocked.
  Intentional, but noisy; it will bury any *unrelated* future failure.
- The cached-table fallback reads `sully_streams` back out of
  `stream-data.json`, so the repo file is now load-bearing for the pipeline, not
  just an output. If it is ever truncated or hand-edited, the fallback silently
  narrows.

## Next concrete action

Decide how to handle SullyGnome now that the Cloudflare challenge is confirmed
on the runner. Three options, none started:

1. Add `curl_cffi` (or `cloudscraper`) to the workflow's `pip install` and route
   `parse_sully_page_info()` / `fetch_sully_range()` through it with browser TLS
   impersonation. Most likely to restore full history; adds a dependency and may
   break again on the next Cloudflare policy change.
2. Retire SullyGnome and rebuild the historical backbone from the cached table
   plus TwitchMetrics going forward. Loses viewer/follower/game enrichment for
   new streams.
3. Leave as-is. The site stays correct and honest, but the model slowly degrades
   and the workflow stays red.

If (3) for now, consider silencing the red runs to avoid alert fatigue — e.g.
fail only when `data_through` falls more than N days behind, rather than on
every degraded run.

## Deployment and status impact

Deployed. GitHub Pages builds from `main` on push; no other deploy target.
Live at https://cyr.mom (CNAME `cyr.mom`) serving `data_through` 2026-08-10.
Deploy reported via `report_event.py --project cyr --kind deploy` (HTTP 201).

Scheduled refresh continues every 30 minutes and will keep committing partial
data while reporting failure.

## Most relevant files

- `scripts/build_dataset.py`
- `.github/workflows/refresh-data.yml`
- `index.html` (source note ~L2245, stale banner ~L1200 and CSS ~L935)
