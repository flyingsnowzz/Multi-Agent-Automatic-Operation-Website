import unittest
import asyncio

from agents.data_agent.tools.analytics_collector import AnalyticsCollector, DataSource


class TestAnalyticsCollectorCompare(unittest.TestCase):
    def test_compare_periods_no_credentials(self):
        async def run():
            collector = AnalyticsCollector()
            try:
                result = await collector.compare_periods(
                    current_start="2026-06-09",
                    current_end="2026-06-09",
                    previous_start="2026-06-08",
                    previous_end="2026-06-08",
                    sources=[DataSource.GOOGLE_ANALYTICS],
                )
                self.assertIn("metrics", result)
                self.assertIn("sessions", result["metrics"])
            finally:
                await collector.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

