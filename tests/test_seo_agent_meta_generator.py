import unittest

from agents.seo_agent.tools.meta_generator import MetaGenerator


class TestSEOAgentMetaGenerator(unittest.TestCase):
    def test_description_contains_primary_keyword_without_secondary(self):
        g = MetaGenerator()
        out = g.generate(
            title="如何选择适合自己的项目",
            content="EMBA选择涉及学费、师资与校友网络等多个方面。",
            primary_keyword="EMBA选择",
            secondary_keywords=[],
            language="chinese",
        )
        self.assertIn("EMBA选择", out.get("meta_description") or "")

    def test_html_escape(self):
        g = MetaGenerator()
        out = g.generate(
            title='x" onerror="alert(1)',
            content='y" z',
            primary_keyword="测试",
            secondary_keywords=[],
            language="chinese",
        )
        html_out = g.generate_html(out)
        self.assertNotIn('onerror="alert(1)', html_out)
        self.assertIn("&quot;", html_out)


if __name__ == "__main__":
    unittest.main()

