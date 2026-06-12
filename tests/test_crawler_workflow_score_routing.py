import asyncio
import unittest

from workflows.crawler_workflow import run_crawler_workflow


def _config():
    return {
        "execution": {"llm_decision_enabled": False},
        "crawler_db": {
            "pass_to_topic_status": "pass_to_topic",
            "discard_status": "discarded",
        },
        "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
        "evaluation_criteria": {
            "material_score_threshold": 50,
            "min_word_count": 80,
            "max_word_count": 5000,
            "input_required_fields": ["title", "content", "source_url"],
            "require_source_ok": True,
            "require_topic_hint": True,
        },
    }


class TestCrawlerWorkflowScoreRouting(unittest.TestCase):
    def test_material_score_and_topic_hint_control_routing(self):
        async def run():
            scores = [100, 99, 90, 89.99, 89, 40, 39.99, 39, 0]
            out = await run_crawler_workflow(
                items=[
                    {
                        "id": idx + 1,
                        "title": f"EMBA 项目选择方法 {score}" if score >= 50 else "资讯",
                        "content": (("EMBA 项目选择方法 数据 案例 步骤 总结 " * 80) if score >= 50 else "资讯资讯资讯"),
                        "source_url": f"https://example.com/{idx}",
                    }
                    for idx, score in enumerate(scores)
                ],
                published_articles=[],
                target_keywords=["EMBA"],
                dry_run=True,
                config=_config(),
                persist_run=False,
            )
            return out

        out = asyncio.run(run())
        decisions = [item["decision"] for item in out["items"]]
        self.assertEqual(
            decisions,
            ["pass_to_topic", "pass_to_topic", "pass_to_topic", "pass_to_topic", "pass_to_topic", "discard", "discard", "discard", "discard"],
        )
        statuses = [item["status_to_update"] for item in out["items"]]
        self.assertEqual(
            statuses,
            [
                "pass_to_topic",
                "pass_to_topic",
                "pass_to_topic",
                "pass_to_topic",
                "pass_to_topic",
                "discarded",
                "discarded",
                "discarded",
                "discarded",
            ],
        )
        next_agents = [item["next_agent"] for item in out["items"]]
        self.assertEqual(
            next_agents,
            ["TopicAgent", "TopicAgent", "TopicAgent", "TopicAgent", "TopicAgent", None, None, None, None],
        )


if __name__ == "__main__":
    unittest.main()
