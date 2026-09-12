# Agent Handoff

Updated: 2026-09-12 (ninth pass: pipeline guards, push race — `7ac2fa2`, `b0d91d2`)

## Active objective

Ninth pass, 2026-09-11 — "fix that", against the open items this session had
flagged. Three shipped as `7ac2fa2`: the staleness guard that has been outstanding
since the fifth pass, `safe_stat()` so a derived stat cannot fail a run, and
`DAY_ORIGIN_HOUR` de-duplicated. One item was **not** done and needs a decision
from the owner — see "Next concrete action".

### Eighth pass objective (closed)

Eighth pass, 2026-09-11 — "fix that and anything else you see", following the
candle chart. Three fixes shipped as `582d445`: the UTC-to-CT conversion the page
had been asserting was wrong, the schedule cards' day-of-week scoring was keyed to
a stale hardcoded range, and the data file went stale behind the CDN. Details
under "Eighth pass" below.

### Seventh pass objective (closed)

Seventh pass, 2026-09-11 — feature request: a candlestick chart under the 7-day
schedule strip showing modelled windows for the next 7 days and observed sessions
for the last 7, with stream length readable off the candle. Built, verified in a
headless browser against live data, and **shipped as `77d19ff`** on `main`.
Details under "Seventh pass" below.

### Earlier objective (closed)

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

## Ninth pass — pipeline guards (`7ac2fa2`, workflow fix)

**The refresh workflow could lose a whole run to a push race.** Found by running
one: `gh workflow run refresh-data.yml` was dispatched to verify the new builder
code on the runner, a docs commit was pushed while it was in flight, and the job
died at `git push` with a non-fast-forward — discarding data it had already
fetched from every source. The step ran `commit && push` with no rebase, so
*anything* landing on `main` during the ~90 seconds a run takes would do this. It
now retries up to three times, rebasing onto `origin/main` between attempts and
resolving a conflict in `data/` in favour of the run's own build outputs, which
are regenerated whole and always supersede what is upstream. A `concurrency` group
keeps two refresh runs from racing each other into the same conflict, queued rather
than cancelled so a run that has already hit the sources gets to finish.

That dispatched run did prove the thing it was meant to prove: the pipeline itself
ran clean on the runner. TwitchMetrics 15 logs / 43 VODs, SullyGnome fell back to
cache as expected, Twitch VOD games hit a transient GQL "service error" and
degraded properly, **no `stat_*` entries** — so `compute_dow_profile()` and the
`_ct` histograms computed without throwing on real runner data. The only failure
was the push.


- **Staleness guard.** `check_pipeline_freshness()` plus `stats.last_live_seen`,
  carried across runs. Degrades with a `stale_pipeline` entry when a live sighting
  at least `STALE_LIVE_HOURS` (24) old is still newer than `data_through`. Rationale
  and limits in the 2026-09-11 DECISIONS entry. The site gives it its own banner —
  "streams are happening and not reaching the dataset" — rather than the
  "upstream source unavailable" copy, which would have been misleading.
  **It is armed but has never fired, and cannot until a run observes him live:**
  `last_live_seen` is currently null.
- **`safe_stat()`.** `dow_hour` and `dow_profile` now degrade instead of aborting
  the run. This was the risk flagged when they were added — both were called inline
  in the payload, outside the degraded-source handling.
- **`DAY_ORIGIN_HOUR` de-duplicated.** The builder ships
  `stats.day_origin_hour`; the page reads it and keeps the literal only as a
  fallback for older payloads. The axis, tick labels and the caption all derive from
  it, so a change in the builder moves the chart rather than silently shifting the
  modelled candles against the observed ones.

## Eighth pass — CT labelling, scoring, freshness (`582d445`)

All three came from the same root: the page presents CT but reasoned in UTC.

