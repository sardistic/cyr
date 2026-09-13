# CYR Stream Forecast

A static, fan-made dashboard that estimates when cyr is likely to stream next.

The site combines historical Twitch stream data, VOD/archive metadata, and a small client-side forecasting model to show the current gap since the last stream, conditional probability windows, recent cadence, day-of-week patterns, and a live-mode view when cyr is online.

## What It Shows

- Next-stream estimate based on historical end-to-start gaps.
- Conditional probabilities for the next 24 and 48 hours.
- Recent stream timeline, cadence distribution, monthly/yearly counts, and start-time charts.
- Title and game-state context for active arcs and recent Just Chatting breaks.
- A live banner when Twitch reports that cyr is currently streaming.

## Data

Generated files live in `data/`:

- `stream-data.js` powers the static site in the browser.
- `stream-data.json` contains the full generated model payload.
- `sully-streams.csv` and `exact-streams.csv` contain stream history inputs.
- `title-semantics.csv` contains archive/title-derived semantic features.

The data pipeline is in `scripts/build_dataset.py`. It pulls from public Twitch/TwitchMetrics/SullyGnome/VOD sources and writes the generated dashboard data. Follower deltas for recent streams are approximated by differencing per-run snapshots of the channel's follower total, and are shown with a ≈.

## Updating

The GitHub Actions workflow at `.github/workflows/refresh-data.yml` refreshes the data on a schedule (declared every 30 minutes; GitHub throttles scheduled runs, so in practice every few hours) and commits changed generated files back to the repo. The page also re-fetches the data file itself every few minutes, so an open tab stays current between runs.

To refresh locally:

```bash
pip install requests yt-dlp curl_cffi
python scripts/build_dataset.py
```

Then open `index.html` or serve the repository as a static site.

## Notes

This is an unofficial forecast model, not a real schedule or endorsement from cyr. It is meant to make the historical pattern visible, not to promise when anyone will go live.
