import csv
import html
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0"}
CHANNEL_ID = "37522866"
TWITCHMETRICS = f"https://www.twitchmetrics.net/c/{CHANNEL_ID}-cyr"
YOUTUBE_ARCHIVE = "https://www.youtube.com/channel/UCtqSew92vbH79xuLLVssbIA/videos"
SULLY_PAGE = "https://sullygnome.com/channel/cyr/5000/streams"
SULLY_CHANNEL_ID = "9451380"


def clean_html(value):
    return html.unescape(re.sub(r"<.*?>", "", value or "")).strip()


def duration_to_seconds(value):
    if not value:
        return 0
    value = value.strip()
    if value.isdigit():
        return int(value)
    parts = [int(p) for p in value.split(":") if p.isdigit()]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def parse_twitchmetrics_stream_logs():
    text = requests.get(f"{TWITCHMETRICS}/streams", headers=UA, timeout=30).text
    blocks = re.findall(r'<li class="list-group-item d-block">(.*?)</li>', text, re.S)
    rows = []
    for block in blocks:
        title = re.search(r"<h6[^>]*>\s*(.*?)\s*</h6>", block, re.S)
        time_match = re.search(
            r'<time[^>]*datetime="([^"]+)"[^>]*>(.*?)</time>\s*-\s*streamed for\s*([^<\n]+)',
            block,
            re.S,
        )
        vod = re.search(r'href="https://www\.twitch\.tv/videos/(\d+)"', block)
        if not title or not time_match:
            continue
        rows.append(
            {
                "source": "twitchmetrics_stream_log",
                "precision": "exact_utc_start",
                "id": vod.group(1) if vod else None,
                "started_at": time_match.group(1),
                "duration_label": time_match.group(3).strip(),
                "duration_seconds": None,
                "title": clean_html(title.group(1)),
                "views": None,
            }
        )
    return rows


def parse_twitchmetrics_vods():
    blocks_re = re.compile(r'<li class="list-group-item d-block">(.*?)</li>', re.S)
    rows_by_id = {}
    for query in ["", "?sort=published_at-desc", "?page=2", "?page=2&sort=published_at-desc"]:
        text = requests.get(f"{TWITCHMETRICS}/videos{query}", headers=UA, timeout=30).text
        for block in blocks_re.findall(text):
            vod = re.search(r'href="https://www\.twitch\.tv/videos/(\d+)"', block)
            duration = re.search(r"<samp>\s*([^<]+?)\s*</samp>", block, re.S)
            title = re.search(r"<h5[^>]*>\s*(.*?)\s*</h5>", block, re.S)
            when = re.search(
                r"([0-9,]+)\s+views\s+-\s+<time[^>]*datetime=\"([^\"]+)\"",
                block,
                re.S,
            )
            if not vod or not title or not when:
                continue
            clean_title = clean_html(title.group(1))
            if "highlight" in clean_title.lower():
                continue
            rows_by_id[vod.group(1)] = {
                "source": "twitchmetrics_vod",
                "precision": "exact_vod_time",
                "id": vod.group(1),
                "started_at": when.group(2),
                "duration_label": duration.group(1).strip() if duration else "",
                "duration_seconds": duration_to_seconds(duration.group(1)) if duration else 0,
                "title": clean_title,
                "views": int(when.group(1).replace(",", "")),
            }
    return list(rows_by_id.values())


def parse_youtube_archive():
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end",
        "1200",
        "--print",
        "%(id)s\t%(duration_string)s\t%(title)s\t%(url)s",
        YOUTUBE_ARCHIVE,
    ]
    proc = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        video_id, duration, title, url = parts
        date_match = re.search(r"\[(\d{1,2}/\d{1,2}/\d{4})\]", title)
        if not date_match:
            date_match = re.search(r"FULL VOD\s+(\d{1,2}/\d{1,2}/\d{4})", title, re.I)
        if not date_match:
            continue
        date = datetime.strptime(date_match.group(1), "%m/%d/%Y").date().isoformat()
        rows.append(
            {
                "source": "youtube_archive_segment",
                "precision": "stream_date_only",
                "date": date,
                "id": video_id,
                "duration_label": duration,
                "duration_seconds": duration_to_seconds(duration),
                "title": title,
                "url": url,
            }
        )
    return rows


