import unittest

from agents.data_agent.tools.report_generator import ReportGenerator


class TestReportGeneratorStructured(unittest.TestCase):
    def test_generate_structured_and_markdown(self):
        g = ReportGenerator()
        report = g.generate_structured(
            report_type="daily",
            current_period={"start": "2026-06-09", "end": "2026-06-09"},
            previous_period={"start": "2026-06-08", "end": "2026-06-08"},
            current_data={
                "google_analytics": {
                    "success": True,
                    "data": {
                        "sessions": 10,
                        "pageviews": 20,
                        "users": 5,
                        "bounce_rate": 40.0,
                        "top_pages": [{"path": "/a", "pageviews": 10}],
                        "top_sources": [{"source": "google", "sessions": 10}],
                    },
                }
            },
            previous_data={"google_analytics": {"success": True, "data": {"sessions": 8, "pageviews": 18, "users": 4, "bounce_rate": 35.0}}},
            comparison_metrics={
                "sessions": {"current": 10, "previous": 8, "change_percent": 25.0},
                "pageviews": {"current": 20, "previous": 18, "change_percent": 11.11},
                "users": {"current": 5, "previous": 4, "change_percent": 25.0},
                "bounce_rate": {"current": 40, "previous": 35, "change_percent": 14.29},
            },
            anomalies=[],
            recommendations=["a"],
        )
        md = g.format_markdown(report)
        self.assertIn("核心指标", md)
        self.assertIn("| Sessions |", md)


if __name__ == "__main__":
    unittest.main()

