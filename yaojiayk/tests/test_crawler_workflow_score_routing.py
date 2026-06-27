import asyncio
import unittest
from unittest.mock import patch


def _config():
    return {
        "crawler_db": {
            "review_pending_status": "review_pending",
            "processed_status": "processed",
            "error_status": "error",
        },
        "evaluation_criteria": {
            "input_required_fields": ["title", "content", "source_url"],
        },
    }


class TestCrawlerWorkflowScoreRouting(unittest.TestCase):
    def test_valid_items_are_handed_to_review(self):
        async def run():
            from workflows.crawler_workflow import run_crawler_workflow
            items = [
                {"id": 1, "title": "item_a", "content": "content_a", "source_url": "https://example.com/1"},
                {"id": 2, "title": "item_b", "content": "content_b", "source_url": "https://example.com/2"},
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["EMBA"],
                dry_run=True,
                config=_config(),
            )
            return out

        out = asyncio.run(run())
        processed_items = out["items"]

        self.assertEqual(processed_items[0]["decision"], "handoff_to_review")
        self.assertEqual(processed_items[0]["status_to_update"], "review_pending")
        self.assertEqual(processed_items[0]["next_agent"], "ReviewAgent")
        self.assertEqual(processed_items[0]["next_payload"]["handoff_stage"], "review")

        self.assertEqual(processed_items[1]["decision"], "handoff_to_review")
        self.assertEqual(processed_items[1]["status_to_update"], "review_pending")
        self.assertEqual(processed_items[1]["next_agent"], "ReviewAgent")

    @patch("workflows.crawler_workflow.update_crawler_status")
    def test_processed_status_fallback_is_used_for_db_write(self, mock_update_status):
        async def mock_update_status_side_effect(config, record_id, new_status, error_message=None):
            return {"success": True}

        mock_update_status.side_effect = mock_update_status_side_effect

        async def run():
            from workflows.crawler_workflow import run_crawler_workflow
            items = [
                {"id": 101, "title": "title_ok", "content": "content_test", "source_url": "https://example.com/101"},
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["EMBA"],
                dry_run=False,  # This triggers status update
                config={
                    "crawler_db": {"processed_status": "processed", "error_status": "error"},
                    "evaluation_criteria": {"input_required_fields": ["title", "content", "source_url"]},
                },
            )
            return out

        out = asyncio.run(run())

        self.assertEqual(mock_update_status.call_count, 1)
        self.assertEqual(out["items"][0]["decision"], "handoff_to_review")
        self.assertEqual(out["items"][0]["status_to_update"], "processed")
        self.assertEqual(out["items"][0]["next_agent"], "ReviewAgent")

        args_1, kwargs_1 = mock_update_status.call_args_list[0]
        self.assertEqual(kwargs_1.get("record_id") or args_1[1], 101)
        self.assertEqual(kwargs_1.get("new_status") or args_1[2], "processed")


if __name__ == "__main__":
    unittest.main()