def parse_sully_page_info():
    text = requests.get(SULLY_PAGE, headers=UA, timeout=30).text
    match = re.search(r"var PageInfo = (.*?);", text)
    if not match:
        raise RuntimeError("Could not find SullyGnome PageInfo")
    return json.loads(match.group(1))


def parse_sully_games(value):
    parts = (value or "").split("|")
    return [parts[i] for i in range(0, len(parts), 3) if parts[i]]


def fetch_sully_range(session, page_info, range_name, start=0, length=500):
    url = (
        "https://sullygnome.com/api/tables/channeltables/streams/"
        f"{range_name}/{SULLY_CHANNEL_ID}/%20/1/1/desc/{start}/{length}"
    )
    headers = {
        **UA,
        "Referer": SULLY_PAGE,
        "Timecode": page_info["timecode"],
    }
    response = session.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_sullygnome_streams():
    page_info = parse_sully_page_info()
    filter_info = page_info.get("filterInfo", {})
    min_year = int(filter_info.get("minYear", 2017))
    max_year = int(filter_info.get("maxYear", datetime.utcnow().year))
    session = requests.Session()
    rows_by_id = {}
    for year in range(min_year, max_year + 1):
        start = 0
        while True:
            payload = fetch_sully_range(session, page_info, str(year), start=start)
            data = payload.get("data", [])
            for item in data:
                stream_id = str(item.get("streamId") or item.get("startDateTime"))
                rows_by_id[stream_id] = {
                    "source": "sullygnome_stream_table",
                    "precision": "exact_utc_start",
                    "id": stream_id,
                    "started_at": item.get("startDateTime"),
                    "ended_at": item.get("endtime"),
                    "duration_label": f"{item.get('length', 0)} minutes",
                    "duration_seconds": int(item.get("length") or 0) * 60,
                    "title": None,
                    "games": parse_sully_games(item.get("gamesplayed")),
                    "avg_viewers": item.get("avgviewers"),
                    "peak_viewers": item.get("maxviewers"),
                    "followers_gained": item.get("followergain"),
                    "view_minutes": item.get("viewminutes"),
                    "stream_url": item.get("streamUrl"),
                    "range": str(year),
                }
            start += len(data)
            if len(data) == 0 or start >= int(payload.get("recordsTotal") or 0):
                break
    return sorted(rows_by_id.values(), key=lambda r: r["started_at"] or "")


def group_archive_segments(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["date"]].append(row)
    grouped = []
    for date, segments in sorted(groups.items()):
        total_duration = sum(s["duration_seconds"] for s in segments)
        primary = max(segments, key=lambda s: s["duration_seconds"]) if segments else {}
        grouped.append(
            {
                "source": "youtube_archive_grouped",
                "precision": "stream_date_only",
                "date": date,
                "segment_count": len(segments),
                "duration_seconds_sum": total_duration,
                "primary_title": primary.get("title"),
                "titles": [s["title"] for s in segments],
                "urls": [s["url"] for s in segments],
            }
        )
    return grouped


def gap_bins_from_dates(dates):
    parsed = [datetime.fromisoformat(d) for d in sorted(dates)]
    bins = Counter({"1": 0, "2": 0, "3": 0, "4-5": 0, "6-7": 0, "8-14": 0, "15+": 0})
    gaps = []
    for prev, cur in zip(parsed, parsed[1:]):
        gap = (cur - prev).days
        gaps.append(gap)
        if gap <= 1:
            bins["1"] += 1
        elif gap == 2:
            bins["2"] += 1
        elif gap == 3:
            bins["3"] += 1
        elif gap <= 5:
            bins["4-5"] += 1
        elif gap <= 7:
            bins["6-7"] += 1
        elif gap <= 14:
            bins["8-14"] += 1
        else:
            bins["15+"] += 1
    return gaps, dict(bins)


def gap_bins_from_hours(hours):
    bins = Counter({"0-12h": 0, "12-24h": 0, "1-2d": 0, "2-3d": 0, "3-5d": 0, "5-7d": 0, "7+d": 0})
    for gap in hours:
        if gap < 12:
            bins["0-12h"] += 1
        elif gap < 24:
            bins["12-24h"] += 1
        elif gap < 48:
            bins["1-2d"] += 1
        elif gap < 72:
            bins["2-3d"] += 1
        elif gap < 120:
            bins["3-5d"] += 1
        elif gap < 168:
            bins["5-7d"] += 1
        else:
            bins["7+d"] += 1
    return dict(bins)


