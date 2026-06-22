import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch


class TestResearchAgentContract(unittest.TestCase):
    def test_research_agent_importable(self):
        from agents.research_agent import ResearchAgent

        self.assertTrue(callable(ResearchAgent))

    def test_execute_mock_contract_fields(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        topic = {
            "title": "2026年EMBA报考条件详解：适合人群、申请流程与准备建议",
            "primary_keyword": "EMBA报考条件",
            "secondary_keywords": ["EMBA申请流程", "EMBA院校怎么选"],
            "content_type": "guide",
            "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
            "search_intent": "informational",
            "outline_points": ["报考条件", "申请流程", "院校选择"],
        }
        out = asyncio.run(agent.execute(topic=topic, mode="mock"))

        self.assertIsInstance(out, dict)
        self.assertIsInstance(out.get("background"), dict)
        for k in ("statistics", "cases", "quotes", "sources", "citations", "warnings"):
            self.assertIsInstance(out.get(k), list)
        self.assertIsInstance(out.get("outline"), dict)
        self.assertIsInstance((out.get("outline") or {}).get("sections"), list)
        self.assertGreaterEqual(len(out["outline"]["sections"]), 3)
        for section in out["outline"]["sections"]:
            self.assertIsInstance(section, dict)
            self.assertIsInstance(section.get("title"), str)
            self.assertNotIn("报考条件报考条件", section.get("title"))
            self.assertNotIn("读EMBA报", section.get("title"))
            self.assertIsInstance(section.get("key_points"), list)
            self.assertGreater(len(section.get("key_points")), 0)
            self.assertEqual(section.get("notes"), "mock")

        expected_citation_keys = {"title", "url", "source", "authority", "citation", "note"}
        for item in out.get("citations") or []:
            self.assertIsInstance(item, dict)
            self.assertEqual(set(item.keys()), expected_citation_keys)
            self.assertIsInstance(item.get("title"), str)
            self.assertIsInstance(item.get("url"), str)
            self.assertEqual(item.get("source"), "mock_source")
            self.assertEqual(item.get("authority"), "low")
            self.assertIsInstance(item.get("citation"), str)
            self.assertEqual(item.get("note"), "mock_source")
        self.assertTrue(out.get("is_mock"))
        self.assertEqual(out.get("data_confidence"), "low")
        json.dumps(out, ensure_ascii=False)

    def test_missing_topic_fields_does_not_crash(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        out = asyncio.run(agent.execute(topic={"title": "EMBA"}, mode="mock"))
        self.assertIsInstance(out.get("warnings"), list)
        self.assertTrue(any("missing_topic_field" in str(x) for x in out.get("warnings")))

    def test_rewrite_candidate_returns_rule_based_research_brief_without_collector(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "rewrite_required": True,
            "publish_candidate": False,
            "topic_id": "topic_123",
            "candidate_id": 123,
            "title": "2026年EMBA报考条件详解：适合人群、申请流程与准备建议",
            "primary_keyword": "EMBA报考条件",
            "secondary_keywords": ["EMBA申请流程"],
            "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
            "search_intent": "informational",
            "content_type": "guide",
            "content_angle": "conditions",
            "source_title": "EMBA 报考条件",
            "source_summary": "EMBA报考通常需要一定工作年限与管理经验，申请前需准备材料并关注时间线。",
            "source_url": "https://example.com/source",
            "source_content": "EMBA报考通常需要一定工作年限与管理经验。申请流程包括材料准备、面试与时间安排。不同项目的具体要求可能存在差异，申请者应以项目官方口径为准。",
            "material_score": 75.0,
            "evaluation": {"source_ok": True, "has_risk": False},
            "dedup": {"similarity_score": 0.2},
            "routing_payload": {"original_key": "val"},
        }

        with patch("agents.research_agent.research_agent.DataCollector.collect", new=AsyncMock(side_effect=AssertionError("collector should not be called"))):
            out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))

        self.assertIsInstance(out, dict)
        self.assertIn("research_brief", out)
        brief = out["research_brief"]
        self.assertEqual(brief["brief_type"], "rewrite_candidate_research_brief")
        self.assertEqual(brief["workflow_route"], "full_rewrite_flow")
        self.assertEqual(brief["route_tier"], "rewrite_candidate")
        self.assertEqual(brief["topic_id"], "topic_123")
        self.assertEqual(brief["candidate_id"], 123)
        self.assertEqual(brief["primary_keyword"], "EMBA报考条件")
        self.assertEqual(brief["target_keywords"], ["EMBA报考条件", "EMBA申请流程"])
        self.assertIn("source_snapshot", brief)
        self.assertIn("source_highlights", brief)
        self.assertIn("key_facts", brief)
        self.assertIn("risk_points", brief)
        self.assertIn("rewrite_constraints", brief)
        self.assertIn("writer_outline", brief)
        self.assertIsInstance(brief["source_highlights"], list)
        self.assertIsInstance(brief["key_facts"], list)
        self.assertIsInstance(brief["risk_points"], list)
        self.assertIsInstance(brief["rewrite_constraints"], list)
        self.assertNotIn("article", out)
        self.assertNotIn("cms_result", out)
        self.assertIn("outline", out)
        self.assertIn("sources", out)
        self.assertIn("citations", out)
        self.assertIsInstance((out.get("outline") or {}).get("sections"), list)
        self.assertIsInstance(out.get("sources"), list)
        self.assertIsInstance(out.get("citations"), list)
        self.assertFalse(out.get("is_mock"))
        json.dumps(out, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
