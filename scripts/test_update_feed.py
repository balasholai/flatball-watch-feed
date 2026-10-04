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
        # The title outranks the description.
        self.assertEqual(uf.categorize("Rookie of the Year Highlights", "Great mark and force work.",
                                       config, "strategy"), "highlight")
        self.assertEqual(uf.categorize("Chander Boyd-Fliegel", "A drill for cutters.", config, "highlight"),
                         "strategy")

    def test_clean_title(self):
        self.assertEqual(uf.clean_title("Huge Layout D! 🔥 #ultimate #frisbee"), "Huge Layout D!")

    def test_existing_feed_is_valid(self):
        self.assertEqual(uf.validate(json.loads((ROOT / "videos.json").read_text())), [])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.json").read_text())
        # A frozen feed, not the live videos.json: the live one changes every
        # day, and counts asserted against it broke the daily run's own tests.
        self.feed = json.loads((Path(__file__).parent / "test_feed.json").read_text())
        self.config.pop("categoryEveryDays", None)  # tested on its own below

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

    def test_excluded_words_count_in_titles_not_descriptions(self):
        boilerplate = ("How to throw a flick. Listen to our podcast, "
                       "grab merch and tickets at the link below.")
        items = [
            video("BBBBBBBBBB1", "How to Throw a Frisbee", description=boilerplate),
            video("BBBBBBBBBB2", "Global Disc Golf Forum 2026 | WFDF talk"),
        ]
        client = FakeClient({"@watchUFAtv": ("UFA", items)})
        _, added, _ = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual([a["embedURL"][-11:] for a in added], ["BBBBBBBBBB1"])
        self.assertEqual(added[0]["category"], "strategy")

    def test_hashtags_count_as_mentioning_ultimate(self):
        items = [video("CCCCCCCCCC1", "Learn this trick throw #ultimatefrisbee", description="")]
        client = FakeClient({"@watchUFAtv": ("UFA", items)})
        _, added, _ = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual([a["embedURL"][-11:] for a in added], ["CCCCCCCCCC1"])

    def test_strategy_waits_a_week_between_batches(self):
        self.config["categoryEveryDays"] = {"strategy": 7}
        tutorial = [video("DDDDDDDDDD1", "How to break the mark")]

        def strategy_added_on(day):
            feed = json.loads(json.dumps(self.feed))
            for v in feed["videos"]:
                if v["category"] == "strategy":
                    v["dateAdded"] = "2026-01-01"
            feed["videos"][0]["category"] = "strategy"
            feed["videos"][0]["dateAdded"] = day
            client = FakeClient({"@watchUFAtv": ("UFA", tutorial)})
            return uf.run(client, self.config, feed, NOW)[1]

        # NOW is 17 September: a batch on the 12th means the next is the 19th.
        self.assertEqual(strategy_added_on("2026-09-12"), [])
        self.assertEqual(len(strategy_added_on("2026-09-10")), 1)

    def test_no_duplicates_and_no_change(self):
        existing = uf.youtube_id_from_url(self.feed["videos"][0]["embedURL"])
        client = FakeClient({"@watchUFAtv": ("UFA", [video(existing, "Top 10 plays")])})
        new_feed, added, removed = uf.run(client, self.config, self.feed, NOW)
        self.assertEqual(added, [])
        self.assertEqual(new_feed["version"], self.feed["version"])

    def test_limits_and_trim(self):
        self.config["maxFeedSize"] = 11
        self.config["maxNewPerChannelPerRun"] = 5
        self.config["minPerCategory"] = {}  # floors tested separately
        items = [video(f"BBBBBBBBBB{i}", f"Ultimate highlights week {i}",
                       published=f"2026-09-1{i}T00:00:00Z") for i in range(5)]
        client = FakeClient({"@watchUFAtv": ("UFA", items)})
        new_feed, added, removed = uf.run(client, self.config, self.feed, NOW)
        # Five eligible highlights, but highlight is capped at 2 a run. The old
        # behaviour took maxNewPerRun of whatever arrived first, which is how
        # highlights came to outnumber strategy better than two to one.
        self.assertEqual(len(added), 2)
        self.assertTrue(all(a["category"] == "highlight" for a in added))
        self.assertEqual(len(new_feed["videos"]), 11)         # trimmed to cap
        self.assertTrue(all(r["dateAdded"] < "2026-09-10" for r in removed))  # recent ones protected
        self.assertEqual(uf.validate(new_feed), [])

    def test_strategy_keeps_its_slots_when_highlights_flood_in(self):
        """The point of the quotas: a day of highlight uploads plus one coaching
        video must not spend every slot on highlights."""
        self.config["maxNewPerChannelPerRun"] = 9
        self.config["channels"] = [
            {"name": "UFA", "handle": "@watchUFAtv", "defaultCategory": "highlight"},
            {"name": "Coach", "handle": "@coach", "defaultCategory": "strategy"},
        ]
        floods = [video(f"DDDDDDDDDD{i}", f"Ultimate highlights week {i}",
                        published=f"2026-09-1{i}T00:00:00Z") for i in range(5)]
        coaching = [video("EEEEEEEEEE1", "How to break the mark, explained",
                          published="2026-09-16T00:00:00Z")]
        client = FakeClient({"@watchUFAtv": ("UFA", floods), "@coach": ("Coach", coaching)})
        _, added, _ = uf.run(client, self.config, self.feed, NOW)
        by_category = {a["category"] for a in added}
        self.assertIn("strategy", by_category)
        self.assertEqual(sum(a["category"] == "highlight" for a in added), 2)

    def test_strategy_may_run_long(self):
        """Film study routinely passes 20 minutes. Under the single old ceiling
        it was rejected before anything asked what category it was."""
        long_strategy = video("FFFFFFFFFF1", "Zone defence breakdown: film study", duration="PT35M")
        long_highlight = video("FFFFFFFFFF2", "Ultimate highlights week 9", duration="PT35M")
        self.assertIsNone(uf.reject_reason(long_strategy, self.config, NOW, set(), "strategy"))
        self.assertIn("too long", uf.reject_reason(long_highlight, self.config, NOW, set(), "highlight"))
        # Before a category is known the ceiling must be the loosest one, or a
        # long strategy video never survives to be categorised at all.
        self.assertIsNone(uf.reject_reason(long_strategy, self.config, NOW, set(), None))

    def test_trim_respects_category_floors(self):
        videos = ([{"id": f"s{i}", "category": "strategy", "dateAdded": "2026-01-01"} for i in range(3)]
                  + [{"id": f"h{i}", "category": "highlight", "dateAdded": "2026-02-01"} for i in range(7)])
        kept, removed = uf.trim_feed(videos, max_size=5, protect_since="2026-09-01",
                                     min_per_category={"strategy": 3})
        self.assertEqual(sum(v["category"] == "strategy" for v in kept), 3)
        self.assertEqual(len(kept), 5)
        # Strategy is the OLDEST here, so an oldest-first trim would have taken
        # all of it; the floor is what stops that.
        self.assertTrue(all(v["category"] == "highlight" for v in removed))

    def test_floors_never_exceed_the_feed(self):
        """A floor that cannot be honoured must not loop or overfill: the trim
        stops when everything left is protected, even above max_size."""
        videos = [{"id": f"s{i}", "category": "strategy", "dateAdded": "2026-01-01"} for i in range(4)]
        kept, removed = uf.trim_feed(videos, max_size=2, protect_since="2026-09-01",
                                     min_per_category={"strategy": 10})
        self.assertEqual(len(kept), 4)
        self.assertEqual(removed, [])

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
