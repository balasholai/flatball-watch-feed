# UFP Watch feed

The hosted `videos.json` for Flatball.io's Watch tab. A GitHub Action runs
daily, pulls new uploads from trusted channels with the YouTube Data API, filters
them with `config.json`, and commits the updated feed. The app checks this file at
most once a day, so new videos appear without an App Store update.

Embeds only: the feed stores links and metadata. Nothing downloads or re-hosts video.

## One-time setup (about 15 minutes)

1. **Create a public GitHub repo** named `flatball-watch-feed` and push this folder's
   contents to its root (`videos.json`, `config.json`, `scripts/`, `.github/`).
   It must be public so the app can read the file without credentials — which
   is why this is its own repo and not a folder inside the app, which is
   private. The Action also only runs from a repository root.

2. **Create a YouTube Data API key**
   - Go to console.cloud.google.com → create a project (e.g. "UFP feed").
   - APIs & Services → Library → enable **YouTube Data API v3**.
   - APIs & Services → Credentials → Create credentials → **API key**.
   - Restrict the key: API restrictions → YouTube Data API v3 only.

3. **Add the key to GitHub**: repo → Settings → Secrets and variables → Actions →
   New repository secret → name `YOUTUBE_API_KEY`.

4. **Run it once**: repo → Actions → "Update Watch feed" → Run workflow. Check the
   log: it lists every skipped video with a reason and every added one.

5. **Point the app at the feed**: in the Flutter app's
   `lib/core/content/video_feed_store.dart` set
   ```dart
   static const String? remoteFeedUrl =
       'https://raw.githubusercontent.com/<your-username>/flatball-watch-feed/main/videos.json';
   ```
   It is `null` today, so the app uses only its bundled copy.

## Tuning (edit `config.json`, no code changes)

| Setting | What it does |
|---|---|
| `channels` | Trusted channels, by `handle`, `username` or `channelId`, with a `defaultCategory`. Each can override `maxAgeDays`, `minDurationSeconds`, `maxDurationSeconds`, `maxDurationSecondsByCategory` and `excludeKeywords`, and set `requireKeywords: false` for ultimate-only channels (used for India Ultimate and Off-Season Ultimate, whose titles may be in Hindi or hashtags only). A channel that does not resolve is logged as `! channel not found` and skipped — a wrong handle costs nothing but a log line |
| `maxNewPerRun` / `maxNewPerChannelPerRun` | How many videos can be added per day, overall and per channel |
| `maxNewPerCategoryPerRun` | **Reserved slots per category per day.** Highlight channels upload most days and coaching channels upload most weeks, so without this the newest-first fill gives every slot to highlights. Strategy holds 3, play 1, highlight 2 — an unused strategy slot is simply not filled rather than handed to a highlight |
| `minPerCategory` | **Floors inside `maxFeedSize`.** The trim drops oldest-first, which over a couple of months grinds the slow-publishing category out of the feed however well the per-run quotas worked. A category at its floor is not trimmed |
| `maxAgeDays` | Only consider uploads newer than this |
| `min/maxDurationSeconds` | Skips tiny clips and full-game streams |
| `maxDurationSecondsByCategory` | A longer ceiling for one category. Strategy gets 45 minutes: film study and breakdowns routinely pass the 20 minutes that suits a highlight reel, and a single ceiling silently rejected exactly the category the feed was short of. Before a video is categorised the loosest ceiling applies, so nothing is thrown out before anyone asks what it is |
| `requireAnyKeyword` / `excludeKeywords` | Must mention ultimate; skips streams, podcasts, promos |
| `categoryKeywords` | Title/description words that pick Strategy, Highlight or Play (checked in that order) |
| `blockedVideoIds` | YouTube ids to remove now and never add again |
| `maxFeedSize` / `protectRecentDays` | Keeps ~50 videos, dropping the oldest but never anything from the last week |

To keep a hand-picked video permanently, add `"keep": true` to its entry in `videos.json`.
To fix a wrong category or title, just edit `videos.json`; the script never rewrites existing entries.

## Keeping the app's offline copy in sync

This `videos.json` is the source of truth. Before each app release, run
`tool/sync-content.sh` from the Flutter app's root to copy it into
`assets/content/videos.json` (`--check` only reports whether they differ). The
bundled copy is what the app shows offline and on first launch.

## Local run

```bash
python scripts/test_update_feed.py                           # offline tests
YOUTUBE_API_KEY=... python scripts/update_feed.py --dry-run  # preview without writing
```

## Getting the category mix you want

A video's category is decided by `categoryKeywords` against its title and
description, falling back to the channel's `defaultCategory`. Two things follow:

- **A category needs a source.** Quotas reserve slots; they cannot invent
  videos. Every channel in `config.json` today is a broadcaster with
  `defaultCategory: highlight`, so strategy only appears when a broadcaster
  happens to publish a tutorial. For a reliable strategy feed, add a coaching
  channel with `defaultCategory: "strategy"`.
- **`excludeKeywords` applies to strategy too.** `interview` is on that list,
  which also blocks a coaching interview. If you add a channel whose good
  content is framed that way, override `excludeKeywords` on that channel rather
  than loosening it for everyone.

Check a change before trusting it:

```bash
YOUTUBE_API_KEY=... python scripts/update_feed.py --dry-run
```

The log prints every candidate with its verdict — `skip` with a reason, `hold`
when a category is full for the run, `+` when added.

## Good to know

- **Quota:** about 2 units per channel per run; the free limit is 10,000/day.
- **Embedding:** videos whose owners disabled embedding are skipped automatically.
- **Rights:** channels are trusted by you; check that featuring their videos is okay.
- **GitHub pauses scheduled workflows** in repos with no activity for 60 days. Daily
  commits normally prevent that; if the feed goes quiet, re-enable it under Actions.
