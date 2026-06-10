import asyncio
import unittest


class TestSEOAgentContract(unittest.TestCase):
    def test_execute_produces_required_fields_without_api_key(self):
        from agents.seo_agent import SEOAgent

        agent = SEOAgent()
        out = asyncio.run(
            agent.execute(
                article={
                    "title": "如何选择适合自己的项目",
                    "content_md": "EMBA选择需要综合考虑学费、师资与校友网络等因素。",
                    "slug": "emba-choice",
                },
                topic={
                    "title": "如何选择适合自己的项目",
                    "primary_keyword": "EMBA选择",
                    "secondary_keywords": ["EMBA学费", "商学院"],
                    "content_type": "guide",
                },
                page_info={"category": "EMBA"},
                dry_run=True,
            )
        )

        self.assertIsInstance(out, dict)
        for k in [
            "optimized_article",
            "meta_title",
            "meta_description",
            "og_tags",
            "twitter_tags",
            "schema_json",
            "internal_links",
            "seo_report",
            "improvement_suggestions",
            "warnings",
        ]:
            self.assertIn(k, out)

        self.assertIsInstance(out.get("schema_json"), dict)
        self.assertIsInstance(out.get("internal_links"), list)
        self.assertIsInstance(out.get("improvement_suggestions"), list)
        self.assertIsInstance(out.get("warnings"), list)


if __name__ == "__main__":
    unittest.main()
