#!/usr/bin/env python3
"""
Daily Watch feed updater for Ultimate Frisbee Pro.

Finds new uploads from trusted channels via the YouTube Data API v3, filters them
with the rules in config.json, and merges them into videos.json.

Embeds only: this script stores links and metadata. It never downloads or re-hosts
video or thumbnails.

Quota: ~2 units per channel per run (playlistItems.list + videos.list), plus a
one-time 1 unit per channel to resolve handles. The free daily quota is 10,000.

Usage:
  YOUTUBE_API_KEY=... python scripts/update_feed.py            # update videos.json
  YOUTUBE_API_KEY=... python scripts/update_feed.py --dry-run  # print, don't write
Standard library only (Python 3.9+).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
API = "https://www.googleapis.com/youtube/v3/"
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VALID_CATEGORIES = {"play", "highlight", "strategy"}
VALID_PLATFORMS = {"youtube", "instagram"}
# Settings a channel entry in config.json may override.
CHANNEL_OVERRIDES = ("maxAgeDays", "minDurationSeconds", "maxDurationSeconds")


def rules_for_channel(config: dict, channel: dict) -> dict:
    """Global rules with the channel's overrides applied."""
    rules = dict(config)
    rules.update({key: channel[key] for key in CHANNEL_OVERRIDES if key in channel})
    if channel.get("requireKeywords") is False:  # e.g. ultimate-only channels posting in Hindi
        rules["requireAnyKeyword"] = []
    return rules


# ---------------------------------------------------------------- API access

class YouTubeClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def get(self, endpoint: str, **params) -> dict:
        params["key"] = self.api_key
        url = API + endpoint + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)

    def uploads_playlist(self, channel: dict) -> tuple[str, str] | None:
        """Returns (uploads playlist id, channel title)."""
        params = {"part": "contentDetails,snippet"}
        if channel.get("channelId"):
            params["id"] = channel["channelId"]
        elif channel.get("handle"):
            params["forHandle"] = channel["handle"]
        elif channel.get("username"):
            params["forUsername"] = channel["username"]
        else:
            return None
        items = self.get("channels", **params).get("items", [])
        if not items:
            return None
        item = items[0]
        return item["contentDetails"]["relatedPlaylists"]["uploads"], item["snippet"]["title"]

    def recent_upload_ids(self, playlist_id: str, limit: int = 15) -> list[str]:
        data = self.get("playlistItems", part="contentDetails", playlistId=playlist_id, maxResults=limit)
        return [i["contentDetails"]["videoId"] for i in data.get("items", [])]

    def video_details(self, video_ids: list[str]) -> list[dict]:
        if not video_ids:
            return []
        data = self.get("videos", part="snippet,contentDetails,status", id=",".join(video_ids[:50]))
        return data.get("items", [])


# ---------------------------------------------------------------- pure helpers

def parse_iso_duration(value: str) -> int | None:
    """'PT1H2M3S' -> 3723."""
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def youtube_id_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    candidate = None
    if host.endswith("youtu.be") and parts:
        candidate = parts[0]
    elif "youtube.com" in host or "youtube-nocookie.com" in host:
        if parts[:1] == ["watch"]:
            candidate = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        elif len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
            candidate = parts[1]
    return candidate if candidate and YOUTUBE_ID.match(candidate) else None


