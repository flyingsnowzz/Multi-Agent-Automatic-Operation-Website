import asyncio
import unittest
from unittest.mock import patch

from workflows.crawler_workflow import run_crawler_workflow


def _config():
    return {
        "crawler_db": {"pass_to_scoring_status": "pass_to_scoring", "discard_status": "discarded"},
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
            self.assertIn(out["items"][0]["decision"], {"discard", "pass_to_scoring"})
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
                    "crawler_db": {"pass_to_scoring_status": "pass_to_scoring", "discard_status": "discarded"},
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
            self.assertEqual(out["items"][0]["decision"], "pass_to_scoring")
            self.assertEqual(out["items"][0]["next_agent"], "ScoringAgent")

        asyncio.run(run())

    @patch("workflows.crawler_workflow.check_duplicate")
    @patch("workflows.crawler_workflow.evaluate_content")
    def test_short_content_bonus_can_update_gate_result(self, mock_evaluate, mock_check_duplicate):
        async def mock_evaluate_side_effect(title, content, source_url, target_keywords, config):
            return {
                "success": True,
                "quality_score": 0.49,
                "relevance_score": 0.8,
                "seo_potential_score": 0.5,
                "material_score": 57.05,
                "topic_hint": "AI Agent 评测",
                "reason": "未通过门禁：low_base_usability",
                "base_relevance_score": 0.8,
                "base_usability_score": 0.49,
                "source_ok": True,
                "content_complete": True,
                "noise_ratio": 0.1,
                "gate_passed": False,
                "gate_result": "discard",
                "next_agent": None,
                "word_count": 120,
                "readability_score": 0.6,
                "has_copyright_risk": False,
                "details": {"gate_failures": ["low_base_usability"]},
            }

        async def mock_check_duplicate_side_effect(title, content, published_articles, threshold=None, algorithm=None, config=None):
            return {"success": True, "is_duplicate": False, "similarity_score": 0.0, "matched_article": None, "details": {}}

        mock_evaluate.side_effect = mock_evaluate_side_effect
        mock_check_duplicate.side_effect = mock_check_duplicate_side_effect

        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "快讯", "content": "短内容 " * 60, "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI Agent"],
                dry_run=True,
                config={
                    "crawler_db": {"pass_to_scoring_status": "pass_to_scoring", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine"},
                    "evaluation_criteria": {
                        "min_word_count": 80,
                        "max_word_count": 5000,
                        "short_content_threshold": 300,
                        "short_content_bonus": 1.1,
                        "min_base_usability_score": 0.5,
                    },
                },
            )
            self.assertEqual(out["items"][0]["decision"], "pass_to_scoring")
            self.assertEqual(out["items"][0]["evaluation"]["gate_result"], "pass_to_scoring")
            self.assertGreaterEqual(float(out["items"][0]["evaluation"]["base_usability_score"]), 0.5)
            self.assertEqual(out["items"][0]["next_agent"], "ScoringAgent")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
