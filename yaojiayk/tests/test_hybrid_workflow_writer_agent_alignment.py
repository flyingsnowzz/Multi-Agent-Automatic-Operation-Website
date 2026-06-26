import unittest
from unittest.mock import AsyncMock, patch


class TestHybridWorkflowWriterAgentAlignment(unittest.TestCase):
    def test_write_node_uses_writer_agent_contract(self):
        from workflows.hybrid_workflow import HybridWorkflow

        wf = HybridWorkflow()
        state = {
            "topic": {"title": "T", "primary_keyword": "k", "content_type": "guide"},
            "research_result": {"outline": {"h2": ["A"]}, "sources": [{"url": "https://example.com/s"}]},
            "brand_config": {"brand_guide": "config/brand_guidelines.yaml"},
            "quality_threshold": 0.8,
            "retry_count": 0,
            "current_stage": "research",
            "error": None,
            "trace_id": "trace123",
        }

        expected = {
            "article": {"title": "T", "content_md": "# T", "meta_description": "D"},
            "statistics": {"word_count": 2, "reading_time_minutes": 1},
            "quality_checks": {},
            "warnings": [],
        }

        async def _fake_execute(*, topic, outline=None, materials=None, brand_config=None, dry_run=False, mode=None):
            self.assertEqual(topic["title"], "T")
            self.assertEqual(outline, {"h2": ["A"]})
            self.assertIn("sources", materials)
            self.assertEqual(brand_config["brand_guide"], "config/brand_guidelines.yaml")
            self.assertTrue(dry_run)
            return expected

        with patch("agents.writer_agent.writer_agent.WriterAgent.execute", new=AsyncMock(side_effect=_fake_execute)):
            out = wf._write_node(state)

        self.assertIs(out["write_result"], expected)
        self.assertEqual(out["current_stage"], "write")
        self.assertIsNone(out["error"])


if __name__ == "__main__":
    unittest.main()
