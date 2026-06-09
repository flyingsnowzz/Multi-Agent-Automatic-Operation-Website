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


if __name__ == "__main__":
    unittest.main()

