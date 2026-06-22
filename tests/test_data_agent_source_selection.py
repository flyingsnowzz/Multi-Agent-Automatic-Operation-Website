import unittest

from agents.data_agent.data_agent import DataAgent
from agents.data_agent.tools.analytics_collector import DataSource


class TestDataAgentSourceSelection(unittest.TestCase):
    def test_enabled_sources_and_unimplemented(self):
        agent = DataAgent(config_path="__missing__")
        agent.config = {
            "data_sources": {
                "google_analytics": {"enabled": True},
                "google_search_console": {"enabled": True},
                "baidu_tongji": {"enabled": False},
                "ahrefs": {"enabled": True},
                "semrush": {"enabled": False},
            }
        }
        sources, errors = agent._get_enabled_sources()
        self.assertEqual(sources, [DataSource.GOOGLE_ANALYTICS, DataSource.SEARCH_CONSOLE])
        self.assertIn("data_source_not_implemented:ahrefs", errors)


if __name__ == "__main__":
    unittest.main()

