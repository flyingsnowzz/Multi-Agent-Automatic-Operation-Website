import asyncio
import unittest

from workflows.crawler_workflow import run_crawler_workflow


def _config():
    return {
        "crawler_db": {"processed_status": "processed", "error_status": "error"},
        "evaluation_criteria": {
            "input_required_fields": ["title", "content", "source_url"],
            "source_summary_max_length": 64,
        },
    }


class TestCrawlerWorkflowMaterialRouting(unittest.TestCase):
    def test_no_writer_or_cms_routing(self):
        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "AI Agent 评测方法", "content": "AI Agent 评测方法 数据 案例 步骤 总结 " * 20, "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI"],
                dry_run=True,
                config=_config(),
            )
            self.assertEqual(out["items"][0]["decision"], "handoff_to_review")
            self.assertEqual(out["items"][0]["next_agent"], "ReviewAgent")
            self.assertNotIn(out["items"][0]["next_agent"], {"WriterAgent", "CMSAgent", "ScoringAgent"})

        asyncio.run(run())

    def test_review_payload_is_normalized(self):
        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "  资讯  ", "content": "正文内容 " * 20, "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI"],
                dry_run=True,
                config=_config(),
            )
            payload = out["items"][0]["next_payload"]
            self.assertEqual(out["items"][0]["decision"], "handoff_to_review")
            self.assertEqual(payload["title"], "资讯")
            self.assertEqual(payload["source_title"], "资讯")
            self.assertEqual(payload["handoff_stage"], "review")
            self.assertLessEqual(len(payload["source_summary"]), 64)

        asyncio.run(run())

    def test_legacy_required_fields_remain_compatible(self):
        async def run():
            out = await run_crawler_workflow(
                items=[{"id": 1, "title": "AI Agent 评测方法", "content": "AI Agent 评测方法 数据 案例 步骤 总结 " * 80, "source_url": "https://example.com/a"}],
                published_articles=[],
                target_keywords=["AI Agent"],
                dry_run=True,
                config={
                    "crawler_db": {"processed_status": "processed", "error_status": "error"},
                    "evaluation_criteria": {
                        "required_fields": ["title", "content", "source_url"],
                        "source_summary_max_length": 48,
                    },
                },
            )
            self.assertEqual(out["items"][0]["decision"], "handoff_to_review")
            self.assertEqual(out["items"][0]["next_agent"], "ReviewAgent")
            self.assertLessEqual(len(out["items"][0]["next_payload"]["source_summary"]), 48)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
