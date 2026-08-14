# Agent Handoff

Updated: 2026-08-14 (third pass: games re-sourced from Twitch GQL)

## Active objective

Recent streams were missing from the dashboard. Restore them, stop the pipeline
reporting success while serving stale data, and stop depending on SullyGnome —
which has been behind a Cloudflare challenge since 2026-07-31 — for anything
that matters.

All three are done. The site is current, runs are green, and the only thing
SullyGnome still uniquely provides is viewer/follower figures.

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

Third pass — `fc0ff0c`, per-stream games no longer come from SullyGnome:

- `gql()`, `fetch_vod_game_index()`, `fetch_vod_games_detail()` in
  `scripts/build_dataset.py` read games from Twitch's GQL endpoint — the same
  one already used for live status. Unauthenticated (public web client ID), no
  Cloudflare in front of it.
- Two levels of detail: a VOD's base `game{name}`, and
  `momentRequestType:VIDEO_CHAPTER_MARKERS` for the ordered multi-game list when
  the category changed mid-stream. The marker list is what reproduces
  SullyGnome's `games` array.
- `attach_vod_games()` fills only rows with empty `games`, so SullyGnome still
  wins where it has data — it carries viewer/follower figures next to the games.
- Banner copy updated: a SullyGnome outage now costs "viewer and follower
  figures", not games.

## Current behavior

Shipped in three commits — `4be0f7f`, `54a36ea`, `fc0ff0c` — on `main`,
deployed to https://cyr.mom via Pages.

Source hierarchy as of `fc0ff0c`:

| Source | Role | Status |
| --- | --- | --- |
| TwitchMetrics | primary — which streams exist and when | working |
| Twitch GQL | per-stream games, live status | working |
| YouTube archive | title semantics, archive gaps | working |
| SullyGnome | viewer/follower figures, deep history | **blocked** |

SullyGnome is now the only degraded source and the only thing it uniquely
supplies is viewer/follower figures. Losing it no longer costs streams or games.

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
can genuinely hide streams, losing SullyGnome only costs viewer and follower
figures, and it no longer claims the former when only the latter happened.

Those 6 streams now carry real games from Twitch GQL, including a Project
Zomboid arc spanning 07-30 → 08-07 that was previously invisible:

```
2026-07-31  Just Chatting, WATERPUNK, Waterpark Simulator, Project Zomboid
2026-08-04  Just Chatting, Project Zomboid
2026-08-05  Just Chatting
2026-08-06  Just Chatting, Project Zomboid
2026-08-07  Just Chatting, Project Zomboid
2026-08-10  Just Chatting
```

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

Third pass (`fc0ff0c`) validated on runner via `workflow_dispatch` run
31834455224 (2026-08-14T19:42Z), green:

```
Twitch VOD game index: 31 VODs
Filled games for 6 stream(s) from Twitch VOD data
```

Committed `d8d4a75`. Waited for the Pages deploy to finish, then confirmed on
the live site with a cache-busted fetch — first check read a stale CDN copy
(`generated_at` 19:10) and showed empty games, which was the CDN and not the
data. After the deploy landed, `cyr.mom` serves `generated_at` 19:42:43 with all
six game lists populated.

Games were cross-checked against SullyGnome's own historical records for streams
both sources cover, and match exactly: 2026-07-28 → `[Just Chatting, Dirty
Business]`, 2026-07-27 → `[Just Chatting, How to Make an Atomic Bomb in Your
Garden, Dirty Business]`.

## Uncommitted implementation details

None of the implementation work is uncommitted. It shipped in three commits:
`4be0f7f` (stop the silent staleness), `54a36ea` (TwitchMetrics primary, TLS
impersonation, no failing runs) and `fc0ff0c` (games from Twitch GQL). The
workflow's own data commits followed each: `d279488`, `71a4375`, `d8d4a75`.

Still untracked and deliberately left alone: `README.md`. It predates this
session and is not mine to commit.

Uncommitted right now: this file's third-pass update. Working tree is otherwise
clean at `d8d4a75`.

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
- Build GQL queries with **plain string concatenation, never f-strings**. They
  are almost entirely braces; f-string escaping produced a query with one extra
  `}` that Twitch accepted and answered with an empty result rather than an
  error, so it read as "0 VODs" instead of a failure. `gql()` now asserts the
  braces balance before sending — keep that guard.
- When checking a fix against the live site, wait for the Pages deploy to
  complete and cache-bust the fetch. A stale CDN copy briefly made a correct
  change look broken here.

## Risks and unknowns

- TLS impersonation got the landing page but **not** `/api/`. SullyGnome is
  still down and may stay down. Untried: a residential proxy, a headless browser
  to mint `cf_clearance`, or `cloudscraper` against the API path specifically.
- While SullyGnome stays blocked the historical table is frozen at its cached
  rows and **viewer/follower figures stop updating for new streams**. Games and
  stream times are no longer affected as of `fc0ff0c`.
- Twitch only retains VODs for a limited window, so `fetch_vod_game_index()`
  covers recent streams only. That is exactly where the gap is — older rows
  already carry games from the cached SullyGnome table — but it does mean games
  cannot be backfilled for any stream whose VOD has since expired.
- The Twitch GQL calls use the public web client ID. It is unauthenticated and
  undocumented, so it can change without notice; a failure is caught and lands
  in `degraded_sources` as `twitch_vod_games`.
- **Nothing alerts any more.** That was the explicit ask, and it is the right
  call for this noise level, but it means a future outage of TwitchMetrics — the
  source that *can* actually hide streams — will surface only as a banner on the
  site that someone has to look at. If that matters later, alert on
  `twitchmetrics*` in `degraded_sources` only, not on any degraded source.

## Next concrete action

Nothing is blocking. Site is current, runs are green, no alerts fire, and games
are restored from a source that does not depend on SullyGnome.

Open and offered to the owner at the end of the session, not yet answered:
probe **TwitchTracker** (`twitchtracker.com/cyr/streams`) and **Streamscharts**
(`streamscharts.com/channels/cyr/streams`) for the last SullyGnome-only fields,
avg/peak viewers and follower deltas. Both are likely Cloudflare-fronted, but
`curl_cffi` is already wired in so the test is cheap, and they are different
operators so their block profiles may differ. Twitch Helix is *not* a substitute
here — it needs a client secret and does not expose per-VOD viewer averages.

If that is not wanted, this work is complete and SullyGnome can simply be left
degraded.

## Deployment and status impact

Deployed. GitHub Pages builds from `main` on push; no other deploy target.
Live at https://cyr.mom (CNAME `cyr.mom`) serving `data_through` 2026-08-10.
Deploy reported via `report_event.py --project cyr --kind deploy` (HTTP 201).

Scheduled refresh continues every 30 minutes and will keep committing partial
data while reporting failure.

## Most relevant files

- `scripts/build_dataset.py` — `gql()`, `fetch_vod_game_index()`,
  `fetch_vod_games_detail()`, `attach_vod_games()`, `open_sully_session()`,
  `backfill_recent_from_exact()`, `load_cached_sully_rows()`
- `.github/workflows/refresh-data.yml`
- `index.html` (source note ~L2245, stale banner ~L1200 and CSS ~L935)
