import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock


class _DummyLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self._content)


class TestWorkflowSEOResultPresence(unittest.TestCase):
    def test_langgraph_seo_node_produces_seo_result(self):
        from workflows.langgraph_workflow import MultiAgentWorkflow

        wf = MultiAgentWorkflow(config_dir="agents")
        wf.llm = _DummyLLM(
            content=(
                "{"
                '"optimized_article":{"title":"T","content":"C"},'
                '"meta_title":"MT",'
                '"meta_description":"MD",'
                '"og_tags":{},'
                '"twitter_tags":{},'
                '"schema_json":{},'
                '"internal_links":[],'
                '"seo_report":{},'
                '"improvement_suggestions":[]'
                "}"
            )
        )
        state = {
            "topic": {"title": "T", "primary_keyword": "k", "secondary_keywords": [], "content_type": "guide"},
            "write_result": {"article": {"title": "T", "content_md": "# C", "meta_description": "x"}},
            "edit_result": {"article": {"title": "T", "content_md": "# C", "meta_description": "x"}},
        }
        out = wf._seo_node(state)
        self.assertIn("seo_result", out)
        self.assertIn("meta_title", out["seo_result"])
        self.assertIn("schema_json", out["seo_result"])

    def test_crewai_seo_agent_has_tools(self):
        with open("workflows/crewai_workflow.py", "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("get_keyword_analyzer_tool", text)
        self.assertIn("get_meta_generator_tool", text)
        self.assertIn("get_schema_generator_tool", text)


if __name__ == "__main__":
    unittest.main()