def exact_gap_hours(rows):
    parsed = sorted(
        datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
        for r in rows
        if r.get("started_at")
    )
    return [
        round((cur - prev).total_seconds() / 3600, 3)
        for prev, cur in zip(parsed, parsed[1:])
    ]


def compute_dow_hour(rows):
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_counts = Counter()
    hour_counts = Counter()
    for row in rows:
        started = row.get("started_at")
        if not started:
            continue
        try:
            dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            dow_counts[dow_labels[dt.weekday()]] += 1
            hour_counts[dt.hour] += 1
        except ValueError:
            continue
    return {
        "dow": {label: dow_counts[label] for label in dow_labels},
        "hour_utc": {str(h): hour_counts[h] for h in range(24)},
    }


def mean(values):
    return sum(values) / len(values) if values else 0


def median(values):
    if not values:
        return 0
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def percentile(values, q):
    if not values:
        return 0
    values = sorted(values)
    pos = (len(values) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(values) - 1)
    if lower == upper:
        return values[lower]
    return values[lower] * (upper - pos) + values[upper] * (pos - lower)


TITLE_FEATURES = {
    "gameplay_arc": [
        "first time", "finishing", "final", "part", "playthrough", "playing", "beat", "ending",
        "death stranding", "subnautica", "minecraft", "gta", "rp", "rust", "elden", "game",
    ],
    "collab": ["@", "with ", "ft ", "feat", "friends", "boys", "girls", "party", "crew"],
    "event_drops": ["drops", "giveaway", "birthday", "event", "sponsored", "keys", "award", "tourney"],
    "challenge": ["not ending", "until", "challenge", "hardcore", "impossible", "speedrun", "marathon"],
    "travel_irl": ["travel", "trip", "japan", "korea", "vegas", "austin", "irl", "hotel", "airport"],
    "food_cooking": ["cook", "cooking", "food", "baking", "pizza", "cake", "eat", "kitchen"],
    "return_marker": ["back", "returned", "return", "been a while", "missed", "hi bb", "i'm back"],
    "recovery_risk": ["sick", "tired", "sleep", "hungover", "late", "short", "recover", "break"],
}

POSITIVE_WORDS = [
    "love", "good", "great", "best", "fun", "cozy", "happy", "finally", "pog", "win",
    "birthday", "free", "giveaway", "drops", "first time", "hi bb",
]
NEGATIVE_WORDS = [
    "bad", "wrong", "hate", "fear", "scary", "cursed", "pain", "dead", "death", "sick",
    "tired", "impossible", "rage", "suffering", "cry", "disaster", "worst",
]


def title_text(row):
    titles = row.get("titles")
    if titles:
        return " ".join(titles)
    return row.get("title") or row.get("primary_title") or ""


def classify_title(text):
    lowered = (text or "").lower()
    features = [name for name, words in TITLE_FEATURES.items() if any(word in lowered for word in words)]
    pos = sum(lowered.count(word) for word in POSITIVE_WORDS)
    neg = sum(lowered.count(word) for word in NEGATIVE_WORDS)
    intensity = 0
    intensity += min(3, (text or "").count("!"))
    intensity += min(2, (text or "").count("?"))
    intensity += 1 if "@" in text else 0
    intensity += 1 if len(re.findall(r"\b[A-Z]{4,}\b", text or "")) >= 3 else 0
    score = pos - neg
    if score >= 2:
        sentiment = "positive"
    elif score <= -1:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    if intensity >= 3:
        intensity_label = "high"
    elif intensity >= 1:
        intensity_label = "medium"
    else:
        intensity_label = "low"
    return {
        "features": features or ["unclassified"],
        "sentiment": sentiment,
        "sentiment_score": score,
        "intensity": intensity_label,
        "intensity_score": intensity,
    }


