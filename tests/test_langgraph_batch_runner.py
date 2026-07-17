import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.run_langgraph_batch import (
    _cms_schedule_dispatch_status,
    _ensure_reprint_credit,
    _ensure_reprint_title,
    _strip_public_source_markers,
    _looks_like_flat_forwarded_content,
    _sanitize_publish_markdown,
    _sanitize_publish_title,
    _content_html_from_markdown,
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

    def test_cms_dispatch_does_not_rewind_future_schedule_state(self):
        """Verify a future schedule cursor means today's quota is exhausted."""
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

            self.assertFalse(status["due"])
            self.assertEqual(status["remaining"], 0)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {"date": "2026-07-16", "slot_index": 1, "used": 4})

    def test_cms_dispatch_returns_window_sleep_inside_slot(self):
        """Verify slot dispatch waits only until the current window ends."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "cms_state.json"
            state_path.write_text(json.dumps({"date": "2026-07-16", "slot_index": 4, "used": 0}), encoding="utf-8")

            with patch.dict(
                "os.environ",
                {
                    "CMS_SCHEDULE_STATE_PATH": str(state_path),
                    "CMS_SCHEDULE_TIMES": "09:00,11:00,13:00,15:00,17:00",
                    "CMS_SCHEDULE_PER_SLOT": "10",
                    "CMS_SCHEDULE_SLOT_WINDOW_SECONDS": "900",
                },
            ):
                status = _cms_schedule_dispatch_status(now=datetime(2026, 7, 16, 17, 2, 10))

            self.assertTrue(status["due"])
            self.assertEqual(status["slot"], "17:00")
            self.assertLess(status["window_sleep_seconds"], status["sleep_seconds"])
            self.assertEqual(status["window_sleep_seconds"], 771)

    def test_pending_reprint_helpers_hide_public_attribution(self):
        """Verify pending release hides public title/body source markers."""
        self.assertEqual(_ensure_reprint_title("标题"), "标题")
        self.assertEqual(_ensure_reprint_title("转载｜标题"), "标题")
        self.assertEqual("正文", _ensure_reprint_credit("正文", source_title="标题", source_url="https://example.com/original"))
        self.assertEqual("正文", _strip_public_source_markers("正文\n\n> 转载来源：[标题](https://example.com/original)"))
        self.assertTrue(_looks_like_flat_forwarded_content("长句子" * 300))
        self.assertFalse(_looks_like_flat_forwarded_content("第一段。\n\n第二段。"))

    def test_publish_markdown_sanitizer_restores_escaped_newlines_and_dedupes(self):
        """Verify CMS-bound Markdown does not publish literal backslash-n bodies."""
        body = "第一段。\\n\\n- 要点一\\n\\nð第二段。"
        duplicated = f"{body}\\n\\n{body}"

        cleaned = _sanitize_publish_markdown(duplicated)
        html = _content_html_from_markdown(duplicated)

        self.assertEqual(cleaned, "第一段。\n\n- 要点一\n\n🌍第二段。")
        self.assertNotIn("\\n", html)
        self.assertIn("<p>第一段。</p>", html)
        self.assertIn("<li>要点一</li>", html)

    def test_publish_title_sanitizer_repairs_mojibake_emoji(self):
        """Verify CMS-bound titles do not keep mojibake emoji bytes."""
        self.assertEqual(_sanitize_publish_title("ð 标题"), "📊 标题")


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
