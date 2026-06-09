import asyncio
import os
import unittest

import workflows.crawler_workflow as cw


class TestCrawlerWorkflowDecisionFallback(unittest.TestCase):
    def test_llm_exception_fallbacks_to_rule(self):
        old = cw._decide_with_crewai
        old_env = os.environ.get("CRAWLER_ENABLE_LLM_DECISION")
        os.environ["CRAWLER_ENABLE_LLM_DECISION"] = "true"

        def boom(**_):
            raise RuntimeError("boom")

        cw._decide_with_crewai = boom

        async def run():
            items = [
                {
                    "id": 1,
                    "title": "k test",
                    "content": ("k " * 700) + "\n\n" * 5 + "http://a.com\n![x](y)",
                    "source_url": "https://example.com/a",
                }
            ]
            out = await cw.run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["k"],
                dry_run=False,
                config={
                    "execution": {"auto_publish_threshold": 0.8, "rewrite_threshold": 0.5, "llm_decision_enabled": True},
                    "crawler_db": {"ready_to_publish_status": "ready_to_publish", "ready_to_rewrite_status": "ready_to_rewrite", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {
                        "min_quality_score": 0.5,
                        "min_relevance_score": 0.4,
                        "min_seo_potential_score": 0.4,
                        "min_word_count": 80,
                        "max_word_count": 5000,
                        "required_fields": ["title", "content", "source_url"],
                    },
                },
            )
            self.assertTrue(out.get("items"))
            self.assertEqual(out["items"][0]["decision"], "publish")

        try:
            asyncio.run(run())
        finally:
            cw._decide_with_crewai = old
            if old_env is None:
                os.environ.pop("CRAWLER_ENABLE_LLM_DECISION", None)
            else:
                os.environ["CRAWLER_ENABLE_LLM_DECISION"] = old_env


if __name__ == "__main__":
    unittest.main()

