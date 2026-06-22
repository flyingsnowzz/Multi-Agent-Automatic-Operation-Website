import asyncio
import os
import unittest

from agents.editor_agent import EditorAgent


class TestEditorAgentLLMGateAndFallback(unittest.TestCase):
    def test_llm_gate_env_and_fallback(self):
        old_env = os.environ.get("EDITOR_ENABLE_LLM")
        os.environ["EDITOR_ENABLE_LLM"] = "true"

        async def run():
            agent = EditorAgent()
            agent.config = dict(agent.config or {})
            agent.config["execution"] = dict((agent.config.get("execution") or {}))
            agent.config["execution"]["llm_review_enabled"] = True

            async def fake_call(_prompt: str):
                return {"success": False, "error": "llm_request_failed"}

            agent._call_llm = fake_call  # type: ignore[assignment]

            out = await agent.execute(
                article={"title": "标题" * 4, "content_md": "内容" * 400, "meta_description": "x" * 160},
                topic={"primary_keyword": "文章", "content_type": "blog"},
                dry_run=False,
            )
            self.assertTrue(out.get("success"))
            self.assertTrue((out.get("llm") or {}).get("used"))

        try:
            asyncio.run(run())
        finally:
            if old_env is None:
                os.environ.pop("EDITOR_ENABLE_LLM", None)
            else:
                os.environ["EDITOR_ENABLE_LLM"] = old_env


if __name__ == "__main__":
    unittest.main()

