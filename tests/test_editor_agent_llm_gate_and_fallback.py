import asyncio
import unittest

from agents.editor_agent import EditorAgent


class TestEditorAgentFallback(unittest.TestCase):
    def test_llm_failure_falls_back_gracefully(self):
        async def run():
            agent = EditorAgent()
            agent.config["llm"] = dict(agent.config.get("llm") or {})
            agent.config["llm"]["enabled"] = True

            async def fake_call(_article):
                return {"success": False, "error": "llm_request_failed"}

            agent._call_llm = fake_call                    # type: ignore[assignment]

            out = await agent.execute(
                article={"title": "测试文章", "content_md": "## 正文\n\n关于中共发展历史的分析。"},
                dry_run=False,
            )
            self.assertTrue(out.get("success"))
            self.assertTrue(out.get("llm_review", {}).get("used"))
            self.assertEqual(out.get("llm_skipped_reason"), "")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
