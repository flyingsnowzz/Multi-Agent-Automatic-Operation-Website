import unittest
from datetime import date

from agents.data_agent.data_agent import DataAgent


class TestDataAgentDateRanges(unittest.TestCase):
    def test_daily_periods(self):
        agent = DataAgent(config_path="__missing__")
        current, previous = agent._default_periods("daily", today=date(2026, 6, 9))
        self.assertEqual(current, {"start": "2026-06-09", "end": "2026-06-09"})
        self.assertEqual(previous, {"start": "2026-06-08", "end": "2026-06-08"})

    def test_weekly_periods_last_week(self):
        agent = DataAgent(config_path="__missing__")
        current, previous = agent._default_periods("weekly", today=date(2026, 6, 9))
        self.assertEqual(current, {"start": "2026-06-01", "end": "2026-06-07"})
        self.assertEqual(previous, {"start": "2026-05-25", "end": "2026-05-31"})

    def test_monthly_periods_last_month(self):
        agent = DataAgent(config_path="__missing__")
        current, previous = agent._default_periods("monthly", today=date(2026, 6, 9))
        self.assertEqual(current, {"start": "2026-05-01", "end": "2026-05-31"})
        self.assertEqual(previous, {"start": "2026-04-01", "end": "2026-04-30"})


if __name__ == "__main__":
    unittest.main()