1. **The UTC-to-CT claim was false.** `drawDowChart()`, `drawDowMini()`, the two
   Schedule Patterns headings and `buildSchedule()`'s summary all asserted
   "Mon UTC = Sun night CT" and shifted weekday labels a day back on that basis.
   Measured over the full history, a UTC Monday is a **CT Monday 73%** of the time
   and a CT Sunday only 27%; the start-hour mode is 4 PM CT, not late evening.
   `compute_dow_hour()` now also emits `dow_ct` and `hour_ct`, bucketed on the same
   6 AM CT stream day as `dow_profile`, and every user-facing chart reads those and
   says CT. The UTC series stay in the payload as the fallback for an older one.
   The start-hour chart is materially more readable as a result: in CT the
   distribution is one clean peak at 4 PM instead of a curve split across the
   midnight wrap.
2. **The schedule cards' DOW range was hardcoded** at `dowMin = 291, dowMax = 430`
   against a real spread of 216–436. Quiet days scored *negative*, which damped
   them — by accident. Deriving the range from the data removed the accident and
   pushed Saturday and Sunday to "High", the two quietest days in the record. So
   the DOW term became a **multiplier** on the blended score (`dowCount / dowMax`),
   which damps a quiet day at every horizon rather than only at the far end where
   the additive term already carried weight. Cards now read Sat "Possible", Sun
   "Low", Mon "Peak". The peak branch keys off whichever day actually leads instead
   of a hardcoded `'Mon'`.
3. **Data went stale behind the CDN.** The workflow regenerates
   `data/stream-data.js` every 30 minutes, but the edge serves it with
   `max-age=14400` at a fixed URL, so a visitor on a warm cache could sit on
   four-hour-old data while the page reported it as current — wrong live status,
   wrong elapsed clock, wrong "checked at". `refreshData()` re-fetches the payload
   with `cache: 'no-store'` and a cache-busting query on load, every 5 minutes, and
   on `visibilitychange`, repainting only when `generated_at` has moved. It refills
   `stats` in place so the reference every render function closes over stays valid,
   and a failed fetch keeps what is already on screen.

   Corrected in `6a34c92`: the first cut compared `generated_at` to decide whether
   anything had moved. That timestamp comes from the refresh workflow, so a rebuild
   that changes the stats without rerunning it — adding a field computed from rows
   that were already present, which is what both of this session's commits did —
   serves a different payload under an unchanged timestamp and was skipped. Caught
   on the live site: cyr.mom was holding the cached `stream-data.js` and never
   picked up `dow_ct`. It now compares the response body against the last one
   accepted, which also means the first fetch after load repaints once and thereby
   recovers any page served new HTML against a stale cached payload.

## Seventh pass — session candles (`77d19ff`)

- `scripts/build_dataset.py`:
  - `DAY_ORIGIN_HOUR = 6` and `stream_day_offsets()` express every session as hours
    since 6 AM CT on its "stream day" (see the 2026-09-11 entry in DECISIONS.md for
    why 6 AM and not noon or midnight).
  - `compute_dow_profile()` emits `stats.dow_profile`: per CT weekday, the p10/p25/
    p50/p75 start offset, p50/p90 end offset, p25/p50/p75 duration, `active_rate`
    (share of that weekday with any stream), `n` and `window_days`. Quantiles come
    from the last 730 days; a weekday with under 8 recent samples falls back to the
    full history rather than projecting off two or three streams.
  - `last_n_stream_details()` now returns 24 streams, not 8, so the observed week is
    always covered even after a dense run of short sessions.
- `index.html` — new `.candle-strip` between the schedule strip and the hero:
  - 14 columns, 7 observed then 7 modelled, y axis 6 AM → 6 AM CT. Observed
    sessions draw as solid green candles (body height *is* the session length);
    modelled days draw as hollow violet candles, body median start → median end,
    dashed wick p10 start → p90 end, opacity scaled by `active_rate`.
  - A live stream draws as a green candle ending at the current time with a dashed
    open cap; a session running past its day's end gets a chevron and keeps its true
    end time in the tooltip.
  - Per-column hover tooltip, a generated summary line, and a "View as table"
    fallback carrying the same numbers.
  - The strip hides itself when `dow_profile` is absent, so an older cached payload
    degrades rather than erroring.
