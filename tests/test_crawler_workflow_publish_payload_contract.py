import asyncio
import unittest

from workflows.crawler_workflow import run_crawler_workflow


class TestCrawlerWorkflowPublishPayloadContract(unittest.TestCase):
    def test_pass_to_topic_payload_contract(self):
        async def run():
            items = [
                {
                    "id": 1,
                    "title": "k test",
                    "content": ("k " * 700) + "\n\n" * 5 + "http://a.com\n![x](y)",
                    "source_url": "https://example.com/a",
                    "category": "news",
                    "author": "author_test",
                    "spider_name": "spider_test",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["k"],
                dry_run=True,
                config={
                    "execution": {"llm_decision_enabled": False},
                    "crawler_db": {"pass_to_topic_status": "pass_to_topic", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {"min_word_count": 80, "max_word_count": 5000},
                },
            )
            payload = out["items"][0]["next_payload"]
            self.assertEqual(out["items"][0]["decision"], "pass_to_topic")
            self.assertEqual(out["items"][0]["next_agent"], "TopicAgent")
            self.assertEqual(payload["topic_hint"], "k test")
            self.assertEqual(payload["source_title"], "k test")
            self.assertTrue(payload["source_summary"].startswith("k "))
            self.assertEqual(payload["source_url"], "https://example.com/a")
            self.assertGreaterEqual(payload["material_score"], 50)
            self.assertNotIn("candidate_topic", payload)
            self.assertNotIn("primary_keyword", payload)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
