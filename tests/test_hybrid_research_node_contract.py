import json
import unittest


class TestHybridResearchNodeContract(unittest.TestCase):
    def test_research_node_writes_dict_result(self):
        from workflows.hybrid_workflow import HybridWorkflow, HybridStage

        wf = HybridWorkflow(config_dir="agents", image_mode="plan_only")
        state = {
            "topic": {
                "id": "t1",
                "title": "EMBA和MBA有什么区别：企业高管如何选择更合适",
                "primary_keyword": "EMBA和MBA的区别",
                "secondary_keywords": ["EMBA院校怎么选"],
                "content_type": "guide",
                "target_keywords": ["EMBA和MBA的区别", "EMBA院校怎么选"],
                "search_intent": "informational",
                "outline_points": ["区别", "适合人群", "选择建议"],
            },
            "brand_config": {},
            "quality_threshold": 80,
            "research_result": None,
            "write_result": None,
            "edit_result": None,
            "seo_result": None,
            "image_result": None,
            "cms_result": None,
            "evolved_keywords": None,
            "performance_data": None,
            "current_stage": "",
            "retry_count": 0,
            "error": None,
            "trace_id": "trace_test",
        }

        out = wf._research_node(state)
        self.assertEqual(out["current_stage"], HybridStage.RESEARCH)
        self.assertIsNone(out.get("error"))
        self.assertIsInstance(out.get("research_result"), dict)

        research = out["research_result"]
        json.dumps(research, ensure_ascii=False)
        self.assertIn("sources", research)
        self.assertIn("citations", research)
        self.assertIn("outline", research)
        self.assertIsInstance(research.get("sources"), list)
        self.assertIsInstance(research.get("citations"), list)
        self.assertIsInstance((research.get("outline") or {}).get("sections"), list)


if __name__ == "__main__":
    unittest.main()