- Verified with Playwright at 1280px and 430px: no console errors, no page-level
  horizontal overflow, tooltips and the table populate. First checked against
  shifted cached streams, then against the real data pulled in during the rebase —
  6 of the last 7 days streaming, 41h42m on air, lengths from 0.9h to 13.2h, all
  legible as candle bodies.

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

As of the seventh pass the header carries two panels: the existing 7-day schedule
strip, and below it a **Session Candles** chart covering the last 7 days and the
next 7 on one 6 AM–6 AM CT time-of-day axis.

- Observed days draw one solid green candle per session, start to end, so body
  height is the session length; two sessions stack, a live stream ends at the
  current time with a dashed cap, and a day with no stream shows a muted dash.
- Modelled days draw a hollow violet candle: body median start to median end,
  dashed wick p10 start to p90 end, opacity scaled by how often that weekday has
  a stream. A length figure per column prints actual hours (past) or the modelled
  median (ahead).
- Hovering a column gives exact times, games, weekday rate and sample size;
  "View as table" carries the same numbers for anyone not using a pointer.
- The panel removes itself when `stats.dow_profile` is missing, so a payload
  built by an older revision of the builder degrades instead of erroring.

Everything below this line describes the data pipeline, unchanged by that pass
except for the two new stats.

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

Ninth pass:

