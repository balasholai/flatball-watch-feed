# UFP Watch feed

The hosted `videos.json` for Ultimate Frisbee Pro's Watch tab. A GitHub Action runs
daily, pulls new uploads from trusted channels with the YouTube Data API, filters
them with `config.json`, and commits the updated feed. The app checks this file at
most once a day, so new videos appear without an App Store update.

Embeds only: the feed stores links and metadata. Nothing downloads or re-hosts video.

## One-time setup (about 15 minutes)

1. **Create a public GitHub repo** named `ufp-watch-feed` and push this folder's
   contents to its root (`videos.json`, `config.json`, `scripts/`, `.github/`).
   It must be public so the app can read the file without credentials.
   (If this folder also lives inside the app repo, it's fine to keep a copy there.)

2. **Create a YouTube Data API key**
   - Go to console.cloud.google.com → create a project (e.g. "UFP feed").
   - APIs & Services → Library → enable **YouTube Data API v3**.
   - APIs & Services → Credentials → Create credentials → **API key**.
   - Restrict the key: API restrictions → YouTube Data API v3 only.

3. **Add the key to GitHub**: repo → Settings → Secrets and variables → Actions →
   New repository secret → name `YOUTUBE_API_KEY`.

4. **Run it once**: repo → Actions → "Update Watch feed" → Run workflow. Check the
   log: it lists every skipped video with a reason and every added one.

5. **Point the app at the feed**: in `Core/Content/VideoFeedStore.swift` set
   ```swift
   static let remoteFeedURL: URL? = URL(string: "https://raw.githubusercontent.com/<your-username>/ufp-watch-feed/main/videos.json")
   ```

## Tuning (edit `config.json`, no code changes)

| Setting | What it does |
|---|---|
| `channels` | Trusted channels, by `handle`, `username` or `channelId`, with a `defaultCategory`. Each can override `maxAgeDays`, `minDurationSeconds`, `maxDurationSeconds`, and set `requireKeywords: false` for ultimate-only channels (used for India Ultimate and Off-Season Ultimate, whose titles may be in Hindi or hashtags only) |
| `maxNewPerRun` / `maxNewPerChannelPerRun` | How many videos can be added per day, overall and per channel |
| `maxAgeDays` | Only consider uploads newer than this |
| `min/maxDurationSeconds` | Skips tiny clips and full-game streams |
| `requireAnyKeyword` / `excludeKeywords` | Must mention ultimate; skips streams, podcasts, promos |
| `categoryKeywords` | Title/description words that pick Strategy, Highlight or Play (checked in that order) |
| `blockedVideoIds` | YouTube ids to remove now and never add again |
| `maxFeedSize` / `protectRecentDays` | Keeps ~50 videos, dropping the oldest but never anything from the last week |

To keep a hand-picked video permanently, add `"keep": true` to its entry in `videos.json`.
To fix a wrong category or title, just edit `videos.json`; the script never rewrites existing entries.

## Keeping the app's offline copy in sync

This `videos.json` is the source of truth. Before each app release, run `./scripts/sync-content.sh` from the app project root to copy it into the app bundle (`--check` just reports whether they differ).

## Local run

```bash
python scripts/test_update_feed.py                           # offline tests
YOUTUBE_API_KEY=... python scripts/update_feed.py --dry-run  # preview without writing
```

## Good to know

- **Quota:** about 2 units per channel per run; the free limit is 10,000/day.
- **Embedding:** videos whose owners disabled embedding are skipped automatically.
- **Rights:** channels are trusted by you; check that featuring their videos is okay.
- **GitHub pauses scheduled workflows** in repos with no activity for 60 days. Daily
  commits normally prevent that; if the feed goes quiet, re-enable it under Actions.
