import unittest

from agents.topic_agent import TopicAgent


class TestTopicAgentPriorityWeights(unittest.TestCase):
    def test_priority_score_respects_search_volume_weight(self):
        agent = TopicAgent(mode="mock")
        agent.config = {
            "topic": {
                "keyword_difficulty": {"max": 35},
                "search_volume": {"preferred": 500},
                "output": {"prioritize_diversity": False},
            },
            "keyword_research": {"filters": {"prefer": ["指南"]}},
            "output": {"priority_weights": {"search_volume": 1.0, "keyword_difficulty": 0.0, "competition_gap": 0.0, "trending_score": 0.0, "strategic_value": 0.0}},
        }
        high = agent._priority_score(
            keyword="A指南",
            search_volume=1000,
            kd=35,
            competition_score=80,
            trend_score=0,
            content_gaps=[],
            opportunities=[],
        )["score"]
        low = agent._priority_score(
            keyword="A指南",
            search_volume=0,
            kd=0,
            competition_score=0,
            trend_score=100,
            content_gaps=["x"] * 10,
            opportunities=["y"] * 10,
        )["score"]
        self.assertGreater(high, low)

    def test_priority_score_respects_kd_weight(self):
        agent = TopicAgent(mode="mock")
        agent.config = {
            "topic": {
                "keyword_difficulty": {"max": 35},
                "search_volume": {"preferred": 500},
                "output": {"prioritize_diversity": False},
            },
            "output": {"priority_weights": {"search_volume": 0.0, "keyword_difficulty": 1.0, "competition_gap": 0.0, "trending_score": 0.0, "strategic_value": 0.0}},
        }
        easy = agent._priority_score(
            keyword="A",
            search_volume=0,
            kd=0,
            competition_score=0,
            trend_score=0,
            content_gaps=[],
            opportunities=[],
        )["score"]
        hard = agent._priority_score(
            keyword="A",
            search_volume=100000,
            kd=35,
            competition_score=0,
            trend_score=100,
            content_gaps=["x"] * 10,
            opportunities=["y"] * 10,
        )["score"]
        self.assertGreater(easy, hard)


if __name__ == "__main__":
    unittest.main()

