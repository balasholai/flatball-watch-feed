"""Offline tests for update_feed.py (no API key or network). Run: python scripts/test_update_feed.py"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import update_feed as uf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 9, 17, 3, 0, tzinfo=dt.timezone.utc)


def video(vid, title, published="2026-09-15T12:00:00Z", duration="PT3M10S", embeddable=True,
          description="Great ultimate frisbee action from the weekend.", live="none"):
    return {
        "id": vid,
        "snippet": {"title": title, "description": description, "publishedAt": published,
                    "liveBroadcastContent": live},
        "contentDetails": {"duration": duration},
        "status": {"embeddable": embeddable, "privacyStatus": "public"},
    }


class FakeClient:
    def __init__(self, channels):
        self.channels = channels  # label -> (title, [items])

    def uploads_playlist(self, channel):
        label = channel.get("handle") or channel.get("username")
        label = label or channel.get("channelId")
        return (f"UU-{label}", self.channels[label][0]) if label in self.channels else None

    def recent_upload_ids(self, playlist_id, limit=15):
        return [playlist_id]

    def video_details(self, ids):
        label = ids[0][3:]
        return self.channels[label][1]


class HelperTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(uf.parse_iso_duration("PT1H2M3S"), 3723)
        self.assertEqual(uf.parse_iso_duration("PT45S"), 45)
        self.assertEqual(uf.parse_iso_duration("P0D"), 0)  # live streams; rejected as too short
        self.assertIsNone(uf.parse_iso_duration(""))

    def test_youtube_ids(self):
        self.assertEqual(uf.youtube_id_from_url("https://www.youtube.com/watch?v=Tg3F4Zp0K4I"), "Tg3F4Zp0K4I")
        self.assertEqual(uf.youtube_id_from_url("https://youtu.be/Tg3F4Zp0K4I"), "Tg3F4Zp0K4I")
        self.assertEqual(uf.youtube_id_from_url("https://www.youtube.com/shorts/1jS4SarMzmo"), "1jS4SarMzmo")
        self.assertIsNone(uf.youtube_id_from_url("https://www.youtube.com/@ultiworld"))

    def test_categorize(self):
        config = json.loads((ROOT / "config.json").read_text())
        self.assertEqual(uf.categorize("How to break the mark", "", config, "highlight"), "strategy")
        self.assertEqual(uf.categorize("Top 10 layouts of the week", "", config, "play"), "highlight")
        self.assertEqual(uf.categorize("Insane layout Callahan", "", config, "highlight"), "play")
        self.assertEqual(uf.categorize("Semifinal: Boston vs Seattle", "", config, "highlight"), "highlight")

    def test_clean_title(self):
        self.assertEqual(uf.clean_title("Huge Layout D! 🔥 #ultimate #frisbee"), "Huge Layout D!")

    def test_existing_feed_is_valid(self):
        self.assertEqual(uf.validate(json.loads((ROOT / "videos.json").read_text())), [])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.json").read_text())
        self.feed = json.loads((ROOT / "videos.json").read_text())

    def test_filters_and_adds(self):
        items = [
            video("AAAAAAAAAA1", "Layout Callahan to win it"),
            video("AAAAAAAAAA2", "Full game: Boston vs Seattle", duration="PT1H40M"),
            video("AAAAAAAAAA3", "Old highlights", published="2026-07-01T00:00:00Z"),
            video("AAAAAAAAAA4", "No embed highlights", embeddable=False),
            video("AAAAAAAAAA5", "Cooking pasta", description="A recipe."),
            video("AAAAAAAAAA6", "Going live now", live="live"),
        ]
        client = FakeClient({"@watchUFAtv": ("UFA", items)})
        new_feed, added, removed = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual([a["embedURL"][-11:] for a in added], ["AAAAAAAAAA1"])
        self.assertEqual(added[0]["category"], "play")
        self.assertEqual(added[0]["creator"], "UFA")
        self.assertEqual(added[0]["durationSeconds"], 190)
        self.assertEqual(added[0]["dateAdded"], "2026-09-17")
        self.assertEqual(new_feed["version"], "2026.09.17")
        self.assertEqual(uf.validate(new_feed), [])

    def test_no_duplicates_and_no_change(self):
        existing = uf.youtube_id_from_url(self.feed["videos"][0]["embedURL"])
        client = FakeClient({"@watchUFAtv": ("UFA", [video(existing, "Top 10 plays")])})
        new_feed, added, removed = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual(added, [])
        self.assertEqual(new_feed["version"], self.feed["version"])

    def test_limits_and_trim(self):
        self.config["maxFeedSize"] = 11
        self.config["maxNewPerChannelPerRun"] = 5
        items = [video(f"BBBBBBBBBB{i}", f"Ultimate highlights week {i}",
                       published=f"2026-09-1{i}T00:00:00Z") for i in range(5)]
        client = FakeClient({"@watchUFAtv": ("UFA", items)})
        new_feed, added, removed = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual(len(added), 3)                       # maxNewPerRun
        self.assertEqual(len(new_feed["videos"]), 11)         # trimmed to cap
        self.assertTrue(all(r["dateAdded"] < "2026-09-10" for r in removed))  # recent ones protected
        self.assertEqual(uf.validate(new_feed), [])

    def test_india_channel_overrides(self):
        # Hindi title without English keywords, a short clip, and a month-old upload
        items = [
            video("CCCCCCCCCC1", "ऑफ सीज़न लीग का शानदार पॉइंट", description="मैच हाइलाइट्स",
                  duration="PT15S", published="2026-08-20T00:00:00Z"),
        ]
        client = FakeClient({"@offseasonultimate": ("Off-Season Ultimate", items)})
        new_feed, added, removed = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["creator"], "Off-Season Ultimate")
        # The same clip from a general channel is rejected by the global rules
        client = FakeClient({"@watchUFAtv": ("UFA", items)})
        _, added, _ = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual(added, [])

    def test_missing_channel_is_skipped(self):
        new_feed, added, removed = uf.run(FakeClient({}), self.config, self.feed, NOW)
        self.assertEqual(added, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
