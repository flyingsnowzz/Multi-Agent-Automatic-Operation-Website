import asyncio
import unittest

from workflows.crawler_workflow import run_crawler_workflow


def _config():
    return {
        "crawler_db": {"pass_to_topic_status": "pass_to_topic", "discard_status": "discarded"},
        "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
        "evaluation_criteria": {
            "material_score_threshold": 50,
            "min_word_count": 80,
            "max_word_count": 5000,
            "input_required_fields": ["title", "content", "source_url"],
            "require_source_ok": True,
            "require_topic_hint": True,
        },
    }


class TestCrawlerWorkflowMaterialRouting(unittest.TestCase):
    def test_low_material_score_discard(self):
        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "新闻", "content": "简单摘要。", "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI"],
                dry_run=True,
                config=_config(),
            )
            self.assertEqual(out["items"][0]["decision"], "discard")
            self.assertLess(float(out["items"][0]["evaluation"]["material_score"]), 50)

        asyncio.run(run())

    def test_topic_hint_empty_discard(self):
        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "资讯", "content": "资讯 " * 20, "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI"],
                dry_run=True,
                config=_config(),
            )
            self.assertEqual(out["items"][0]["decision"], "discard")
            self.assertEqual(out["items"][0]["evaluation"]["topic_hint"], "")

        asyncio.run(run())

    def test_no_writer_or_cms_routing(self):
        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "AI Agent 评测方法", "content": "AI Agent 评测方法 数据 案例 步骤 总结 " * 80, "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI Agent"],
                dry_run=True,
                config=_config(),
            )
            self.assertIn(out["items"][0]["decision"], {"discard", "pass_to_topic"})
            self.assertNotIn(out["items"][0]["next_agent"], {"WriterAgent", "CMSAgent"})

        asyncio.run(run())

    def test_legacy_config_terms_remain_compatible(self):
        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "AI Agent 评测方法", "content": "AI Agent 评测方法 数据 案例 步骤 总结 " * 80, "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI Agent"],
                dry_run=True,
                config={
                    "crawler_db": {"pass_to_topic_status": "pass_to_topic", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {
                        "min_quality_score": 50,
                        "required_fields": ["title", "content", "source_url"],
                        "min_word_count": 80,
                        "max_word_count": 5000,
                    },
                    "metrics": {"metrics_to_track": ["average_quality_score"]},
                    "decision_rules": {
                        "discard_conditions": [
                            "quality_score < min_quality_score",
                            "has_copyright_risk == true",
                        ]
                    },
                },
            )
            self.assertEqual(out["items"][0]["decision"], "pass_to_topic")
            self.assertEqual(out["items"][0]["next_agent"], "TopicAgent")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
