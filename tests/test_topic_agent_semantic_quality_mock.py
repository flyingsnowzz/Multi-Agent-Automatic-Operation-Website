import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents.topic_agent import TopicAgent
from agents.topic_agent.tools.keyword_research import KeywordData, KeywordResearchResult


class TestTopicAgentSemanticQualityMock(unittest.TestCase):
    def test_emba_outputs_at_least_three_clear_topics(self):
        agent = TopicAgent(mode="mock")
        out = asyncio.run(agent.execute(keywords=["EMBA"], limit=8))
        topics = out["topics"]
        self.assertGreaterEqual(len(topics), 3)

        bad = ["怎么EMBA", "如何EMBA", "EMBA技巧", "EMBA方法", "EMBA 工具", "EMBA工具", "EMBA 方法"]
        for t in topics:
            self.assertGreaterEqual(t["semantic_quality_score"], 70.0)
            for b in bad:
                self.assertNotIn(b, t["title"])
                self.assertNotIn(b, t["target_keywords"][0])

    def test_low_semantic_topic_is_filtered_even_if_keyword_tool_returns_it(self):
        agent = TopicAgent(mode="mock")

        async def _fake_kw(*_, **__):
            return KeywordResearchResult(
                primary_keywords=[
                    KeywordData(keyword="怎么EMBA", search_volume=5000, keyword_difficulty=10, source="mock", is_mock=True),
                    KeywordData(keyword="EMBA报考条件", search_volume=1200, keyword_difficulty=20, source="mock", is_mock=True),
                    KeywordData(keyword="EMBA申请流程", search_volume=800, keyword_difficulty=18, source="mock", is_mock=True),
                ],
                long_tail_keywords=[],
                questions=[],
                gaps=[],
                is_mock=True,
                data_confidence="low",
                warnings=[],
            )

        class _DummySERP:
            def __init__(self, keyword: str):
                self.keyword = keyword
                self.total_results = 100000
                self.competition_score = 20
                self.top_domains = []
                self.avg_word_count = 1200
                self.content_gaps = []
                self.opportunities = []

        with patch("agents.topic_agent.tools.keyword_research.KeywordResearchTool.research_keywords", new=AsyncMock(side_effect=_fake_kw)):
            with patch("agents.topic_agent.tools.serp_analysis.SERPAnalysisTool.analyze_serp", new=AsyncMock(side_effect=lambda kw: _DummySERP(kw))):
                with patch(
                    "agents.topic_agent.tools.trend_detection.TrendDetectionTool.detect_trends",
                    new=AsyncMock(return_value=[SimpleNamespace(trend_score=80)]),
                ):
                    out = asyncio.run(agent.execute(keywords=["EMBA"], limit=5))

        topics = out["topics"]
        self.assertTrue(all(t["target_keywords"][0] != "怎么EMBA" for t in topics))


if __name__ == "__main__":
    unittest.main()

