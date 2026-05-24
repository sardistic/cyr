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


def main():
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
    monthly = Counter(d[:7] for d in archive_dates)

    payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "sources": {
            "twitchmetrics_stream_logs": len(stream_logs),
            "twitchmetrics_vods": len(vods),
            "exact_merged_rows": len(exact_rows),
            "youtube_archive_segments": len(archive_segments),
            "youtube_archive_grouped_dates": len(archive_groups),
        },
        "stats": {
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
        },
        "exact_rows": exact_rows,
        "archive_grouped_dates": archive_groups,
    }

    (DATA_DIR / "stream-data.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

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

    print(json.dumps(payload["sources"], indent=2))
    print(json.dumps(payload["stats"]["exact_gap_hours"], indent=2))
    print(json.dumps(payload["stats"]["archive_gap_days"], indent=2))


if __name__ == "__main__":
    main()
