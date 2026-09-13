# Project Decisions

Record durable architectural and operational decisions here.

### Date — Decision title

Context:

Decision:

Consequences:

### 2026-08-14 — No source may silently gate the pipeline

Context: SullyGnome moved behind a Cloudflare challenge on 2026-07-31. It was
the first source fetched and a hard gate: on failure the run patched live status
and returned 0, skipping TwitchMetrics and the YouTube archive entirely. The
workflow reported success for 14 days while the site showed data frozen at
2026-07-30 under a "refreshed today" label. Six streams were missing.

Decision: A failing source degrades the run, it does not end it. SullyGnome
falls back to the cached table from stream-data.json and the remaining sources
still run, with TwitchMetrics backfilling any streams the Sully table lacks. The
payload distinguishes data_through (newest stream present) from generated_at
(when the build ran), and carries degraded_sources; the UI shows a banner from
degraded_sources rather than from days-behind, because genuine multi-week
streaming breaks are normal for this channel. Degraded runs exit 2 so the
partial refresh still commits but the job goes red.

Consequences: The site can no longer claim freshness it does not have. Recent
streams survive any single source outage. The cost is that stream-data.json is
now an input as well as an output, and the workflow stays red for as long as a
source is down — noisy, and it will mask unrelated failures.

### 2026-08-14 — TwitchMetrics is the primary source; a degraded run is not a failure

Context: The 2026-08-14 rework above kept SullyGnome first in the pipeline, so
its Cloudflare outage still shaped every run, and it made degraded runs exit
non-zero. In practice that meant a red workflow every 30 minutes and a mailbox
full of "Run failed" notifications for a condition that was neither new nor
actionable. curl_cffi TLS impersonation was then tested and found to clear
SullyGnome's landing page but not its /api/ path, from both a local IP and a
GitHub runner.

Decision: TwitchMetrics becomes the primary source and decides which streams
exist and when. SullyGnome is demoted to enrichment — games, viewer counts,
follower deltas, deep history — fetched through curl_cffi impersonation and
allowed to fail. Degraded runs exit 0; only a total data failure exits non-zero.
The signal moves entirely onto the site's banner, which distinguishes losing
TwitchMetrics (streams may be missing) from losing SullyGnome (metadata only).

Consequences: Runs are green and quiet, and recent streams no longer depend on
the least reliable source. The cost is that nothing alerts: a future
TwitchMetrics outage — the one source whose loss can actually hide streams —
will surface only on the page. If that becomes a real risk, alert on
twitchmetrics* in degraded_sources specifically, not on any degraded source.

### 2026-09-11 — The stream day runs 6 AM to 6 AM CT

Context: The session-candle chart plots a stream's start and end on a time-of-day
axis. A session that starts at 9 PM and ends at 3 AM is one stream, so a
midnight-anchored day would cut it in two. A noon anchor keeps night sessions
whole but shoves daytime streams onto the previous day's tail, where they run off
the end of the axis: measured over the last two years, 9.1% of sessions ended past
the end of their day at a noon origin against 1.1% at 6 AM.

Decision: Both the builder (`DAY_ORIGIN_HOUR` in `scripts/build_dataset.py`) and
the chart (`DAY_ORIGIN_HOUR` in `index.html`) treat 6 AM CT as the start of a
stream day. `dow_profile` quantiles are bucketed on that basis, so the two
constants must move together.

Consequences: Night sessions draw as one candle and daytime streams sit where they
belong. The residual ~1% — multi-day marathons — are clipped at the axis and
marked with a chevron; the tooltip still reports their true end time. It also
means `dow_profile`'s weekday keys are CT stream-days, which is not the same
bucketing as `dow_hour`'s UTC start-days used by the schedule cards.

### 2026-09-11 — A live sighting is the staleness signal, not elapsed time

Context: In August every source stopped being live enough to notice new streams.
`data_through` stood still for five days while each run committed a fresh
`generated_at`, so the workflow stayed green and the site served stale data under
a "checked just now" label. The 2026-08-14 decision had deliberately moved all
signalling onto the site banner, but nothing computed this condition, so the
banner had nothing to show. The obvious guard — fail when `data_through` has not
moved in N days — cannot work here: this channel genuinely goes quiet for weeks,
and a correct quiet period is indistinguishable from a broken pipeline by elapsed
time alone.

Decision: Use live status as the discriminator. It is fetched by a different call
than the stream list, so a stream observed live that never becomes a recorded
stream is positive evidence that the list is broken rather than the channel being
idle. The build carries `stats.last_live_seen` across runs and appends a
`stale_pipeline` entry to `degraded_sources` when that sighting is at least
`STALE_LIVE_HOURS` (24) old and still newer than `data_through`. The site gives
that its own banner copy, distinct from "an upstream source is down".

Consequences: The failure mode that hid five days of streams now surfaces within a
day of recurring, without false-positiving on quiet weeks. The guard is only as
good as live status: it cannot fire until a run has observed him live at least
once, so a fresh checkout starts with `last_live_seen` null and silent, and if
Twitch's live-status call breaks at the same time as the stream list, nothing
fires. It fails safe — silent, never noisy — which is the right direction for a
banner nobody is paged by.

### 2026-09-11 — Derived stats degrade; only missing data fails a run

Context: The 2026-08-14 rule that no source may silently gate the pipeline was
about fetching. Everything computed afterwards — the histograms, quantiles and
CDFs in the stats block — was still called inline while building the payload, so a
throw in any of them aborted a run that had already collected the streams
successfully.

Decision: `safe_stat()` wraps a derived stat, returning a default and appending
`stat_<name>` to `degraded_sources` on failure. Applied to `dow_hour` and
`dow_profile`; the same treatment fits any other presentational stat.

Consequences: A broken panel costs its own panel. The dashboard degrades a piece
at a time instead of the refresh failing whole, and the page already hides
anything whose stat is missing. The cost is that a genuinely broken computation is
now quiet unless someone reads `degraded_sources`.

### 2026-09-13 — Follower deltas come from differencing per-run totals

Context: SullyGnome was the only source of per-stream follower gains and has been
behind a Cloudflare challenge since 2026-07-31. The two routes considered — a
Twitch Helix client secret in repository secrets, or defeating the challenge with
a headless browser — both needed an owner decision and neither was clean. A third
was overlooked: the unauthenticated Twitch GQL call the builder already makes for
live status and VODs also returns the channel's follower total.

Decision: Each run snapshots the total into `follower_snapshots` at the top level
of `stream-data.json` (kept out of `stats` so it never reaches the dashboard
payload every tab re-fetches), pruned to 90 days. `attach_follower_deltas()` fills
`followers_gained` on rows no source described by differencing the last snapshot
at or before the stream's start from the first at or after its end, requiring each
side of the bracket to be within 8 hours, and marks the row
`followers_gained_source: "snapshot"` with the idle slack recorded. SullyGnome's
own figure is never overwritten. The site renders snapshot-derived figures with a
≈ and omits a missing figure entirely rather than printing zero.

Consequences: Recent streams get a follower figure without any secret, any
credential, or any bot-challenge circumvention. The figure is approximate: runs
land 2–5 hours apart, so a bracket can carry a few hours of idle drift on either
side, and the ≈ is load-bearing. It cannot recover history — a stream only gets a
delta once snapshots exist on both sides of it, so nothing before 2026-09-13 will
ever be filled this way. If the schedule cadence ever tightens the figures tighten
with it, automatically.

