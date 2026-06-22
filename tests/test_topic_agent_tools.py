import unittest
import asyncio
import os

from agents.topic_agent.tools.keyword_research import KeywordResearchTool
from agents.topic_agent.tools.trend_detection import TrendDetectionTool
from agents.topic_agent.tools.serp_analysis import SERPAnalysisTool
from agents.topic_agent import TopicAgent


class TestTopicAgentTools(unittest.TestCase):
    def test_questions_type_is_list_of_str(self):
        tool = KeywordResearchTool(config={"mode": "mock"})
        out = asyncio.run(tool.research_keywords(seed_keywords=["EMBA"], min_search_volume=0, max_kd=100, limit=10))
        self.assertTrue(all(isinstance(x, str) for x in out.questions))
        bad = ["怎么EMBA", "如何EMBA", "EMBA技巧", "EMBA方法", "EMBA 工具", "EMBA工具", "EMBA 方法"]
        all_kw = [k.keyword for k in out.primary_keywords + out.long_tail_keywords] + list(out.questions)
        for x in all_kw:
            for b in bad:
                self.assertNotIn(b, x)

    def test_live_mode_without_key_does_not_return_mock(self):
        old = os.environ.get("SERPAPI_API_KEY")
        os.environ["SERPAPI_API_KEY"] = ""
        try:
            tool = KeywordResearchTool(config={"mode": "live"})
            with self.assertRaises(RuntimeError):
                asyncio.run(tool.research_keywords(seed_keywords=["EMBA"], min_search_volume=0, max_kd=100, limit=10))
        finally:
            if old is None:
                os.environ.pop("SERPAPI_API_KEY", None)
            else:
                os.environ["SERPAPI_API_KEY"] = old

    def test_trend_score_not_negative(self):
        tool = TrendDetectionTool()
        score = tool.calculate_trend_score(current_volume=100, previous_volume=1000, search_velocity=0.1)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        asyncio.run(tool.close())

    def test_serp_domain_parse_robust(self):
        tool = SERPAnalysisTool(config={"mode": "mock"})
        self.assertEqual(tool._extract_domain("https://example.com/a/b"), "example.com")
        self.assertEqual(tool._extract_domain("example.com/a"), "example.com")

    def test_topic_agent_importable(self):
        self.assertTrue(callable(TopicAgent))

    def test_topic_agent_execute_contract(self):
        agent = TopicAgent(mode="mock")
        out = asyncio.run(agent.execute(keywords=["EMBA"], limit=5))
        
        # 1. 验证顶级返回字典的契约
        self.assertIsInstance(out, dict)
        required_top_keys = {
            "topics",
            "raw_keyword_data",
            "raw_serp_data",
            "warnings",
            "is_mock",
            "data_confidence",
            "generated_at"
        }
        for key in required_top_keys:
            self.assertIn(key, out)
            
        # 2. 验证 topics 列表中每个主题的契约
        topics = out["topics"]
        self.assertIsInstance(topics, list)
        self.assertGreater(len(topics), 0)
        
        required_topic_keys = {
            "id",
            "title",
            "target_keywords",
            "search_volume",
            "keyword_difficulty",
            "competition_level",
            "content_type",
            "search_intent",
            "outline_points",
            "priority",
            "reason",
            "estimated_difficulty",
            "data_sources"
        }
        
        for topic in topics:
            self.assertIsInstance(topic, dict)
            for key in required_topic_keys:
                self.assertIn(key, topic)
            
            # 验证具体要求的核心字段类型与非空
            self.assertIsInstance(topic["title"], str)
            self.assertTrue(len(topic["title"]) > 0)
            
            self.assertIsInstance(topic["target_keywords"], list)
            self.assertGreater(len(topic["target_keywords"]), 0)
            self.assertTrue(all(isinstance(x, str) for x in topic["target_keywords"]))
            
            self.assertIsInstance(topic["search_volume"], int)
            self.assertIsInstance(topic["keyword_difficulty"], float)
            
            self.assertIsInstance(topic["content_type"], str)
            self.assertIn(topic["content_type"], ["guide", "comparison", "list", "case_study", "how_to"])
            
            self.assertIsInstance(topic["priority"], str)
            self.assertIn(topic["priority"], ["high", "medium", "low"])
            
            self.assertIsInstance(topic["reason"], str)
            self.assertTrue(len(topic["reason"]) > 0)

            self.assertIn("semantic_quality_score", topic)
            self.assertIsInstance(topic["semantic_quality_score"], float)
            self.assertGreaterEqual(topic["semantic_quality_score"], 0.0)
            self.assertLessEqual(topic["semantic_quality_score"], 100.0)

            self.assertIn("quality_warnings", topic)
            self.assertIsInstance(topic["quality_warnings"], list)

            bad_title = ["怎么EMBA", "如何EMBA", "EMBA技巧", "EMBA方法", "EMBA 工具", "EMBA工具", "EMBA 方法"]
            for b in bad_title:
                self.assertNotIn(b, topic["title"])


if __name__ == "__main__":
    unittest.main()
