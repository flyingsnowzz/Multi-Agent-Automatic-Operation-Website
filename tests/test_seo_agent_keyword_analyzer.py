import unittest

from agents.seo_agent.tools.keyword_analyzer import KeywordAnalyzer


class TestSEOAgentKeywordAnalyzer(unittest.TestCase):
    def test_chinese_heading_preserved(self):
        content = "# 标题\n\n## EMBA选择标准\n\n这里讨论EMBA选择的关键因素。\n\n## 其他\n\n结尾再提一次EMBA选择。"
        analyzer = KeywordAnalyzer()
        out = analyzer.analyze(content=content, primary_keyword="EMBA选择", secondary_keywords=[], language="chinese")
        headings = (out.get("distribution") or {}).get("headings") or []
        self.assertTrue(any("EMBA选择" in h for h in headings), headings)

    def test_english_phrase_count(self):
        content = "A business school program is different from another business school program."
        analyzer = KeywordAnalyzer()
        out = analyzer.analyze(content=content, primary_keyword="business school", secondary_keywords=[], language="english")
        self.assertEqual(out.get("primary_count"), 2)


if __name__ == "__main__":
    unittest.main()

