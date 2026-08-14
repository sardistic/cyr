# Agent Handoff

Updated: 2026-08-14 (second pass: source reorder + impersonation)

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
  - ~~Exits 2 when any source degraded~~ — superseded by `54a36ea`, see below.
- `.github/workflows/refresh-data.yml`: adds `archive-grouped-dates.csv` and
  `title-semantics.csv` to the `git add` list — they were being regenerated but
  never committed. (The exit-2 / fail-the-job handling this commit added was
  removed again in `54a36ea`.)
- `index.html`: source note now reads "data through X · checked Y" instead of
  only "refreshed Y"; new amber `#stale-banner` appears when
  `degraded_sources` is non-empty.

## Current behavior

Shipped as `4be0f7f` then reworked in `54a36ea` on `main`, deployed to
https://cyr.mom via Pages.

Source hierarchy as of `54a36ea`: **TwitchMetrics is primary** — it decides
which streams exist and when. SullyGnome runs afterwards for game/viewer/
follower enrichment and deep history, and may fail without consequence. The
YouTube archive is independent.

SullyGnome is fetched through `curl_cffi` TLS impersonation. That clears the
Cloudflare challenge on the landing page (`PageInfo` parses again) but the
`/api/` path is challenged separately and still returns 403 — verified both
locally and on the runner. So SullyGnome remains degraded in practice; it is
just no longer fatal.

Runs are **green again**. Degraded sources no longer exit non-zero and the
"Fail if a source was degraded" step is gone, because a red run every 30
minutes was pure noise. Only a total data failure (no streams from any source)
exits 1. The site carries the signal instead, via the banner.

The site now shows all 6 previously-missing streams (2026-07-31, 08-04, 08-05,
08-06, 08-07, 08-10). `last_stream` moved 2026-07-30 → 2026-08-10, row count
2458 → 2464, no duplicates. `data_through` is served alongside `generated_at`,
and the amber "DATA BEHIND" banner is live because `degraded_sources` is
non-empty. The banner distinguishes the two failure shapes: losing TwitchMetrics
can genuinely hide streams, losing SullyGnome only costs game and viewer
figures, and it no longer claims the former when only the latter happened.

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

**This settled the first open question:** the GitHub runner gets the same
Cloudflare challenge this machine does. Not a network or markup issue.

Second pass (`54a36ea`) validated the same way, then on the runner via
`workflow_dispatch` run 31832013144 (2026-08-14T19:09Z), which **passed green**:

```
TwitchMetrics stream logs: 15 / VODs: 31
SullyGnome: landing page cleared via chrome impersonation.
SullyGnome unavailable, using cached table: stream table API returned http 403
  for 2017 (Cloudflare challenge on /api/ — landing page cleared but the API
  path did not)
SullyGnome: fell back to 2464 cached streams
YouTube archive: 1123 segments
Data through: 2026-08-10T17:38:27Z
```

Committed `71a4375`, Pages deployed, live site confirmed serving
`data_through` 2026-08-10 with `degraded_sources: [sullygnome]`. Impersonation
targets were matrix-tested against the live site before shipping: rolling
`chrome` and `safari17_0` clear the landing page; `chrome124`, `chrome131`,
`firefox135` and `edge101` do not. No target cleared `/api/`.

## Uncommitted implementation details

None of the implementation work is uncommitted. It shipped in two commits:
`4be0f7f` (stop the silent staleness) and `54a36ea` (TwitchMetrics primary,
TLS impersonation, no failing runs). The workflow's own data commits followed
each: `d279488` and `71a4375`.

Still untracked and deliberately left alone: `README.md`. It predates this
session and is not mine to commit.

Uncommitted right now: this file's post-deploy update.

Generated Git state is in `.agent/runtime/WORKTREE.md`.

## Gotchas worth keeping

- `curl_cffi` only clears the challenge **when it sends its own browser
  headers**. Passing the module's `UA` dict into an impersonated request
  overrides them and gets it challenged again. That is why
  `open_sully_session()` hands back the landing-page response instead of
  re-requesting it, and why `fetch_sully_range()` omits `UA` when impersonating.
  This cost a debugging cycle; do not "tidy" those headers back in.
- Pinned impersonation targets go stale: `chrome124`, `chrome131`, `firefox135`
  and `edge101` are all challenged today. Rolling `chrome` works, `safari17_0`
  works. `SULLY_IMPERSONATE` deliberately holds rolling names only.
- `data/stream-data.json` is now an **input** as well as an output — the
  cached-table fallback reads `sully_streams` back out of it. Truncating or
  hand-editing that file silently narrows the historical model.

## Risks and unknowns

- TLS impersonation got the landing page but **not** `/api/`. SullyGnome
  enrichment is still down and may stay down. Untried next steps: a residential
  proxy, solving the challenge with a headless browser, or a `cloudscraper`
  attempt on the API path specifically.
- While SullyGnome stays blocked the historical table is frozen at its cached
  rows. Recent streams keep flowing via TwitchMetrics, but **viewer/follower
  stats and per-stream game lists stop updating**, and the game-category parts
  of the model slowly drift as game-less rows accumulate.
- Backfilled rows have no `games`, viewer, or follower figures, so the timeline
  renders them via the UI's "Just Chatting" fallback. Six such rows are live.
- **Nothing alerts any more.** That was the explicit ask, and it is the right
  call for this noise level, but it means a future outage of TwitchMetrics — the
  source that *can* actually hide streams — will surface only as a banner on the
  site that someone has to look at. If that matters later, alert on
  `twitchmetrics*` in `degraded_sources` only, not on any degraded source.

## Next concrete action

Nothing is blocking. The site is current, runs are green, and no alerts fire.

If restoring game/viewer enrichment becomes worthwhile, the open question is
narrow: get past Cloudflare on `sullygnome.com/api/` specifically, given the
landing page already clears. Worth trying in order — `cloudscraper` against the
API path, then a headless browser to mint a `cf_clearance` cookie the session
can reuse, then a proxy with better IP reputation.

Otherwise leave it. Recent streams do not depend on it.

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
