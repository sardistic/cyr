# Agent Handoff

Updated: 2026-08-18 (sixth pass: stop filing a live stream as a finished one)

## Active objective

Sixth pass, 2026-08-18 — reported as "time since last stream ended: 7h 57m, it
hasn't been that long". The site was reading the elapsed metric off a stream
recorded as 19 minutes long that had actually run 6h03m. Fixed and deployed in
`22998df`; the live site now reads the true end time. Details under "Sixth
pass" below.

### Earlier objective (closed)

Recent streams were missing from the dashboard. Restore them, stop the pipeline
reporting success while serving stale data, and stop depending on SullyGnome —
which has been behind a Cloudflare challenge since 2026-07-31 — for anything
that matters.

All three are done. The site is current, runs are green, and the only field
SullyGnome still uniquely provides is follower deltas.

Reopened 2026-08-15 by a second instance of the same failure, reported as "not
autorunning to fetch new data": the schedule and the runs were fine, but no
remaining source was live enough to notice a new stream, so `data_through` sat
at 2026-08-10 for five days while every run committed a fresh `generated_at`.
Closed by `b7b2cd0` — Twitch's own VOD list now sources new streams, and the
site is current through 2026-08-14.

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

Fourth pass — `f833ded`, hunting the last SullyGnome-only fields:

- Probed the two candidates named as the previous next action. **Both rejected:**
  TwitchTracker `/streams` and all of Streamscharts return 403 under `chrome`
  and `safari17_0` impersonation; TwitchTracker's root page clears the challenge
  but is aggregate-only, with client-rendered (empty in HTML) tables and no
  per-stream links.
- Found the figures on a source already in use. `twitchmetrics_blocks()` replaces
  the `(.*?)</li>` block regex, which stopped at the first *nested* `</li>` — the
  per-game breakdown — and discarded everything after it, including the avg/peak
  viewer numbers. All 15 stream-log entries now carry them; VOD/log counts
  unchanged at 31/15.
- `attach_viewer_stats()` fills only rows with no figures. SullyGnome wins where
  it has data: the two sources poll independently and disagree a few percent
  (2026-06-18 — Sully 1116/1247, TwitchMetrics 1145/1240), so mixing them within
  one stream would be worse than a gap.
- **Follower deltas remain SullyGnome-only.** No source found for them.

Fifth pass — `b7b2cd0`, the pipeline could not see new streams:

- Reported as "not autorunning to fetch new data". The automation was fine —
  the schedule fired every 30 minutes, every run was green, every run pushed a
  commit. Those commits only moved `generated_at`: `data_through` had been
  frozen at 2026-08-10 for five days while cyr streamed on 08-14.
- Cause: the fourth pass left **TwitchMetrics as the only source that can
  introduce a stream** (SullyGnome serves cached rows only). TwitchMetrics
  indexes on its own schedule and simply had not picked the 08-14 stream up —
  verified directly, its `/videos` page tops out at VOD `2842524127` (08-10).
  A source that lags is invisible here: nothing fails, the run just re-fetches
  what it already had.
- `parse_twitch_vods()` reads the VOD list from the Twitch GQL endpoint already
  used for live status and games. Twitch publishes a VOD when the stream ends,
  so it sees new streams immediately. Rows join the same `exact_rows` merge that
  feeds `backfill_recent_from_exact()`, so no new merge path was needed.
- `stitch_split_vods()` merges VODs that are one stream split by a reconnect.
  The 08-14 stream arrived as two VODs 14 seconds apart (`2846356160` +
  `2846385201`); untreated they would count as two streams and put a bogus
  sub-hour entry in the gap stats. Checked before adding: zero such pairs exist
  in the previous 36 exact rows, so this changes no historical figure.
- The merge no longer lets an empty field overwrite a populated one, and
  backfilled rows are tagged with the upstream that surfaced them
  (`twitch_vod_backfill`) rather than a hardcoded `twitchmetrics_backfill`.

Sixth pass — `22998df`, a live stream was being filed as a finished one:

- Twitch publishes a VOD when the stream *starts* and reports the length it has
  reached so far. The 30-minute refresh that lands mid-stream therefore reads a
  6-hour stream as however many minutes it was in. `backfill_recent_from_exact()`
  only ever added rows, so that first partial reading was permanent: the 08-17
  stream sat on the site as "19 minutes, ended 21:25" and the 08-16 one as
  "14 minutes". Every derived metric hangs off `last_stream.ended_at_iso`, so
  the site claimed 7h57m since the last stream ended when the real figure was
  2h20m — along with the wrong percentile, conditional probabilities and
  median-target time.
- `drop_in_progress_vods()` keeps the VOD Twitch is still writing to out of the
  completed set. Live status is the signal; the VOD's own end time (start +
  length within ~2 minutes of now) is the backstop for when `fetch_twitch_live()`
  fails, at the cost of holding a genuinely finished stream back one cycle. It
  runs *before* `stitch_split_vods()` so a reconnect cannot fold the still-growing
  VOD into the finished one and freeze that row too.
