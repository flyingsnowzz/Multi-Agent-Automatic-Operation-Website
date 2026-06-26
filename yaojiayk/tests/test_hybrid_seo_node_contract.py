import unittest


class TestHybridSEONodeContract(unittest.TestCase):
    def test_hybrid_seo_node_produces_seo_result_without_llm(self):
        from workflows.hybrid_workflow import HybridWorkflow

        wf = HybridWorkflow(config_dir="agents")
        state = {
            "topic": {"title": "T", "primary_keyword": "k", "secondary_keywords": [], "content_type": "guide"},
            "brand_config": {},
            "quality_threshold": 80,
            "research_result": None,
            "write_result": None,
            "edit_result": {"article": {"title": "T", "content_md": "# C", "meta_description": "x"}},
            "seo_result": None,
            "image_result": None,
            "cms_result": None,
            "evolved_keywords": None,
            "performance_data": None,
            "current_stage": "edit",
            "retry_count": 0,
            "error": None,
            "trace_id": "t",
        }
        out = wf._seo_node(state)
        self.assertIn("seo_result", out)
        self.assertIn("meta_title", out["seo_result"])
        self.assertIn("schema_json", out["seo_result"])


if __name__ == "__main__":
    unittest.main()

