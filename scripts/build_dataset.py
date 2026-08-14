import csv
import html
import json
import re
import subprocess
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    # curl_cffi impersonates a real browser's TLS/JA3 fingerprint. SullyGnome sits
    # behind a Cloudflare challenge that plain requests cannot pass.
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CHANNEL_ID = "37522866"
TWITCHMETRICS = f"https://www.twitchmetrics.net/c/{CHANNEL_ID}-cyr"
YOUTUBE_ARCHIVE = "https://www.youtube.com/channel/UCtqSew92vbH79xuLLVssbIA/videos"
SULLY_PAGE = "https://sullygnome.com/channel/cyr/5000/streams"
SULLY_CHANNEL_ID = "9451380"
# Rolling "latest Chrome" — pinned versions (chrome124 etc.) already fail the
# challenge, so don't pin. safari17_0 is the observed working alternate.
SULLY_IMPERSONATE = ("chrome", "safari17_0")
TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # public web client ID


def fetch_twitch_live():
    """Return live stream info dict if cyr is live, else None."""
    try:
        r = requests.post(
            TWITCH_GQL,
            headers={"Client-Id": TWITCH_CLIENT_ID},
            json={"query": '{user(login:"cyr"){stream{id title game{name} createdAt viewersCount}}}'},
            timeout=10,
        )
        stream = r.json().get("data", {}).get("user", {}).get("stream")
        if not stream:
            return None
        return {
            "started_at": stream["createdAt"],
            "game": stream.get("game", {}).get("name", ""),
            "title": stream.get("title", ""),
            "viewers": stream.get("viewersCount", 0),
        }
    except Exception:
        return None


def gql(query):
    if query.count("{") != query.count("}"):
        raise RuntimeError(f"unbalanced GQL query braces: {query}")
    r = requests.post(TWITCH_GQL, headers={"Client-Id": TWITCH_CLIENT_ID}, json={"query": query}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    if payload.get("errors"):
        raise RuntimeError(f"GQL errors: {payload['errors']}")
    return payload.get("data") or {}


def fetch_vod_game_index(limit=100):
    """Game lists per VOD, from Twitch's own GQL — no auth, no Cloudflare.

    SullyGnome used to be the only source of per-stream games. Twitch exposes the
    same thing: a VOD's base game, plus chapter markers when the game changed
    mid-stream. Spot-checked against SullyGnome's old records and they match.

    Returns {vod_id: {"started_at": iso, "games": [...]}}; games may be a single
    entry when the stream never switched category.
    """
    # Plain concatenation, not an f-string: these queries are mostly braces and
    # f-string escaping makes them unreadable and easy to get wrong.
    query = (
        '{user(login:"cyr"){videos(first:' + str(limit) + ",type:ARCHIVE,sort:TIME){edges{node{"
        "id createdAt game{name}"
        "}}}}}"
    )
    data = gql(query)
    edges = (((data.get("user") or {}).get("videos") or {}).get("edges")) or []
    index = {}
    for edge in edges:
        node = edge.get("node") or {}
        if not node.get("id"):
            continue
        base = (node.get("game") or {}).get("name")
        index[str(node["id"])] = {
            "started_at": node.get("createdAt"),
            "games": [base] if base else [],
        }
    return index


def fetch_vod_games_detail(vod_id):
    """Full ordered game list for one VOD via its chapter markers."""
    query = (
        '{video(id:"' + str(vod_id) + '"){moments(first:50,momentRequestType:VIDEO_CHAPTER_MARKERS){edges{node{'
        "description details{... on GameChangeMomentDetails{game{name}}}"
        "}}}}}"
    )
    data = gql(query)
    edges = ((((data.get("video") or {}).get("moments")) or {}).get("edges")) or []
    games = []
    for edge in edges:
        node = edge.get("node") or {}
        name = (((node.get("details") or {}).get("game")) or {}).get("name") or node.get("description")
        if name and name not in games:
            games.append(name)
    return games


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


SULLY_PAGE_INFO_PATTERNS = (
    r"var PageInfo = (.*?);",
    r"var\s+PageInfo\s*=\s*(\{.*?\})\s*;",
    r"\bPageInfo\s*=\s*(\{.*?\})\s*;",
    r"window\.PageInfo\s*=\s*(\{.*?\})\s*;",
)


def open_sully_session():
    """Session that can pass SullyGnome's Cloudflare challenge, if possible.

    Returns (session, impersonation_label, page_response). Note that curl_cffi
    only clears the challenge if we let it send its own browser headers — passing
    our UA dict overrides them and gets the request challenged again — so the
    landing page is fetched here and handed back rather than re-requested.
    """
    if curl_requests is None:
        print("SullyGnome: curl_cffi not installed, falling back to plain requests.")
        session = requests.Session()
        return session, None, session.get(SULLY_PAGE, headers=UA, timeout=30)

    last = None
    for target in SULLY_IMPERSONATE:
        try:
            session = curl_requests.Session(impersonate=target)
            response = session.get(SULLY_PAGE, timeout=30)
            if response.status_code == 200 and "Just a moment" not in response.text[:400]:
                return session, target, response
            last = (session, response)
        except Exception as e:  # noqa: BLE001 - transport error, try the next target
            print(f"SullyGnome: {target} impersonation errored ({type(e).__name__}: {e})")
    session, response = last if last else (curl_requests.Session(impersonate=SULLY_IMPERSONATE[0]), None)
    return session, None, response


def parse_sully_page_info(response):
    text = response.text
    for pattern in SULLY_PAGE_INFO_PATTERNS:
        match = re.search(pattern, text, re.S)
        if not match:
            continue
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

    # Last resort: pull the one field fetch_sully_range actually needs.
    timecode = re.search(r'["\']timecode["\']\s*:\s*["\']([^"\']+)["\']', text)
    if timecode:
        print("SullyGnome: PageInfo block not found, recovered timecode only.")
        return {"timecode": timecode.group(1), "filterInfo": {}}

    # Log enough to diagnose a markup change or a bot challenge from the run log.
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S)
    raise RuntimeError(
        "Could not find SullyGnome PageInfo "
        f"(http {response.status_code}, {len(text)} bytes, "
        f"title={clean_html(title.group(1)) if title else 'none'!r}, "
        f"head={text[:200]!r})"
    )


