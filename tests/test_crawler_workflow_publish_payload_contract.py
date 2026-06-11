import asyncio
import unittest

from agents.cms_agent.cms_agent import CMSAgent
from workflows.crawler_workflow import run_crawler_workflow


class TestCrawlerWorkflowPublishPayloadContract(unittest.TestCase):
    def test_publish_payload_matches_cms_agent_input(self):
        async def run():
            items = [
                {
                    "id": 1,
                    "title": "k test",
                    "content": ("k " * 700) + "\n\n" * 5 + "http://a.com\n![x](y)",
                    "source_url": "https://example.com/a",
                    "category": "news",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["k"],
                dry_run=True,
                config={
                    "execution": {"auto_publish_threshold": 90, "rewrite_threshold": 40, "llm_decision_enabled": False},
                    "crawler_db": {"ready_to_publish_status": "ready_to_publish", "ready_to_rewrite_status": "ready_to_rewrite", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {
                        "min_quality_score": 40,
                        "min_relevance_score": 40,
                        "min_seo_potential_score": 40,
                        "min_word_count": 80,
                        "max_word_count": 5000,
                        "required_fields": ["title", "content", "source_url"],
                    },
                },
            )
            payload = out["items"][0]["next_payload"]
            self.assertIn("article", payload)
            self.assertIn("page_info", payload)
            self.assertIn("images", payload)
            self.assertEqual((payload.get("article") or {}).get("meta", {}).get("crawler_record_id"), 1)
            self.assertEqual((payload.get("article") or {}).get("meta", {}).get("source_url"), "https://example.com/a")
            self.assertEqual((payload.get("page_info") or {}).get("tags"), ["k"])
            self.assertEqual((payload.get("page_info") or {}).get("primary_keyword"), "k")

            cms = CMSAgent()
            extracted = cms._extract_article_payload(article=payload["article"], page_info=payload["page_info"], images=payload.get("images"))
            self.assertTrue(extracted.get("title"))
            self.assertTrue(extracted.get("content_md") or extracted.get("content"))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
