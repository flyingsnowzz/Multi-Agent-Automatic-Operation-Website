import asyncio
import unittest

from agents.crawler_processor_agent.tools.dedup_checker import check_duplicate


class TestDedupCheckerStrategies(unittest.TestCase):
    def test_url_exact_match(self):
        async def run():
            out = await check_duplicate(
                title="x",
                content="y",
                source_url="https://a.com/1",
                published_articles=[{"title": "t", "content": "c", "source_url": "https://a.com/1"}],
            )
            self.assertTrue(out.get("success"))
            self.assertTrue(out.get("is_duplicate"))
            self.assertEqual((out.get("details") or {}).get("match_type"), "url")

        asyncio.run(run())

    def test_title_norm_match(self):
        async def run():
            out = await check_duplicate(
                title="你好，世界！",
                content="y",
                source_url=None,
                published_articles=[{"title": "你好 世界", "content": "c", "source_url": ""}],
            )
            self.assertTrue(out.get("success"))
            self.assertTrue(out.get("is_duplicate"))
            self.assertEqual((out.get("details") or {}).get("match_type"), "title_norm")

        asyncio.run(run())

    def test_url_normalization_match(self):
        async def run():
            out = await check_duplicate(
                title="x",
                content="y",
                source_url="https://a.com/1?utm_source=ad&ref=123&spm=99",
                published_articles=[{"title": "t", "content": "c", "source_url": "https://a.com/1"}],
            )
            self.assertTrue(out.get("success"))
            self.assertTrue(out.get("is_duplicate"))
            self.assertEqual((out.get("details") or {}).get("match_type"), "url")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