def slugify(text: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "video"


def clean_title(title: str) -> str:
    title = re.sub(r"#\w+", "", title)                      # hashtags
    title = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", "", title)  # emoji
    title = re.sub(r"\s*\|\s*", " | ", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" -|")
    return title


def short_description(text: str, max_length: int = 160) -> str | None:
    text = re.sub(r"https?://\S+", "", text or "")
    text = re.sub(r"#\w+", "", text)
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 20:
            sentence = re.split(r"(?<=[.!?])\s", line)[0]
            if len(sentence) > max_length:
                sentence = sentence[: max_length - 1].rsplit(" ", 1)[0] + "…"
            return sentence
    return None


def contains_any(text: str, keywords: list[str]) -> bool:
    text = text.lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", text) for k in keywords)


def categorize(title: str, description: str, config: dict, default: str) -> str:
    text = f"{title} {description}"
    rules = config.get("categoryKeywords", {})
    # Order matters: a tutorial is strategy even if it mentions layouts, and a
    # "Top 10 layouts" compilation is a highlight rather than a single play.
    for category in ("strategy", "highlight", "play"):
        if contains_any(text, rules.get(category, [])):
            return category
    return default if default in VALID_CATEGORIES else "highlight"


def reject_reason(item: dict, config: dict, now: dt.datetime, known_ids: set[str]) -> str | None:
    video_id = item["id"]
    snippet, details, status = item.get("snippet", {}), item.get("contentDetails", {}), item.get("status", {})
    title = snippet.get("title", "")
    text = f"{title} {snippet.get('description', '')}"

    if video_id in known_ids:
        return "already in feed"
    if video_id in set(config.get("blockedVideoIds", [])):
        return "blocked"
    if not status.get("embeddable", False):
        return "embedding disabled"
    if status.get("privacyStatus") != "public":
        return "not public"
    if snippet.get("liveBroadcastContent", "none") != "none":
        return "live or upcoming"
    published = snippet.get("publishedAt")
    if published:
        age = now - dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
        if age.days > config.get("maxAgeDays", 14):
            return "too old"
    duration = parse_iso_duration(details.get("duration", ""))
    if duration is None:
        return "unknown duration"
    if duration < config.get("minDurationSeconds", 20):
        return "too short"
    if duration > config.get("maxDurationSeconds", 1200):
        return "too long"
    if contains_any(text, config.get("excludeKeywords", [])):
        return "excluded keyword"
    required = config.get("requireAnyKeyword", [])
    if required and not contains_any(text, required):
        return "not about ultimate"
    return None


def to_feed_entry(item: dict, channel_title: str, category: str, today: str, taken_ids: set[str]) -> dict:
    snippet = item["snippet"]
    title = clean_title(snippet.get("title", "")) or "Untitled video"
    base = slugify(title)
    slug, n = base, 2
    while slug in taken_ids:
        slug, n = f"{base}-{n}", n + 1
    entry = {
        "id": slug,
        "title": title,
        "embedURL": f"https://www.youtube.com/watch?v={item['id']}",
        "platform": "youtube",
        "category": category,
        "durationSeconds": parse_iso_duration(item["contentDetails"]["duration"]),
        "dateAdded": today,
        "description": short_description(snippet.get("description", "")),
        "creator": channel_title,
    }
    return {k: v for k, v in entry.items() if v is not None or k == "durationSeconds"}


def trim_feed(videos: list[dict], max_size: int, protect_since: str) -> tuple[list[dict], list[dict]]:
    """Keep newest; never drop entries with keep=true or added on/after protect_since."""
    ordered = sorted(videos, key=lambda v: v.get("dateAdded", ""), reverse=True)
    removed: list[dict] = []
    while len(ordered) > max_size:
        for index in range(len(ordered) - 1, -1, -1):
            candidate = ordered[index]
            if not candidate.get("keep") and candidate.get("dateAdded", "") < protect_since:
                removed.append(ordered.pop(index))
                break
        else:
            break  # everything left is protected
    return ordered, removed


def validate(feed: dict) -> list[str]:
    errors, seen = [], set()
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", feed.get("version", "")):
        errors.append("version must be yyyy.MM.dd")
    for video in feed.get("videos", []):
        vid = video.get("id")
        if not vid or vid in seen:
            errors.append(f"missing or duplicate id: {vid}")
        seen.add(vid)
        for field in ("title", "embedURL", "platform", "category", "dateAdded"):
            if not video.get(field):
                errors.append(f"{vid}: missing {field}")
        if video.get("platform") not in VALID_PLATFORMS:
            errors.append(f"{vid}: bad platform")
        if video.get("category") not in VALID_CATEGORIES:
            errors.append(f"{vid}: bad category")
        if video.get("platform") == "youtube" and not youtube_id_from_url(video.get("embedURL", "")):
            errors.append(f"{vid}: embedURL is not a YouTube video link")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", video.get("dateAdded", "")):
            errors.append(f"{vid}: dateAdded must be yyyy-MM-dd")
    return errors


# ---------------------------------------------------------------- main flow

def run(client, config: dict, feed: dict, now: dt.datetime) -> tuple[dict, list[dict], list[dict]]:
    local_now = now.astimezone(ZoneInfo(config.get("timezone", "UTC")))
    today = local_now.strftime("%Y-%m-%d")
    protect_since = (local_now - dt.timedelta(days=config.get("protectRecentDays", 7))).strftime("%Y-%m-%d")

    videos = [v for v in feed.get("videos", [])
              if youtube_id_from_url(v.get("embedURL", "")) not in set(config.get("blockedVideoIds", []))]
    known_youtube_ids = {youtube_id_from_url(v["embedURL"]) for v in videos if v.get("platform") == "youtube"}
    taken_slugs = {v["id"] for v in videos}

    candidates: list[tuple[str, dict, str, str]] = []  # (publishedAt, item, channel title, default category)
    for channel in config.get("channels", []):
        label = channel.get("name") or channel.get("handle") or channel.get("username")
        try:
            resolved = client.uploads_playlist(channel)
            if not resolved:
                print(f"! channel not found: {label}", file=sys.stderr)
                continue
            playlist_id, channel_title = resolved
            items = client.video_details(client.recent_upload_ids(playlist_id))
        except Exception as error:  # one bad channel shouldn't stop the run
            print(f"! {label}: {error}", file=sys.stderr)
            continue

        rules = rules_for_channel(config, channel)
        accepted = 0
        for item in sorted(items, key=lambda i: i["snippet"].get("publishedAt", ""), reverse=True):
            reason = reject_reason(item, rules, now, known_youtube_ids)
            if reason:
                print(f"  skip [{label}] {item['snippet'].get('title', '')[:60]!r}: {reason}")
                continue
            if accepted >= config.get("maxNewPerChannelPerRun", 1):
                break
            candidates.append((item["snippet"].get("publishedAt", ""), item, channel_title,
                               channel.get("defaultCategory", "highlight")))
            accepted += 1

    added: list[dict] = []
    for _, item, channel_title, default in sorted(candidates, key=lambda c: c[0], reverse=True):
        if len(added) >= config.get("maxNewPerRun", 3):
            break
        snippet = item["snippet"]
        category = categorize(snippet.get("title", ""), snippet.get("description", ""), config, default)
        entry = to_feed_entry(item, channel_title, category, today, taken_slugs)
        taken_slugs.add(entry["id"])
        known_youtube_ids.add(item["id"])
        added.append(entry)

    videos, removed = trim_feed(videos + added, config.get("maxFeedSize", 50), protect_since)
    changed = bool(added or removed or len(videos) != len(feed.get("videos", [])))
    new_feed = {
        "version": local_now.strftime("%Y.%m.%d") if changed else feed.get("version", local_now.strftime("%Y.%m.%d")),
        "videos": videos,
    }
    return new_feed, added, removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the result without writing videos.json")
    parser.add_argument("--feed", default=str(ROOT / "videos.json"))
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    args = parser.parse_args()

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY is not set", file=sys.stderr)
        return 2

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    feed_path = Path(args.feed)
    feed = json.loads(feed_path.read_text(encoding="utf-8"))

    new_feed, added, removed = run(YouTubeClient(api_key), config, feed, dt.datetime.now(dt.timezone.utc))

    errors = validate(new_feed)
    if errors:
        print("Validation failed; videos.json not written:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1

    for video in added:
        print(f"+ [{video['category']}] {video['title']}  {video['embedURL']}")
    for video in removed:
        print(f"- {video['title']}")
    if not added and not removed:
        print("No changes.")
        return 0

    output = json.dumps(new_feed, indent=2, ensure_ascii=False) + "\n"
    if args.dry_run:
        print(output)
    else:
        feed_path.write_text(output, encoding="utf-8")
        print(f"Wrote {feed_path.name}: {len(new_feed['videos'])} videos, version {new_feed['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
