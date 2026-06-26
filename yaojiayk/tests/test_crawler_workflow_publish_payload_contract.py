import asyncio
import unittest

from workflows.crawler_workflow import run_crawler_workflow


class TestCrawlerWorkflowPublishPayloadContract(unittest.TestCase):
    def test_pass_to_topic_payload_contract(self):
        async def run():
            items = [
                {
                    "id": 1,
                    "title": "EMBA 院校选择策略",
                    "content": (
                        "根据最新招生数据，EMBA 院校选择需要同时比较课程设置、校友资源、学费回报与地域机会。"
                        "\n\n首先，考生需要明确自身管理经验、行业背景与职业目标。"
                        "\n\n其次，可以通过招生简章、往届案例、面试流程与课程模块来判断项目匹配度。"
                        "\n\n例如，部分院校更强调战略管理与全球访学，部分院校则更适合本地企业高管。"
                        "\n\n最后，建议结合预算、时间投入、师资和校友网络做结论。"
                    ) * 8,
                    "source_url": "https://example.com/a",
                    "category": "news",
                    "author": "author_test",
                    "spider_name": "spider_test",
                }
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["k"],
                dry_run=True,
                config={
                    "execution": {"llm_decision_enabled": False},
                    "crawler_db": {"pass_to_topic_status": "pass_to_topic", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {
                        "discard_below_score": 40,
                        "publish_candidate_threshold": 80,
                        "min_word_count": 80,
                        "max_word_count": 5000,
                    },
                },
            )
            payload = out["items"][0]["next_payload"]
            self.assertEqual(out["items"][0]["decision"], "pass_to_topic")
            self.assertEqual(out["items"][0]["next_agent"], "TopicAgent")
            self.assertEqual(payload["topic_hint"], "EMBA 院校选择策略")
            self.assertEqual(payload["source_title"], "EMBA 院校选择策略")
            self.assertTrue(payload["source_summary"].startswith("根据最新招生数据"))
            self.assertEqual(payload["source_url"], "https://example.com/a")
            self.assertGreaterEqual(payload["material_score"], 80)
            self.assertEqual(payload["route_tier"], "publish_candidate")
            self.assertFalse(payload["rewrite_required"])
            self.assertTrue(payload["publish_candidate"])
            self.assertNotIn("candidate_topic", payload)
            self.assertNotIn("primary_keyword", payload)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
