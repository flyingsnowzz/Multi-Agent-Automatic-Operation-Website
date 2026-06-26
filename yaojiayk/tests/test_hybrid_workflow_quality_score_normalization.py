import unittest

from workflows.hybrid_workflow import (
    HybridWorkflow,
    _extract_quality_score,
    _normalize_quality_threshold,
)


class TestHybridWorkflowQualityScoreNormalization(unittest.TestCase):
    def test_extract_quality_score_accepts_zero_to_one(self):
        self.assertEqual(_extract_quality_score({"quality_score": 0.75}), 75.0)
        self.assertEqual(_extract_quality_score({"quality_score": {"overall": 0.82}}), 82.0)

    def test_extract_quality_score_accepts_zero_to_hundred(self):
        self.assertEqual(_extract_quality_score({"quality_score": 75}), 75.0)
        self.assertEqual(_extract_quality_score({"overall_score": 88}), 88.0)

    def test_normalize_threshold_keeps_legacy_ratio(self):
        self.assertEqual(_normalize_quality_threshold(0.8), 80.0)
        self.assertEqual(_normalize_quality_threshold(80), 80.0)

    def test_route_after_edit_retries_for_legacy_threshold(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 0,
            "quality_threshold": 0.8,
            "edit_result": {"quality_score": 0.75},
        }
        route = wf._route_after_edit(state)
        self.assertEqual(route, "retry_write")
        self.assertEqual(state["retry_count"], 1)

    def test_route_after_edit_retries_for_hundred_scale_score(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 0,
            "quality_threshold": 0.8,
            "edit_result": {"quality_score": 75},
        }
        route = wf._route_after_edit(state)
        self.assertEqual(route, "retry_write")
        self.assertEqual(state["retry_count"], 1)

    def test_route_after_edit_continues_for_hundred_scale_threshold(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 0,
            "quality_threshold": 80,
            "edit_result": {"quality_score": {"overall": 85}},
        }
        route = wf._route_after_edit(state)
        self.assertEqual(route, "continue")
        self.assertEqual(state["retry_count"], 0)

    def test_route_after_edit_stops_retrying_after_limit(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 2,
            "quality_threshold": 80,
            "edit_result": {"quality_score": 60},
        }
        route = wf._route_after_edit(state)
        self.assertEqual(route, "error")
        self.assertEqual(state["retry_count"], 2)
        self.assertIsInstance(state.get("error"), dict)
        self.assertEqual(state["error"].get("type"), "QualityGateFailed")
        self.assertEqual(state["error"].get("quality_score"), 60)
        self.assertEqual(state["error"].get("threshold"), 80.0)

    def test_route_after_edit_continues_without_score(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 0,
            "quality_threshold": 80,
            "edit_result": {"article": {"title": "T"}},
        }
        self.assertEqual(wf._route_after_edit(state), "continue")

    def test_route_after_edit_rejected_retries_before_limit(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 1,
            "quality_threshold": 80,
            "edit_result": {"approval_status": "rejected", "quality_score": 95},
        }
        route = wf._route_after_edit(state)
        self.assertEqual(route, "retry_write")
        self.assertEqual(state["retry_count"], 2)

    def test_route_after_edit_rejected_stops_at_limit(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 2,
            "quality_threshold": 80,
            "edit_result": {"approval_status": "rejected", "quality_score": 95},
        }
        route = wf._route_after_edit(state)
        self.assertEqual(route, "error")
        self.assertEqual(state["retry_count"], 2)
        self.assertEqual((state.get("error") or {}).get("type"), "ApprovalRejected")

    def test_route_after_edit_stops_retrying_after_limit_for_ratio_scale(self):
        wf = HybridWorkflow()
        state = {
            "error": None,
            "retry_count": 2,
            "quality_threshold": 0.8,
            "edit_result": {"quality_score": 0.6},
        }
        route = wf._route_after_edit(state)
        self.assertEqual(route, "error")
        self.assertEqual((state.get("error") or {}).get("quality_score"), 60.0)
        self.assertEqual((state.get("error") or {}).get("threshold"), 80.0)


if __name__ == "__main__":
    unittest.main()