- Workflow push step simulated locally against a real bare remote, three cases,
  all passing: nothing else landed (pushes first attempt); an unrelated commit
  landed mid-run (rebases, pushes on the second attempt, both the data and the
  other commit survive); a competing data commit landed mid-run (rebases, conflict
  resolved to this run's outputs, both commits in history). The first version of
  that harness passed for the wrong reason — the competing push was silently
  failing so the rebase path never ran — which is why the setup steps now abort
  loudly.
- Verified on the runner via `workflow_dispatch` (run 34667936716): pipeline clean,
  no `stat_*` degradation, failure isolated to the push.
- `check_pipeline_freshness()` unit-tested against eight cases, all passing: the
  August failure (live seen 5d ago, `data_through` 6d back) fires; a genuine quiet
  period where the data recorded the last stream does not; a stream live right now
  does not; a sighting 6h old is inside the grace window and does not; 25h old does;
  no sighting, no `data_through`, and unparseable timestamps are all silent and do
  not throw. Carry-forward checked in both directions — a live run stamps a fresh
  sighting, an offline run preserves the previous one.
- `safe_stat()` checked on both paths: passes the value through, and on a throw
  returns the default with `stat_<name>` appended to `degraded_sources`.
- Banner branches driven from injected payloads over HTTP: a `stale_pipeline` entry
  produces the new copy, a `sullygnome` entry still produces the old copy unchanged,
  and no console errors in either.
- `day_origin_hour` verified three ways: read from the payload (6), falls back to 6
  when the field is absent, and follows the builder to 9 — with the axis and caption
  moving with it.
- Data refresh regression re-run after all of it; still picks up a changed payload,
  no-ops on an unchanged one.

Eighth pass:

- UTC→CT confusion matrix computed from all 2,467 rows before changing anything:
  UTC Mon → CT Mon 73% / CT Sun 27%, and similarly same-day for every other
  weekday (Sat is the loosest at 58/42). The page's one-day shift was the minority
  case in every column.
- Schedule cards read back from the rendered DOM after each scoring change. The
  intermediate state — Sat "High", Sun "Likely" — is what caught the hardcoded
  range having been load-bearing; final state is Sat "Possible", Sun "Low", Mon
  "Peak", weekdays "High".
- `refreshData()` tested against a real HTTP server: loaded the page, rewrote
  `stream-data.js` underneath it, and confirmed the page picked up the new
  `generated_at`, `data_through` and stream count with no reload, kept
  `D.stats === stats`, no-opped on an unchanged payload, and left the candle panel
  drawn. No console errors. (Over `file://` the fetch is blocked by the browser and
  the catch swallows it — that is why this had to be tested over HTTP.)
- Both rebuilt charts screenshot-checked; `node --check` on the page script;
  `py_compile` on the builder.

Seventh pass (chart), all local:

- `python -m py_compile scripts/build_dataset.py`; `compute_dow_profile()`
  exercised against no rows, one row, and a row with no timestamp (returns `{}`,
  one weekday, `{}` respectively).
- Day-origin choice measured, not guessed: over the last two years a noon origin
  leaves 9.1% of sessions running past the end of their day against 1.1% at 6 AM
  (4 AM 4.0%, 8 AM 1.8%, 10 AM 3.3%).
- Chart palette run through the dataviz validator against the page's `--paper`
  surface. Green + violet pass CVD separation, the normal-vision floor, chroma
  and contrast; they fail only the dark-mode lightness band, which every existing
  token on this page fails. Green + blue + violet together failed the
  normal-vision floor at ΔE 14.8, which is why the chart carries two hues and
  distinguishes observed from modelled by fill, not by a third colour.
- Page script extracted and `node --check`ed clean.
- Rendered in headless Chromium at 1280px and 430px: no console errors, no
  page-level horizontal scroll, tooltip and table populate. The observed half was
  exercised by shifting the cached streams into the last-7-day window and faking a
  live session, since the local checkout's data stops at 2026-08-17 and the real
  observed week is currently empty.

Earlier passes:

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

**Nothing is uncommitted.** Ninth pass `7ac2fa2`, eighth `582d445` + `6a34c92`,
seventh `77d19ff` (rebased onto `65d4b80`). All pushed to `main`. The working tree holds only the untracked
`README.md` noted below.

The rebase had to resolve `data/stream-data.json` and `data/stream-data.js`: the
scheduled refresh had committed 360 times since this checkout's base, so the
upstream copies were taken whole and the recompute rerun on top of them to
reinstate `dow_profile` and the wider `recent_streams`. **Do this the same way if
it recurs** — never keep the local data files over upstream's, they are a month
stale; take upstream and recompute.

What shipped in that commit:

- `scripts/build_dataset.py` — `DAY_ORIGIN_HOUR`, `stream_day_offsets()`,
  `compute_dow_profile()`, `recent_streams` widened from 8 to 24, and
  `dow_profile` added to the stats payload.
- `index.html` — `.candle-strip` styles, the panel markup after
  `.schedule-days`, and the chart code (`candleModel()`, `drawCandles()`,
  `buildCandles()`, `candleHover()`, `centerCandleScroll()`) above
  `buildSchedule()`, which now calls `buildCandles()` on every refresh tick.
- `data/stream-data.json` and `data/stream-data.js` — carrying `dow_profile` and
  24 `recent_streams`, recomputed from upstream's current rows. The next
  scheduled run regenerates both from the builder itself.

`docs/agent/DECISIONS.md` gained the 6 AM stream-day entry. It shipped in six commits:
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

- **The schedule model changed, and the cards now forecast differently.** The DOW
  multiplier in `582d445` lowers every non-peak day relative to what the page used
  to publish — Saturday most of all, at roughly half the peak day's factor. That is
  the defensible reading of the record, but it is a model change, not a relabel,
  and nobody has watched it against outcomes yet. If it damps too hard, the factor
  is one expression in `buildSchedule()`.
- **A payload can change without `generated_at` moving.** That is what `6a34c92`
  fixed, and it is worth remembering in the other direction too: `generated_at` is
  a workflow-run timestamp, not a content hash, so nothing should treat it as one.
- **`refreshData()` defeats edge caching for the data file by design.** Every
  visitor now pulls ~11 KB from origin on load and every 5 minutes thereafter
  instead of reading a shared cached copy. That is the point — the file changes
  every 30 minutes — but it does move load onto Pages, and the interval is the
  knob if that ever matters.
- ~~Two `DAY_ORIGIN_HOUR` constants must stay in step~~ — fixed in `7ac2fa2`; the
  builder ships the value and the page reads it.
- **The workflow's data commits can now rebase over other work.** That is the
  point, but it means a data refresh will quietly reorder itself after a code
  commit pushed at the same moment. Harmless for build outputs; worth knowing if
  the job ever starts committing something that is not a build output.
- **The staleness guard cannot fire until a run sees him live.** `last_live_seen`
  starts null and is only stamped when `live_stream` is non-null on some run. So a
  fresh checkout, or a period where Twitch's live-status call breaks at the same
  time as the stream list, leaves the guard silent. It fails safe rather than
  noisy, which is the right direction, but it is not coverage for "the whole Twitch
  path is down".
- `dow_hour` carries both bucketings now (`dow`/`hour_utc` in UTC,
  `dow_ct`/`hour_ct` on the CT stream day). They are not interchangeable — a
  reader that mixes them reintroduces exactly the error `582d445` removed. Anything
  user-facing takes the `_ct` series.
- `active_rate` divides active days by the number of that weekday in the window,
  so a long break reads as a low rate for every weekday in it. That is the honest
  reading of "how often does he stream on a Tuesday" but it is not a conditional
  probability, and it is not what the schedule cards' `score` computes.
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

**Follower deltas — needs an owner decision, not an agent one.** This is the one
item from the flagged list that was not fixed, because both routes require a choice
only the owner can make:

1. Twitch Helix `/channels/followers` returns a *current total* only, so the
   pipeline would have to snapshot it per run and difference successive values.
   That yields deltas going forward but never recovers history, and it needs a
   client secret in repository secrets.
2. Get past Cloudflare on `sullygnome.com/api/` — a headless browser to mint
   `cf_clearance`, or a proxy with better IP reputation. The landing page already
   clears; only `/api/` does not.

Neither is blocked on engineering. Say which and it is a short piece of work; until
then SullyGnome stays degraded and nothing depends on it.

**Watch the next scheduled refresh.** `77d19ff` and `582d445` added
`compute_dow_profile()` and the `_ct` histograms to the stats payload, and both
are called inline rather than inside the degraded-source handling — if either
throws, the whole run fails instead of degrading. Neither has yet run on the
runner; every payload so far came from the local recompute. Confirm one green
scheduled run and that the live page still draws before treating this as settled.

**Then:** watch for the staleness guard's first real firing. It is armed but
unproven against a live incident — the unit tests cover the logic, nothing has
exercised it end to end on the runner.

**Previously closed:**

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

Seventh through ninth passes deployed to https://cyr.mom in four commits:
`77d19ff` (candle chart), `582d445` (CT labelling, scoring, freshness),
`6a34c92` (payload-content comparison) and `7ac2fa2` (pipeline guards), each
pushed to `main` on 2026-09-11 and
each reported via `report_event.py --project cyr --kind deploy`. No
infrastructure touched — this repo is Pages-from-`main`, with no container, host or
tunnel involvement.

**Expect a lag between pushing and seeing a change.** Cloudflare sits in front of
Pages and served the previous `data/stream-data.js` for several minutes after
`77d19ff` went live, during which the new HTML loaded against an old payload and
the candle panel hid itself exactly as its degradation path intends. Verify a
deploy with a cache-busting query (`?cb=$(date +%s)`) before concluding anything is
broken. `582d445` is what stops that from affecting real visitors going forward.

Prior state, unchanged:

Deployed. GitHub Pages builds from `main` on push; no other deploy target.
Live at https://cyr.mom (CNAME `cyr.mom`) serving `data_through` 2026-08-17.
Deploy reported via `report_event.py --project cyr --kind deploy`.

Scheduled refresh is declared as every 30 minutes (`cron: '*/30 * * * *'`) but
**does not run anywhere near that often**: observed intervals on 2026-09-11 were
2–5 hours (06:25, 11:40, 15:57, 19:04, 21:50, 23:53 UTC), which is ordinary GitHub
throttling of scheduled workflows, not a fault. Assume hours, not minutes, when
reasoning about how quickly anything reaches the site — the page's own 5-minute
data re-fetch is what keeps an open tab current between runs, not the cron. It commits on every run because
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
- `scripts/build_dataset.py` — `DAY_ORIGIN_HOUR`, `stream_day_offsets()`,
  `compute_dow_profile()`
- `index.html` — `.candle-strip` CSS ~L830, markup ~L1330, and `candleModel()` /
  `drawCandles()` / `buildCandles()` just above `buildSchedule()`
