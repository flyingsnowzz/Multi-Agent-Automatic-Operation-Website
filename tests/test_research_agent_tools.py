import unittest
import asyncio

from agents.research_agent.tools.data_collector import DataCollector
from agents.research_agent.tools.citation_formatter import CitationFormatter, CitationStyle, get_citation_formatter_tool


class TestResearchAgentTools(unittest.TestCase):
    def test_data_collector_sources_do_not_index_error(self):
        collector = DataCollector()
        try:
            out = asyncio.run(
                collector.collect(
                    topic="t",
                    keywords=["k1", "k2"],
                    sources=["expert_opinions", "official_statistics", "unknown_x"],
                )
            )
            self.assertIn("data", out)
            self.assertIn("expert_opinions", out["data"])
            self.assertIn("official_statistics", out["data"])
            self.assertIn("unknown_x", out["data"])
            self.assertEqual(out["data"]["unknown_x"].get("error"), "unknown_source")
        finally:
            asyncio.run(collector.close())

    def test_data_collector_can_be_called_twice(self):
        collector = DataCollector()
        try:
            a = asyncio.run(collector.collect(topic="t", keywords=["k"], sources=["official_statistics"]))
            b = asyncio.run(collector.collect(topic="t", keywords=["k"], sources=["official_statistics"]))
            self.assertTrue(a["data"]["official_statistics"].get("success"))
            self.assertTrue(b["data"]["official_statistics"].get("success"))
        finally:
            asyncio.run(collector.close())

    def test_citation_formatter_authors_string(self):
        f = CitationFormatter(CitationStyle.GB_T7714)
        out = f.format({"type": "journal", "authors": "张三,李四", "title": "t", "journal": "j", "year": "2024"})
        self.assertIn("张三", out)
        self.assertIn("李四", out)

    def test_citation_formatter_tool_invalid_json(self):
        tool_fn = get_citation_formatter_tool()
        out = tool_fn.run(sources_json="{", style="gb_t7714")
        self.assertTrue(out.startswith("ERROR:"))


if __name__ == "__main__":
    unittest.main()
