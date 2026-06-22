import asyncio
import unittest
from workflows.crawler_workflow import run_crawler_workflow


class TestCrawlerWorkflowDecisionFallback(unittest.TestCase):
    def test_duplicate_article_discard(self):
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
                    "crawler_db": {"pass_to_topic_status": "pass_to_topic", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {"min_word_count": 80, "max_word_count": 5000},
                },
            )
            self.assertTrue(out.get("items"))
            self.assertEqual(out["items"][0]["decision"], "discard")
            self.assertIsNone(out["items"][0]["next_agent"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
