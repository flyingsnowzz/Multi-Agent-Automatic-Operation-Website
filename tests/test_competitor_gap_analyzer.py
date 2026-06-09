import unittest

from agents.competitor_agent.tools.gap_analyzer import GapAnalyzer


class TestGapAnalyzer(unittest.TestCase):
    def test_extract_keywords_from_entries(self):
        a = GapAnalyzer()
        kws = a.extract_keywords_from_entries(
            [
                {"title": "EMBA学费排名2026", "summary": "EMBA 学费 排名 对比"},
                {"title": "EMBA申请指南", "summary": "申请 条件 资料"},
            ]
        )
        self.assertTrue(len(kws) > 0)

    def test_serp_gaps_uses_domain(self):
        a = GapAnalyzer(our_domain="example.com")
        out = a.analyze_serp_gaps(
            target_keywords=["k1"],
            serp_results={"k1": [{"domain": "abc.com"}, {"domain": "example.com"}]},
        )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("low_ranking_count"), 0)


if __name__ == "__main__":
    unittest.main()

