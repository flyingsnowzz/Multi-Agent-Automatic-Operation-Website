import json
import os
import tempfile
import unittest

from agents.competitor_agent.tools.rss_monitor import RSSMonitor


class TestRSSMonitor(unittest.TestCase):
    def test_parse_date_multiple_formats(self):
        m = RSSMonitor(state_path="")
        self.assertIsNotNone(m._parse_date("Wed, 02 Oct 2002 13:00:00 GMT"))
        self.assertIsNotNone(m._parse_date("2026-06-09T10:20:30Z"))
        self.assertIsNotNone(m._parse_date("2026-06-09 10:20:30"))
        self.assertIsNotNone(m._parse_date("2026-06-09"))

    def test_state_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "rss_state.json")
            m = RSSMonitor(state_path=state_path)
            m._update_feed_state("https://a.com/feed", "https://a.com/p1", "2026-06-09T10:00:00+00:00")

            m2 = RSSMonitor(state_path=state_path)
            s = m2._get_feed_state("https://a.com/feed")
            self.assertEqual(s.get("last_seen_link"), "https://a.com/p1")

            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("feeds", data)


if __name__ == "__main__":
    unittest.main()

