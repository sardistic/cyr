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