def parse_sully_games(value):
    parts = (value or "").split("|")
    return [parts[i] for i in range(0, len(parts), 3) if parts[i]]


def fetch_sully_range(session, page_info, range_name, start=0, length=500, impersonating=False):
    url = (
        "https://sullygnome.com/api/tables/channeltables/streams/"
        f"{range_name}/{SULLY_CHANNEL_ID}/%20/1/1/desc/{start}/{length}"
    )
    headers = {
        # Let curl_cffi supply its own browser headers when impersonating —
        # overriding them breaks the fingerprint and re-triggers the challenge.
        **({} if impersonating else UA),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": SULLY_PAGE,
        "Timecode": page_info["timecode"],
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    response = session.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        challenged = "Just a moment" in response.text[:400]
        raise RuntimeError(
            f"stream table API returned http {response.status_code} for {range_name}"
            + (" (Cloudflare challenge on /api/ — landing page cleared but the "
               "API path did not)" if challenged else "")
        )
    return response.json()


def parse_sullygnome_streams():
    session, impersonation, response = open_sully_session()
    if impersonation:
        print(f"SullyGnome: landing page cleared via {impersonation} impersonation.")
    if response is None:
        raise RuntimeError("SullyGnome landing page could not be fetched at all")
    page_info = parse_sully_page_info(response)
    filter_info = page_info.get("filterInfo", {})
    min_year = int(filter_info.get("minYear", 2017))
    max_year = int(filter_info.get("maxYear", datetime.utcnow().year))
    rows_by_id = {}
    for year in range(min_year, max_year + 1):
        start = 0
        while True:
            payload = fetch_sully_range(
                session, page_info, str(year), start=start, impersonating=bool(impersonation)
            )
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


def parse_ended_at(s):
    """Parse SullyGnome ended_at strings like 'Sunday 24th May 2026 06:45' → UTC datetime."""
    if not s:
        return None
    try:
        return datetime.strptime(
            re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s), "%A %d %B %Y %H:%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def end_to_start_gap_hours(rows):
    """Gap from each stream's end to the next stream's start, in hours."""
    pairs = []
    for r in rows:
        if not r.get("started_at"):
            continue
        started = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
        ended = parse_ended_at(r.get("ended_at", ""))
        pairs.append((started, ended))
    pairs.sort(key=lambda x: x[0])
    gaps = []
    for i in range(1, len(pairs)):
        prev_ended = pairs[i - 1][1]
        cur_started = pairs[i][0]
        if prev_ended is None:
            continue
        g = (cur_started - prev_ended).total_seconds() / 3600
        if g >= 0:
            gaps.append(round(g, 3))
    return gaps


def last_n_gap_details(rows, n=20):
    """Last N end-to-start gaps with stream metadata for sparkline display."""
    pairs = []
    for r in rows:
        if not r.get("started_at"):
            continue
        started = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
        ended = parse_ended_at(r.get("ended_at", ""))
        pairs.append((started, ended, r.get("games", []), r.get("started_at")))
    pairs.sort(key=lambda x: x[0])
    result = []
    for i in range(1, len(pairs)):
        prev_ended = pairs[i - 1][1]
        cur_started = pairs[i][0]
        if prev_ended is None:
            continue
        g = (cur_started - prev_ended).total_seconds() / 3600
        if g >= 0:
            result.append({
                "gap_h": round(g, 1),
                "started_at": pairs[i][3],
                "game": pairs[i][2][0] if pairs[i][2] else "",
            })
    return result[-n:]


def last_n_stream_details(rows, n=8):
    """Last N streams with full metadata for timeline and arc card."""
    sorted_rows = sorted(
        (r for r in rows if r.get("started_at")),
        key=lambda r: r["started_at"]
    )
    result = []
    for r in sorted_rows[-n:]:
        ended_dt = parse_ended_at(r.get("ended_at", ""))
        result.append({
            "started_at": r["started_at"],
            "ended_at_iso": ended_dt.isoformat() if ended_dt else None,
            "games": r.get("games", []),
            "duration_h": round(r.get("duration_seconds", 0) / 3600, 1),
            "duration_label": r.get("duration_label", ""),
            "avg_viewers": r.get("avg_viewers", 0),
            "peak_viewers": r.get("peak_viewers", 0),
            "followers_gained": r.get("followers_gained", 0),
        })
    return result


def compute_gap_cdf(gap_values):
    """Sparse CDF: [[hours, cumulative_pct], ...] used by the dashboard for live interpolation."""
    if not gap_values:
        return []
    sv = sorted(gap_values)
    n = len(sv)
    marks = (
        list(range(0, 24, 3))       # 0,3,6,9,12,15,18,21
        + list(range(24, 72, 6))    # 24,30,36,...,66
        + list(range(72, 168, 12))  # 72,84,...,156
        + [168, 240, 336, 504, 720]
    )
    result = []
    for h in marks:
        count = sum(1 for g in sv if g <= h)
        result.append([h, round(count / n * 100, 2)])
    return result


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


def load_cached_sully_rows():
    """Reuse the last good SullyGnome table when the live scrape fails."""
    json_path = DATA_DIR / "stream-data.json"
    if not json_path.exists():
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return payload.get("sully_streams") or []


def attach_vod_games(rows, vod_index):
    """Fill empty `games` from Twitch VOD data, keyed by VOD id then start time.

    Only touches rows that have no games — SullyGnome's list wins where it exists,
    since it also carries the viewer/follower figures alongside.
    """
    if not vod_index:
        return 0
    by_time = {
        v["started_at"][:13]: (vid, v)
        for vid, v in vod_index.items()
        if v.get("started_at")
    }
    filled = 0
    for row in rows:
        if row.get("games"):
            continue
        vid, entry = None, None
        if row.get("id") and str(row["id"]) in vod_index:
            vid, entry = str(row["id"]), vod_index[str(row["id"])]
        elif row.get("started_at") and row["started_at"][:13] in by_time:
            vid, entry = by_time[row["started_at"][:13]]
        if not entry:
            continue
        games = entry["games"]
        try:
            detailed = fetch_vod_games_detail(vid)
            if detailed:
                games = detailed
        except Exception as e:  # noqa: BLE001 - markers are a bonus, base game still applies
            print(f"  VOD {vid} chapter markers unavailable ({type(e).__name__}), using base game")
        if games:
            row["games"] = games
            filled += 1
    if filled:
        print(f"Filled games for {filled} stream(s) from Twitch VOD data")
    return filled


def backfill_recent_from_exact(sully_rows, exact_rows):
    """Add streams that TwitchMetrics knows about but the Sully table is missing.

    Without this a stale Sully table silently caps `last_stream` and the recent
    timeline at whatever date the scrape last succeeded.
    """
    def to_dt(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    known = sorted(to_dt(r["started_at"]) for r in sully_rows if r.get("started_at"))
    # VOD timestamps run a few seconds behind the Sully table's start time for the
    # same stream, so match on a window rather than an exact timestamp.
    tolerance = timedelta(minutes=15)
    added = []
    for row in exact_rows:
        started = row.get("started_at")
        if not started:
            continue
        started_dt = to_dt(started)
        if any(abs(started_dt - k) < tolerance for k in reversed(known)):
            continue
        duration_seconds = row.get("duration_seconds") or 0
        ended_dt = started_dt + timedelta(seconds=duration_seconds) if duration_seconds else None
        added.append({
            "source": "twitchmetrics_backfill",
            "precision": "exact_vod_time",
            "id": row.get("id"),
            "started_at": started,
            # parse_ended_at() re-reads this SullyGnome-style string downstream.
            "ended_at": ended_dt.strftime("%A %d %B %Y %H:%M") if ended_dt else "",
            "duration_label": f"{round(duration_seconds / 60)} minutes" if duration_seconds else "",
            "duration_seconds": duration_seconds,
            "title": row.get("title"),
            "games": [],
            "avg_viewers": None,
            "peak_viewers": None,
            "followers_gained": None,
            "view_minutes": None,
            "stream_url": f"https://www.twitch.tv/videos/{row['id']}" if row.get("id") else None,
            "range": started[:4],
        })
        known.append(started_dt)
    if added:
        print(f"Backfilled {len(added)} recent stream(s) from TwitchMetrics: "
              f"{', '.join(r['started_at'] for r in added)}")
    return sorted(sully_rows + added, key=lambda r: r.get("started_at") or "")


def update_live_only(live_stream):
    """Patch live_stream into existing data files without re-fetching all sources."""
    json_path = DATA_DIR / "stream-data.json"
    if not json_path.exists():
        print("No existing stream-data.json to update.")
        return False
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["generated_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    payload["degraded_sources"] = ["all sources unavailable; live status only"]
    payload["stats"]["live_stream"] = live_stream
    (DATA_DIR / "stream-data.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    dash = {
        "generated_at": payload["generated_at"],
        "data_through": payload.get("data_through"),
        "degraded_sources": payload["degraded_sources"],
        "sources": payload["sources"],
        "stats": payload["stats"],
    }
    (DATA_DIR / "stream-data.js").write_text("window.__SD=" + json.dumps(dash) + ";", encoding="utf-8")
    print(f"Patched live_stream in existing data (generated_at: {payload['generated_at']}).")
    return True


def main():
    live_stream = fetch_twitch_live()
    if live_stream:
        print(f"LIVE: {live_stream['game']} · {live_stream['viewers']} viewers · started {live_stream['started_at']}")
    else:
        print("Not live right now.")

    degraded = []

    # TwitchMetrics is the primary source: it decides which streams exist and
    # when. SullyGnome is fetched afterwards for game/viewer enrichment and deep
    # history, and is allowed to fail.
    try:
        stream_logs = parse_twitchmetrics_stream_logs()
        print(f"TwitchMetrics stream logs: {len(stream_logs)}")
    except Exception as e:
        print(f"TwitchMetrics stream logs FAILED (skipping): {e}")
        traceback.print_exc()
        degraded.append(f"twitchmetrics_stream_logs: {e}")
        stream_logs = []

    try:
        vods = parse_twitchmetrics_vods()
        print(f"TwitchMetrics VODs: {len(vods)}")
    except Exception as e:
        print(f"TwitchMetrics VODs FAILED (skipping): {e}")
        traceback.print_exc()
        degraded.append(f"twitchmetrics_vods: {e}")
        vods = []
    exact_by_key = {}
    for row in vods + stream_logs:
        key = row.get("id") or row["started_at"]
        exact_by_key[key] = {**exact_by_key.get(key, {}), **row}
    exact_rows = sorted(exact_by_key.values(), key=lambda r: r["started_at"])

    # SullyGnome: enrichment only. Games, viewer counts and follower deltas come
    # from here, plus the deep history TwitchMetrics does not expose. A failure
    # costs metadata, not streams.
    try:
        sully_rows = parse_sullygnome_streams()
        print(f"SullyGnome: {len(sully_rows)} streams")
    except Exception as e:
        print(f"SullyGnome unavailable, using cached table: {e}")
        degraded.append(f"sullygnome: {e}")
        sully_rows = load_cached_sully_rows()
        print(f"SullyGnome: fell back to {len(sully_rows)} cached streams")

    if not sully_rows and not exact_rows:
        print("No stream data from any source — refusing to overwrite good data.")
        if update_live_only(live_stream):
            print("Exiting — live status updated, historical data unchanged.")
        sys.exit(1)

    sully_rows = backfill_recent_from_exact(sully_rows, exact_rows)

    # Games for anything SullyGnome could not describe, straight from Twitch.
    try:
        vod_index = fetch_vod_game_index()
        print(f"Twitch VOD game index: {len(vod_index)} VODs")
        attach_vod_games(sully_rows, vod_index)
    except Exception as e:
        print(f"Twitch VOD game lookup FAILED (skipping): {e}")
        traceback.print_exc()
        degraded.append(f"twitch_vod_games: {e}")

    try:
        archive_segments = parse_youtube_archive()
        print(f"YouTube archive: {len(archive_segments)} segments")
    except Exception as e:
        print(f"YouTube archive FAILED (skipping): {e}")
        traceback.print_exc()
        degraded.append(f"youtube_archive: {e}")
        archive_segments = []
    archive_groups = group_archive_segments(archive_segments)
    archive_dates = [g["date"] for g in archive_groups]
    archive_gaps, archive_bins = gap_bins_from_dates(archive_dates)
    exact_gaps = exact_gap_hours(exact_rows)
    sully_gap_values = end_to_start_gap_hours(sully_rows)
    title_analysis = analyze_titles(archive_groups)
    game_analysis = analyze_games(sully_rows)
    monthly = Counter(d[:7] for d in archive_dates)
    sully_monthly = Counter(r["started_at"][:7] for r in sully_rows if r.get("started_at"))
    sully_yearly = Counter(r["started_at"][:4] for r in sully_rows if r.get("started_at"))

    data_through = max(
        (r["started_at"] for r in sully_rows if r.get("started_at")),
        default=None,
    )

    payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        # Newest stream actually in the data, as opposed to when the build ran.
        "data_through": data_through,
        "degraded_sources": degraded,
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
            "gap_cdf": compute_gap_cdf(sully_gap_values),
            "recent_gaps": last_n_gap_details(sully_rows, n=20),
            "recent_streams": last_n_stream_details(sully_rows, n=8),
            "last_stream": (lambda r: {
                "started_at": r.get("started_at"),
                "ended_at_iso": (lambda e: e.isoformat() if e else None)(
                    parse_ended_at(r.get("ended_at", ""))
                ),
                "games": r.get("games", []),
                "duration_label": r.get("duration_label", ""),
            })(sorted(
                (r for r in sully_rows if r.get("started_at")),
                key=lambda r: r["started_at"]
            )[-1] if any(r.get("started_at") for r in sully_rows) else {}),
            "semantic_analysis": {
                "title_analysis": {k: v for k, v in title_analysis.items() if k != "rows"},
                "game_analysis": game_analysis,
            },
            "live_stream": live_stream,
        },
        "sully_streams": sully_rows,
        "exact_rows": exact_rows,
        "archive_grouped_dates": archive_groups,
        "title_semantic_rows": title_analysis["rows"],
    }

    (DATA_DIR / "stream-data.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    dash = {
        "generated_at": payload["generated_at"],
        "data_through": payload["data_through"],
        "degraded_sources": payload["degraded_sources"],
        "sources": payload["sources"],
        "stats": payload["stats"],
    }
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
    print(f"Data through: {data_through}")

    if degraded:
        # Not an error exit: a degraded source is normal operation now, and a red
        # run every 30 minutes is just noise. The site reports it instead, via the
        # stale banner driven by degraded_sources.
        print("DEGRADED (site will show the banner): " + "; ".join(degraded))


if __name__ == "__main__":
    main()
