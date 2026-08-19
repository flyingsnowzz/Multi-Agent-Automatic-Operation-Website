import unittest
from datetime import datetime, timezone
from pathlib import Path

from agents.active_research_agent.active_research_agent import ActiveResearchAgent


class ActiveResearchAgentTests(unittest.TestCase):
    def test_keyword_groups_have_no_weight_side_effect(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))
        pairs = agent.iter_keywords({"keyword_groups": {"mba": ["MBA"], "edu": ["考研"]}})

        self.assertEqual(pairs, [("mba", "MBA"), ("edu", "考研")])

    def test_score_candidate_uses_transparent_breakdown(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"), min_topic_score=75)
        published = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
        now = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)

        score, breakdown, reasons = agent._score_candidate(
            title="MBA招生政策发布，管理类联考报名条件调整",
            summary="多所商学院同步更新申请流程，方便考生准备材料。",
            keyword="MBA",
            published_at=published,
            source_name="中国教育在线",
            now=now,
        )

        self.assertGreaterEqual(score, 75)
        self.assertIn("business_relevance", breakdown)
        self.assertIn("keyword_scope_match", reasons)

    def test_dedupe_keeps_highest_scored_candidate_for_same_title(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))
        base = {
            "title": "MBA招生政策发布",
            "url": "https://example.com/a?utm_source=x",
            "source_name": "source",
            "source_type": "rss",
            "keyword": "MBA",
            "keyword_group": "mba",
            "published_at": None,
            "summary": "",
            "fetched_at": "2026-08-18T00:00:00+00:00",
            "dedup_key": "a",
            "score_breakdown": {},
            "reasons": [],
        }
        low = agent.to_jsonable([])
        self.assertEqual(low, [])

        from agents.active_research_agent.active_research_agent import ActiveResearchCandidate

        first = ActiveResearchCandidate(topic_score=70, **base)
        second = ActiveResearchCandidate(topic_score=90, **{**base, "url": "https://example.com/b", "dedup_key": "b"})

        out = agent._dedupe_candidates([first, second])

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].topic_score, 90)


if __name__ == "__main__":
    unittest.main()
