import asyncio
import unittest
from workflows.crawler_workflow import run_crawler_workflow


class TestCrawlerWorkflowDecisionFallback(unittest.TestCase):
    def test_published_articles_no_longer_trigger_business_discard(self):
        async def run():
            items = [
                {
                    "id": 1,
                    "title": "AI Agent 评测方法",
                    "content": "这是关于 AI Agent 评测方法的长文。" * 80,
                    "source_url": "https://example.com/a",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[{"title": "其它", "content": "其它", "source_url": "https://example.com/a"}],
                target_keywords=["AI Agent"],
                dry_run=False,
                config={
                    "crawler_db": {"processed_status": "processed", "error_status": "error"},
                    "evaluation_criteria": {"input_required_fields": ["title", "content", "source_url"]},
                },
            )
            self.assertTrue(out.get("items"))
            self.assertEqual(out["items"][0]["decision"], "handoff_to_review")
            self.assertEqual(out["items"][0]["status_to_update"], "processed")
            self.assertEqual(out["items"][0]["next_agent"], "ReviewAgent")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
