import asyncio
import json
import unittest


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


if __name__ == "__main__":
    unittest.main()