def summarize_labeled_gaps(labels_to_gaps, total):
    summary = {}
    for label, gaps in sorted(labels_to_gaps.items()):
        if not gaps:
            continue
        summary[label] = {
            "count": len(gaps),
            "share": round(len(gaps) / total * 100, 1) if total else 0,
            "median_gap_days": round(median(gaps), 2),
            "p90_gap_days": round(percentile(gaps, 0.9), 2),
        }
    return summary


def analyze_titles(archive_groups):
    rows = sorted(archive_groups, key=lambda r: r["date"])
    labels_to_gaps = defaultdict(list)
    sentiment_to_gaps = defaultdict(list)
    intensity_to_gaps = defaultdict(list)
    analyzed = []
    for idx, row in enumerate(rows[:-1]):
        cur = datetime.fromisoformat(row["date"])
        nxt = datetime.fromisoformat(rows[idx + 1]["date"])
        gap = (nxt - cur).days
        classified = classify_title(title_text(row))
        analyzed.append({"date": row["date"], "gap_days": gap, **classified})
        for feature in classified["features"]:
            labels_to_gaps[feature].append(gap)
        sentiment_to_gaps[classified["sentiment"]].append(gap)
        intensity_to_gaps[classified["intensity"]].append(gap)
    total = len(analyzed)
    return {
        "sample_size": total,
        "features": summarize_labeled_gaps(labels_to_gaps, total),
        "sentiment": summarize_labeled_gaps(sentiment_to_gaps, total),
        "intensity": summarize_labeled_gaps(intensity_to_gaps, total),
        "rows": analyzed,
    }


def classify_game(games):
    game_set = {g.lower() for g in games or []}
    if any(g in game_set for g in ["just chatting", "special events"]):
        if len(game_set) == 1:
            return "Just Chatting only"
    if any(g in game_set for g in ["grand theft auto v", "rust", "vrchat"]):
        return "Social sandbox/RP"
    if any(g in game_set for g in ["subnautica", "subnautica 2", "death stranding", "kerbal space program"]):
        return "Story/survival arc"
    if any(g in game_set for g in ["food & drink", "cooking simulator"]):
        return "Food/cooking"
    if not games:
        return "Unknown"
    if len(games) >= 3:
        return "Variety stack"
    return "Other games"


def analyze_games(sully_rows):
    rows = sorted(sully_rows, key=lambda r: r["started_at"] or "")
    category_gaps = defaultdict(list)
    category_viewers = defaultdict(list)
    game_counts = Counter()
    for idx, row in enumerate(rows[:-1]):
        if not row.get("started_at") or not rows[idx + 1].get("started_at"):
            continue
        cur = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
        nxt = datetime.fromisoformat(rows[idx + 1]["started_at"].replace("Z", "+00:00"))
        gap = (nxt - cur).total_seconds() / 3600
        category = classify_game(row.get("games") or [])
        category_gaps[category].append(gap)
        if row.get("avg_viewers") is not None:
            category_viewers[category].append(row["avg_viewers"])
        for game in row.get("games") or []:
            game_counts[game] += 1
    categories = {}
    for category, gaps in sorted(category_gaps.items()):
        categories[category] = {
            "count": len(gaps),
            "share": round(len(gaps) / max(1, len(rows) - 1) * 100, 1),
            "median_gap_hours": round(median(gaps), 2),
            "p90_gap_hours": round(percentile(gaps, 0.9), 2),
            "avg_viewers": round(mean(category_viewers[category]), 0) if category_viewers[category] else 0,
        }
    return {
        "categories": categories,
        "top_games": [{"game": game, "count": count} for game, count in game_counts.most_common(12)],
    }


