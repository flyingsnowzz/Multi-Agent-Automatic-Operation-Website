import unittest
import asyncio
from unittest.mock import patch, AsyncMock


class TestWorkflowEditResultFieldAlignment(unittest.TestCase):
    def test_langgraph_edit_node_sets_edit_result(self):
        from workflows.langgraph_workflow import MultiAgentWorkflow

        wf = MultiAgentWorkflow(config_dir="agents")
        state = {
            "topic": {"title": "T", "primary_keyword": "k", "content_type": "guide"},
            "write_result": {"article": {"title": "T", "content_md": "# C", "meta_description": "x"}},
        }

        async def _fake_execute(*, article, topic, dry_run=False):
            return {"article": article, "quality_score": {"overall": 90, "dimensions": {}}, "issues_found": []}

        with patch("agents.editor_agent.editor_agent.EditorAgent.execute", new=AsyncMock(side_effect=_fake_execute)):
            out = wf._edit_node(state)
        self.assertIn("edit_result", out)
        self.assertIn("article", out["edit_result"])
        self.assertEqual(out["edit_result"]["article"].get("content_md"), "# C")

    def test_hybrid_edit_node_sets_edit_result(self):
        from workflows.hybrid_workflow import HybridWorkflow

        wf = HybridWorkflow()
        state = {
            "topic": {"title": "T", "primary_keyword": "k", "content_type": "guide"},
            "write_result": {"article": {"title": "T", "content_md": "# C", "meta_description": "x"}},
            "brand_config": {},
            "quality_threshold": 0.8,
            "retry_count": 0,
            "current_stage": "write",
            "error": None,
        }

        async def _fake_execute(*, article, topic, dry_run=False):
            return {"article": article, "quality_score": {"overall": 90, "dimensions": {}}, "issues_found": []}

        with patch("agents.editor_agent.editor_agent.EditorAgent.execute", new=AsyncMock(side_effect=_fake_execute)):
            out = wf._edit_node(state)
        self.assertIn("edit_result", out)
        self.assertEqual((out.get("edit_result") or {}).get("article", {}).get("content_md"), "# C")


if __name__ == "__main__":
    unittest.main()