- `refresh_backfilled_row()` lets a later run correct a row already filed from a
  partial reading — that is what repaired 08-16 (14m → 5h02m) and 08-17
  (19m → 6h03m) in place, with no data surgery. Only rows this pipeline
  backfilled are touched; SullyGnome's own figures are left alone, and a shorter
  reading never shrinks a row.
- The front end needed no change: `applyLiveMode()` already overrides the whole
  elapsed panel while live, so excluding the in-progress VOD costs nothing there.

## Current behavior

Shipped in six commits — `4be0f7f`, `54a36ea`, `fc0ff0c`, `f833ded`,
`b7b2cd0`, `22998df` — on `main`, deployed to https://cyr.mom via Pages.

Source hierarchy as of `b7b2cd0`:

| Source | Role | Status |
| --- | --- | --- |
| Twitch GQL | primary — which streams exist and when; games; live status | working |
| TwitchMetrics | avg/peak viewers; corroborates the VOD list | working, lags |
| YouTube archive | title semantics, archive gaps | working |
| SullyGnome | follower deltas, deep history, viewers where present | **blocked** |

SullyGnome is the only degraded source, and after `f833ded` the only field it
uniquely supplies is **follower deltas**. Losing it costs no streams, no games,
and — once TwitchMetrics logs catch up — no viewer figures either.

As of `b7b2cd0` the recent end of the timeline no longer depends on any scraped
third party: Twitch itself supplies new streams. TwitchMetrics lagging is now a
metadata delay, not a missing stream.

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

Fourth pass (`f833ded`) validated on runner via `workflow_dispatch` run
31844811545, green, with VOD/log counts unchanged at 31/15. The viewer fill is a
**no-op today** and that is expected: TwitchMetrics stream logs currently reach
back only to 2026-07-03, and every stream in that window already has SullyGnome
figures. The plumbing was verified directly rather than assumed — all 15 log
entries parse avg/peak (e.g. 2026-06-08T03:10:59Z → 1195/1257), and blanking
three rows and re-running `attach_viewer_stats()` refilled all three.

Fifth pass (`b7b2cd0`) validated against a scratch copy of `data/` first — one
stream backfilled (`2026-08-14T22:33:47Z via twitch_vod_backfill`), the split
VOD stitched to 06:42:00, games filled, rows 2464 → 2465. Re-ran in place to
confirm idempotency: second run backfilled nothing, 2465 rows, 2465 unique
starts, no duplicates. Before adding the stitcher, the existing 36 exact rows
were checked for adjacent VODs within 30 minutes of each other's end — zero, so
no historical figure moves.

Then on the runner via `workflow_dispatch` run 31912676079, green, same output,
committed `84a7dbf`. Pages deploy 31912700968 succeeded and a cache-busted fetch
of `cyr.mom/data/stream-data.js` serves `generated_at` 2026-08-15T22:39:22Z with
`data_through` **2026-08-14T22:33:47Z**.

Sixth pass (`22998df`) — ground truth first: Twitch GQL says VOD `2849049662`
started 2026-08-17T21:06:29Z and ran 21770s, ending 03:09:19Z. The shipped data
said it ended 21:25.

Unit-checked both new functions against that case: live status identifies the
in-progress VOD; with live status missing the end-time backstop catches it; a
stream that finished an hour ago is kept; a live *reconnect* does not drop the
earlier finished VOD of the same stream; the refresh is idempotent, never
shrinks a row, and skips non-backfilled rows. `parse_ended_at()` round-trips the
corrected `ended_at` string.

Then the full build against a scratch copy of `data/`: rows 2467 → 2467, no
additions, no removals, no duplicate starts, gap count unchanged at 2466, median
17.91h unchanged, mean 28.00 → 27.99 (the two corrected gaps). `last_stream`
moved from "19 minutes, ended 21:25" to "363 minutes, ended 03:09". A second run
corrected nothing — idempotent.

Ran in place, committed and pushed as `22998df`; the scheduled run's data commit
had landed first, so the rebase kept the corrected data files. Pages deploy for
`22998df` succeeded and a cache-busted fetch of `cyr.mom/data/stream-data.js`
serves `ended_at_iso` **2026-08-18T03:09:00+00:00** — 2h26m elapsed at the time
of the check, against the 7h57m that was reported.

## Uncommitted implementation details

