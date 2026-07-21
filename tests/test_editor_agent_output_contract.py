import asyncio
import unittest

from agents.editor_agent import EditorAgent


class TestEditorAgentOutputContract(unittest.TestCase):
    def test_execute_dry_run_returns_expected_keys(self):
        async def run():
            agent = EditorAgent()
            out = await agent.execute(
                article={"title": "标题" * 4, "content_md": "这是一个绝对好的文章。" + ("内容" * 400), "meta_description": "x" * 160},
                dry_run=True,
            )
            self.assertTrue(out.get("success"))
            self.assertIn("content_md", out)
            self.assertIn("content_html", out)
            self.assertIn("llm_review", out)
            self.assertFalse(out["llm_review"].get("used"))
            self.assertIn("safety_check", out)
            self.assertTrue(out["safety_check"].get("passed"))

        asyncio.run(run())

    def test_execute_skips_llm_when_sensitive_scan_is_clean(self):
        async def run():
            agent = EditorAgent()

            async def fail_if_called(article):
                raise AssertionError("Editor LLM should not run when sensitive scan is clean")

            agent._call_llm = fail_if_called
            out = await agent.execute(
                article={
                    "title": "普通校园新闻",
                    "content_md": "这是一篇普通校园活动报道，内容健康，不包含敏感词。" * 80,
                },
                dry_run=False,
            )
            self.assertTrue(out.get("success"))
            self.assertTrue(out["safety_check"].get("passed"))
            self.assertFalse(out["llm_review"].get("used"))
            self.assertEqual(out.get("llm_skipped_reason"), "sensitive_check_clean")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
