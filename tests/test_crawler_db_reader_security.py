import asyncio
import unittest

from agents.crawler_processor_agent.tools.crawler_db_reader import CrawlerDBReader


class TestCrawlerDBReaderSecurity(unittest.TestCase):
    def test_invalid_identifier_rejected(self):
        async def run():
            r = CrawlerDBReader(
                {
                    "type": "mysql",
                    "host": "localhost",
                    "port": 3306,
                    "database": "x",
                    "table": "crawled_content;drop table x",
                    "status_field": "status",
                    "pending_status": "pending",
                }
            )
            out = await r.read_pending(limit=1)
            self.assertFalse(out.get("success"))
            self.assertEqual(out.get("error"), "invalid_identifier")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