None of the implementation work is uncommitted. It shipped in six commits:
`4be0f7f` (stop the silent staleness), `54a36ea` (TwitchMetrics primary, TLS
impersonation, no failing runs), `fc0ff0c` (games from Twitch GQL), `f833ded`
(recover TwitchMetrics viewer figures), `b7b2cd0` (Twitch VOD list as a
stream source) and `22998df` (don't file a live stream as a finished one). The
workflow's own data commits followed each: `d279488`, `71a4375`, `d8d4a75`,
`84a7dbf`; `22998df` carried its own corrected data files.

Still untracked and deliberately left alone: `README.md`. It predates this
session and is not mine to commit.

Worth knowing: that untracked `README.md` is the *only* thing in the working
tree, and it is enough to make the handoff hook read the repository as dirty on
every session — an untracked file counts. So expect the Stop hook to ask for a
handoff update once per session even when everything is committed and pushed.
Either commit or remove `README.md` (an owner decision, not an agent one) and
that stops.

Nothing is uncommitted. The fifth-pass code shipped as `b7b2cd0` and this file's
fifth-pass update as `69394dd`; the sixth pass shipped as `22998df` with its
corrected data files in the same commit. All pushed. The working tree holds only
the untracked `README.md` noted above.

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
- **Green runs are not evidence the data moved.** Every run commits, because
  `generated_at` always changes, so the commit log looks alive even when no
  source produced anything new. Check `data_through`, never the run status or
  the commit timestamps. This is exactly what hid the five-day freeze.
- A scraped aggregator lagging looks identical to nothing having happened. Keep
  at least one first-party source (Twitch GQL) able to introduce a stream on its
  own; do not let the pipeline's recent end depend solely on a third party.
- **A VOD is published when the stream starts, not when it ends**, and its
  `lengthSeconds` grows while the stream runs. Anything reading that list has to
  ask whether the newest entry is still recording — `drop_in_progress_vods()`
  does. Sixth pass fixed this once; keep it in mind for any new VOD consumer.
- **Backfill that only ever adds makes a bad first reading permanent.** Rows
  matched as "already known" used to be skipped outright, so a partial duration
  captured mid-stream was never revisited. That is why
  `refresh_backfilled_row()` exists — a matched row is now an opportunity to
  correct, not just a reason to skip.

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
  call for this noise level, but it means a future outage will surface only as a
  banner on the site that someone has to look at. If that matters later, alert
  on `twitch_vods` in `degraded_sources` — after `b7b2cd0` that is the source
  that can actually hide streams — not on any degraded source.
- **A lagging source still fails silently.** `b7b2cd0` fixed the case that bit
  us, but nothing yet notices `data_through` standing still while the runs stay
  green. If Twitch GQL breaks the same way, the same five-day freeze recurs with
  no signal. The cheap guard would be to fail, or at least warn, when
  `data_through` has not moved in N days *and* a live stream was observed since.

## Next concrete action

**None required.** Site is current through 2026-08-17 with the true stream end
time, runs are green, and new streams reach the dataset from Twitch itself
within a refresh cycle of going *offline*.

One thing to watch on the next live stream: the elapsed metric should stay on
the *previous* stream's end while `applyLiveMode()` covers the panel, and the
finished stream should appear at full duration on the first refresh after he
goes offline. That path has been unit-checked but not yet seen live.

Two optional threads, higher value first:

1. **Staleness guard.** Nothing detects `data_through` standing still while runs
   stay green — the failure mode of this fifth pass. See the last entry under
   Risks for the shape of a cheap check.
2. **Follower deltas** still have no non-SullyGnome source. Untried avenues, in
   order of likely payoff:
   - Twitch Helix `/channels/followers` returns only a *current* total, so it
     would need the pipeline to snapshot it per run and difference successive
     values — that yields deltas going forward but never recovers history, and
     it needs a client secret in repo secrets.
   - Get past Cloudflare on `sullygnome.com/api/` (headless browser for
     `cf_clearance`, or a proxy with better IP reputation). The landing page
     already clears; only `/api/` does not.

If neither is wanted, leave SullyGnome degraded. Nothing depends on it.

## Deployment and status impact

Deployed. GitHub Pages builds from `main` on push; no other deploy target.
Live at https://cyr.mom (CNAME `cyr.mom`) serving `data_through` 2026-08-17.
Deploy reported via `report_event.py --project cyr --kind deploy`.

Scheduled refresh continues every 30 minutes. It commits on every run because
`generated_at` always changes, and it exits non-zero only on a total data
failure — so read `data_through`, not the run status, to tell whether the data
actually moved.

## Most relevant files

- `scripts/build_dataset.py` — `gql()`, `parse_twitch_vods()`,
  `drop_in_progress_vods()`, `refresh_backfilled_row()`,
  `stitch_split_vods()`, `fetch_vod_game_index()`, `fetch_vod_games_detail()`,
  `attach_vod_games()`, `open_sully_session()`, `backfill_recent_from_exact()`,
  `load_cached_sully_rows()`, `twitchmetrics_blocks()`, `block_viewer_stats()`,
  `attach_viewer_stats()`
- `.github/workflows/refresh-data.yml`
- `index.html` (source note ~L2245, stale banner ~L1200 and CSS ~L935)
