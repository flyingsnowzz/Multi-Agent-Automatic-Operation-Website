import asyncio
import unittest
from unittest.mock import patch

from workflows.crawler_workflow import run_crawler_workflow


class TestCrawlerWorkflowDryRunNoLLM(unittest.TestCase):
    def test_high_risk_ad_content_discard(self):
        async def run():
            items = [
                {
                    "id": 1,
                    "title": "行业资讯",
                    "content": "招商加盟，扫码咨询，联系电话 123456，未经授权不得转载。" * 30,
                    "source_url": "https://example.com/a",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["行业"],
                dry_run=True,
                config={
                    "crawler_db": {"pass_to_scoring_status": "pass_to_scoring", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {"min_word_count": 80, "max_word_count": 5000},
                },
            )
            self.assertTrue(out.get("items"))
            self.assertEqual(out["items"][0]["decision"], "discard")
            self.assertTrue(out["items"][0]["evaluation"]["has_risk"])

        asyncio.run(run())

    def test_structural_self_check_hardening(self):
        async def run():
            items = [
                # 1. 非字典类型数据
                "not a dict",
                # 2. 纯空白字符标题
                {
                    "id": 2,
                    "title": "   ",
                    "content": "valid content here " * 50,
                    "source_url": "https://example.com/b",
                },
                # 3. 协议不支持的非法 URL
                {
                    "id": 3,
                    "title": "valid title",
                    "content": "valid content here " * 50,
                    "source_url": "ftp://example.com/c",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["valid"],
                dry_run=True,
                config={
                    "execution": {},
                    "crawler_db": {"pass_to_scoring_status": "pass_to_scoring", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine"},
                    "evaluation_criteria": {"min_word_count": 80, "max_word_count": 5000},
                },
            )
            processed_items = out.get("items") or []
            self.assertEqual(len(processed_items), 3)
            for item in processed_items:
                self.assertEqual(item["decision"], "discard")
                self.assertEqual(item["status_to_update"], "discarded")

        asyncio.run(run())

    @patch("workflows.crawler_workflow.check_duplicate")
    @patch("workflows.crawler_workflow.evaluate_content")
    def test_missing_required_fields_short_circuit_before_dedup_and_evaluate(self, mock_evaluate, mock_check_duplicate):
        async def run():
            out = await run_crawler_workflow(
                items=[
                    {
                        "id": 11,
                        "title": "   ",
                        "content": "valid content here " * 50,
                        "source_url": "https://example.com/a",
                    }
                ],
                published_articles=[],
                target_keywords=["valid"],
                dry_run=True,
                config={
                    "crawler_db": {"pass_to_scoring_status": "pass_to_scoring", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine"},
                    "evaluation_criteria": {
                        "input_required_fields": ["title", "content", "source_url"],
                        "min_word_count": 80,
                        "max_word_count": 5000,
                    },
                },
            )
            self.assertEqual(out["items"][0]["decision"], "discard")
            self.assertEqual(out["items"][0]["status_to_update"], "discarded")
            self.assertIn("missing_title", out["items"][0]["reason_codes"])
            self.assertEqual(
                out["items"][0]["evaluation"]["details"]["missing_required_fields"],
                ["title"],
            )

        asyncio.run(run())
        mock_check_duplicate.assert_not_called()
        mock_evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
