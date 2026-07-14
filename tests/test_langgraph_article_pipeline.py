import unittest

from workflows.langgraph_article_pipeline import (
    build_article_graph,
    route_after_quality,
    route_after_rewrite_quality,
    route_after_scoring,
    summarize_graph_result,
)


class LangGraphArticlePipelineTests(unittest.TestCase):
    # These tests focus on graph routing contracts. They avoid live model/API
    # calls so code review can quickly verify the business branch thresholds.
    def test_route_after_scoring_uses_threshold(self):
        """Verify AI score routing stops weak articles and keeps strong ones."""
        self.assertEqual(route_after_scoring({"ai_score": 75}), "quality")
        self.assertEqual(route_after_scoring({"ai_score": 74.9}), "stop_low_score")
        self.assertEqual(route_after_scoring({"stop_reason": "source_missing"}), "stop")

    def test_route_after_quality_splits_direct_and_rewrite(self):
        """Verify quality routing either rewrites, publishes late stages, or stops."""
        self.assertEqual(route_after_quality({"quality_score": 70, "run_late_stages": True}), "rewrite")
        self.assertEqual(route_after_quality({"quality_score": 71, "run_late_stages": True}), "seo")
        self.assertEqual(route_after_quality({"quality_score": 71, "run_late_stages": False}), "done")

    def test_route_after_rewrite_quality_requires_threshold(self):
        """Verify rewritten articles must pass the second quality threshold."""
        self.assertEqual(route_after_rewrite_quality({"rewrite_quality_after": 69.9}), "rewrite_blocked")
        self.assertEqual(route_after_rewrite_quality({"rewrite_quality_after": 70}), "edit")
        self.assertEqual(route_after_rewrite_quality({"stop_reason": "writer_failed"}), "stop")

    def test_summarize_graph_result_is_compact(self):
        """Verify graph summaries keep key fields without dumping full payloads."""
        summary = summarize_graph_result(
            {
                "article_id": 1,
                "title": "T",
                "ai_score": 80,
                "quality_score": 72,
                "seo_meta_title": "SEO",
                "errors": ["x"],
            }
        )
        self.assertEqual(summary["article_id"], 1)
        self.assertEqual(summary["seo_meta_title"], "SEO")
        self.assertEqual(summary["errors"], ["x"])
        self.assertIn("cms_status", summary)

    def test_graph_compiles_when_langgraph_is_installed(self):
        """Verify graph construction produces an invokable compiled graph."""
        # Compilation catches broken node names or conditional edge targets
        # without running any external providers.
        graph = build_article_graph()
        self.assertTrue(hasattr(graph, "ainvoke"))


if __name__ == "__main__":
    unittest.main()
