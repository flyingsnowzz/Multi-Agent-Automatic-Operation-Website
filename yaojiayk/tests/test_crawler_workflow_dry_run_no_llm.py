import asyncio
import unittest

from workflows.crawler_workflow import run_crawler_workflow


class TestCrawlerWorkflowDryRunNoLLM(unittest.TestCase):
    def test_valid_item_handoff_to_review(self):
        async def run():
            items = [
                {
                    "id": 1,
                    "title": "行业资讯",
                    "content": "这是一个结构完整的爬虫正文。" * 20,
                    "source_url": "https://example.com/a",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["行业"],
                dry_run=True,
                config={
                    "crawler_db": {"processed_status": "processed", "error_status": "error"},
                    "evaluation_criteria": {
                        "input_required_fields": ["title", "content", "source_url"],
                        "source_summary_max_length": 64,
                    },
                },
            )
            self.assertTrue(out.get("items"))
            self.assertEqual(out["items"][0]["decision"], "handoff_to_review")
            self.assertEqual(out["items"][0]["status_to_update"], "processed")
            self.assertEqual(out["items"][0]["next_agent"], "ReviewAgent")
            self.assertTrue(out["items"][0]["validation"]["valid"])

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
                    "source_url": "",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["valid"],
                dry_run=True,
                config={
                    "crawler_db": {"processed_status": "processed", "error_status": "error"},
                    "evaluation_criteria": {"input_required_fields": ["title", "content", "source_url"]},
                },
            )
            processed_items = out.get("items") or []
            self.assertEqual(len(processed_items), 3)
            for item in processed_items:
                self.assertEqual(item["decision"], "error")
                self.assertEqual(item["status_to_update"], "error")

        asyncio.run(run())

    def test_missing_required_fields_marks_error(self):
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
                    "crawler_db": {"processed_status": "processed", "error_status": "error"},
                    "evaluation_criteria": {
                        "input_required_fields": ["title", "content", "source_url"],
                    },
                },
            )
            self.assertEqual(out["items"][0]["decision"], "error")
            self.assertEqual(out["items"][0]["status_to_update"], "error")
            self.assertIn("missing_title", out["items"][0]["reason_codes"])
            self.assertEqual(
                out["items"][0]["validation"]["details"]["missing_required_fields"],
                ["title"],
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
