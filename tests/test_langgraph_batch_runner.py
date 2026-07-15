import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.run_langgraph_batch import (
    _cms_schedule_dispatch_status,
    _feed_idle_sleep_seconds,
    _parse_feed_idle_backoff_hours,
    _run_one_batch,
)


class LangGraphBatchRunnerTests(unittest.TestCase):
    # Feed idle backoff is pure calculation, so test it directly instead of
    # sleeping in an integration-style loop test.
    def test_feed_idle_sleep_uses_requested_backoff_sequence(self):
        """Verify feed idle attempts map to the configured backoff schedule."""
        schedule = _parse_feed_idle_backoff_hours("1,2,4,8,12,24")

        self.assertEqual(_feed_idle_sleep_seconds(0, schedule), 3600)
        self.assertEqual(_feed_idle_sleep_seconds(1, schedule), 7200)
        self.assertEqual(_feed_idle_sleep_seconds(2, schedule), 14400)
        self.assertEqual(_feed_idle_sleep_seconds(3, schedule), 28800)
        self.assertEqual(_feed_idle_sleep_seconds(4, schedule), 43200)
        self.assertEqual(_feed_idle_sleep_seconds(5, schedule), 86400)
        self.assertEqual(_feed_idle_sleep_seconds(99, schedule), 86400)

    def test_feed_idle_backoff_bad_config_falls_back_to_default(self):
        """Verify malformed backoff config falls back to the production default."""
        schedule = _parse_feed_idle_backoff_hours("bad")

        self.assertEqual(schedule, [1.0, 2.0, 4.0, 8.0, 12.0, 24.0])

    def test_cms_dispatch_rewinds_future_schedule_state(self):
        """Verify a future schedule cursor does not block today's due slot."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "cms_state.json"
            state_path.write_text(json.dumps({"date": "2026-07-16", "slot_index": 1, "used": 4}), encoding="utf-8")

            with patch.dict(
                "os.environ",
                {
                    "CMS_SCHEDULE_STATE_PATH": str(state_path),
                    "CMS_SCHEDULE_TIMES": "09:00,11:00,13:00,15:00,17:00",
                    "CMS_SCHEDULE_PER_SLOT": "10",
                    "CMS_SCHEDULE_SLOT_WINDOW_SECONDS": "900",
                },
            ):
                status = _cms_schedule_dispatch_status(now=datetime(2026, 7, 15, 11, 10, 0))

            self.assertTrue(status["due"])
            self.assertEqual(status["remaining"], 10)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {"date": "2026-07-15", "slot_index": 1, "used": 0})


class LangGraphBatchRunnerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_preblocked_articles_skip_scoring_and_graph(self):
        """Verify source-blocked rows go straight to audit without scoring/graph calls."""
        args = SimpleNamespace(
            feed=False,
            article_ids=[1938],
            latest=False,
            limit=1,
            include_used=False,
            scoring_only=False,
            ai_concurrency=4,
            no_late_stages=False,
            publish=False,
            persist_audit=True,
            mark_used=False,
            full_output=False,
            state_path=Path("output/langgraph_feeder_state.json"),
        )
        state = {
            "article_id": 1938,
            "title": "科大发明病毒测试器获美国投资者垂青",
            "stop_reason": "source_content_missing",
            "errors": ["source_content_missing"],
        }

        async def fake_save_audit(item):
            """Pretend audit persistence succeeded without touching MySQL."""
            item = dict(item)
            item["audit_persisted"] = True
            item["cms_status"] = "blocked"
            return item

        with patch("scripts.run_langgraph_batch._load_states", new=AsyncMock(return_value=[state])), \
            patch("scripts.run_langgraph_batch.summarize_crawler_topics", side_effect=AssertionError("scoring should be skipped")), \
            patch("scripts.run_langgraph_batch.run_article_graph", new=AsyncMock(side_effect=AssertionError("graph should be skipped"))), \
            patch("scripts.run_langgraph_batch.save_audit_node", new=AsyncMock(side_effect=fake_save_audit)):
            results = await _run_one_batch(args)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["article_id"], 1938)
        self.assertEqual(results[0]["stop_reason"], "source_content_missing")
        self.assertTrue(results[0]["audit_persisted"])


if __name__ == "__main__":
    unittest.main()