def main():
    sully_rows = parse_sullygnome_streams()
    stream_logs = parse_twitchmetrics_stream_logs()
    vods = parse_twitchmetrics_vods()
    exact_by_key = {}
    for row in vods + stream_logs:
        key = row.get("id") or row["started_at"]
        exact_by_key[key] = {**exact_by_key.get(key, {}), **row}
    exact_rows = sorted(exact_by_key.values(), key=lambda r: r["started_at"])

    archive_segments = parse_youtube_archive()
    archive_groups = group_archive_segments(archive_segments)
    archive_dates = [g["date"] for g in archive_groups]
    archive_gaps, archive_bins = gap_bins_from_dates(archive_dates)
    exact_gaps = exact_gap_hours(exact_rows)
    sully_gap_values = exact_gap_hours(sully_rows)
    title_analysis = analyze_titles(archive_groups)
    game_analysis = analyze_games(sully_rows)
    monthly = Counter(d[:7] for d in archive_dates)
    sully_monthly = Counter(r["started_at"][:7] for r in sully_rows if r.get("started_at"))
    sully_yearly = Counter(r["started_at"][:4] for r in sully_rows if r.get("started_at"))

    payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "sources": {
            "sullygnome_stream_table": len(sully_rows),
            "twitchmetrics_stream_logs": len(stream_logs),
            "twitchmetrics_vods": len(vods),
            "exact_merged_rows": len(exact_rows),
            "youtube_archive_segments": len(archive_segments),
            "youtube_archive_grouped_dates": len(archive_groups),
        },
        "stats": {
            "sully_gap_hours": {
                "count": len(sully_gap_values),
                "mean": round(mean(sully_gap_values), 2),
                "median": round(median(sully_gap_values), 2),
                "max": round(max(sully_gap_values), 2) if sully_gap_values else 0,
                "bins": gap_bins_from_hours(sully_gap_values),
            },
            "exact_gap_hours": {
                "count": len(exact_gaps),
                "mean": round(mean(exact_gaps), 2),
                "median": round(median(exact_gaps), 2),
                "max": round(max(exact_gaps), 2) if exact_gaps else 0,
            },
            "archive_gap_days": {
                "count": len(archive_gaps),
                "mean": round(mean(archive_gaps), 2),
                "median": round(median(archive_gaps), 2),
                "max": max(archive_gaps) if archive_gaps else 0,
                "bins": archive_bins,
            },
            "archive_monthly_counts": dict(sorted(monthly.items())),
            "sully_monthly_counts": dict(sorted(sully_monthly.items())),
            "sully_yearly_counts": dict(sorted(sully_yearly.items())),
            "dow_hour": compute_dow_hour(sully_rows),
            "semantic_analysis": {
                "title_analysis": {k: v for k, v in title_analysis.items() if k != "rows"},
                "game_analysis": game_analysis,
            },
        },
        "sully_streams": sully_rows,
        "exact_rows": exact_rows,
        "archive_grouped_dates": archive_groups,
        "title_semantic_rows": title_analysis["rows"],
    }

    (DATA_DIR / "stream-data.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    dash = {"generated_at": payload["generated_at"], "sources": payload["sources"], "stats": payload["stats"]}
    (DATA_DIR / "stream-data.js").write_text("window.__SD=" + json.dumps(dash) + ";", encoding="utf-8")

    with (DATA_DIR / "exact-streams.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "source",
                "precision",
                "id",
                "started_at",
                "duration_label",
                "duration_seconds",
                "views",
                "title",
            ],
        )
        writer.writeheader()
        for row in exact_rows:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})

    with (DATA_DIR / "sully-streams.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "started_at",
                "duration_seconds",
                "avg_viewers",
                "peak_viewers",
                "followers_gained",
                "games",
                "stream_url",
            ],
        )
        writer.writeheader()
        for row in sully_rows:
            output = {k: row.get(k) for k in writer.fieldnames}
            output["games"] = ", ".join(row.get("games") or [])
            writer.writerow(output)

    with (DATA_DIR / "archive-grouped-dates.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "segment_count",
                "duration_seconds_sum",
                "primary_title",
            ],
        )
        writer.writeheader()
        for row in archive_groups:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})

    with (DATA_DIR / "title-semantics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "gap_days", "features", "sentiment", "sentiment_score", "intensity", "intensity_score"],
        )
        writer.writeheader()
        for row in title_analysis["rows"]:
            output = {k: row.get(k) for k in writer.fieldnames}
            output["features"] = ", ".join(row.get("features") or [])
            writer.writerow(output)

    print(json.dumps(payload["sources"], indent=2))
    print(json.dumps(payload["stats"]["sully_gap_hours"], indent=2))
    print(json.dumps(payload["stats"]["exact_gap_hours"], indent=2))
    print(json.dumps(payload["stats"]["archive_gap_days"], indent=2))
    print(json.dumps(payload["stats"]["semantic_analysis"], indent=2))


if __name__ == "__main__":
    main()
