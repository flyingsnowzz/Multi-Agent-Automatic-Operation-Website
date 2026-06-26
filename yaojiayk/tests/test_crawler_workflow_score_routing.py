import asyncio
import unittest
from unittest.mock import patch


def _config():
    return {
        "crawler_db": {
            "pass_to_topic_status": "pass_to_topic",
            "discard_status": "discarded",
        },
        "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
        "evaluation_criteria": {
            "discard_below_score": 40,
            "publish_candidate_threshold": 80,
            "material_score_threshold": 40,
            "min_word_count": 80,
            "max_word_count": 5000,
            "input_required_fields": ["title", "content", "source_url"],
            "require_source_ok": True,
            "require_topic_hint": True,
        },
    }


class TestCrawlerWorkflowScoreRouting(unittest.TestCase):
    @patch("workflows.crawler_workflow.check_duplicate")
    @patch("workflows.crawler_workflow.evaluate_content")
    def test_material_score_and_topic_hint_control_routing(self, mock_evaluate, mock_check_duplicate):
        async def mock_evaluate_side_effect(title, content, source_url, target_keywords, config):
            import re
            match = re.search(r"score_([\d\.]+)", title)
            score = float(match.group(1)) if match else 0.0
            has_risk = "risk" in title
            source_ok = "badsource" not in title
            topic_hint = "" if "notopic" in title else "some_topic"
            return {
                "success": True,
                "material_score": score,
                "has_risk": has_risk,
                "source_ok": source_ok,
                "topic_hint": topic_hint,
                "reason": "mocked",
                "word_count": 100,
                "details": {},
            }

        async def mock_check_duplicate_side_effect(title, content, source_url, published_articles, threshold, algorithm, config):
            is_dup = "duplicate" in title
            return {
                "success": True,
                "is_duplicate": is_dup,
                "similarity_score": 1.0 if is_dup else 0.0,
                "matched_article": None,
                "details": {},
            }

        mock_evaluate.side_effect = mock_evaluate_side_effect
        mock_check_duplicate.side_effect = mock_check_duplicate_side_effect

        async def run():
            from workflows.crawler_workflow import run_crawler_workflow
            items = [
                {"id": 1, "title": "score_39.0", "content": "content", "source_url": "https://example.com/1"},
                {"id": 2, "title": "score_40.0", "content": "content", "source_url": "https://example.com/2"},
                {"id": 3, "title": "score_79.99", "content": "content", "source_url": "https://example.com/3"},
                {"id": 4, "title": "score_80.0", "content": "content", "source_url": "https://example.com/4"},
                {"id": 5, "title": "score_81.0", "content": "content", "source_url": "https://example.com/5"},
                {"id": 6, "title": "score_85.0_duplicate", "content": "content", "source_url": "https://example.com/6"},
                {"id": 7, "title": "score_85.0_risk", "content": "content", "source_url": "https://example.com/7"},
                {"id": 8, "title": "score_85.0_badsource", "content": "content", "source_url": "https://example.com/8"},
                {"id": 9, "title": "score_85.0_notopic", "content": "content", "source_url": "https://example.com/9"},
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["EMBA"],
                dry_run=True,
                config=_config(),
                persist_run=False,
            )
            return out

        out = asyncio.run(run())
        processed_items = out["items"]

        # item 1: 39.0 -> discard
        self.assertEqual(processed_items[0]["decision"], "discard")
        self.assertEqual(processed_items[0]["status_to_update"], "discarded")
        self.assertIsNone(processed_items[0]["next_agent"])
        self.assertIsNone(processed_items[0]["next_payload"])

        # item 2: 40.0 -> rewrite_candidate
        self.assertEqual(processed_items[1]["decision"], "pass_to_topic")
        self.assertEqual(processed_items[1]["status_to_update"], "pass_to_topic")
        self.assertEqual(processed_items[1]["next_agent"], "TopicAgent")
        self.assertEqual(processed_items[1]["next_payload"]["route_tier"], "rewrite_candidate")
        self.assertEqual(processed_items[1]["next_payload"]["rewrite_required"], True)
        self.assertEqual(processed_items[1]["next_payload"]["publish_candidate"], False)
        self.assertEqual(processed_items[1]["next_payload"]["material_score"], 40.0)

        # item 3: 79.99 -> rewrite_candidate
        self.assertEqual(processed_items[2]["decision"], "pass_to_topic")
        self.assertEqual(processed_items[2]["status_to_update"], "pass_to_topic")
        self.assertEqual(processed_items[2]["next_agent"], "TopicAgent")
        self.assertEqual(processed_items[2]["next_payload"]["route_tier"], "rewrite_candidate")
        self.assertEqual(processed_items[2]["next_payload"]["rewrite_required"], True)
        self.assertEqual(processed_items[2]["next_payload"]["publish_candidate"], False)
        self.assertEqual(processed_items[2]["next_payload"]["material_score"], 79.99)

        # item 4: 80.0 -> publish_candidate
        self.assertEqual(processed_items[3]["decision"], "pass_to_topic")
        self.assertEqual(processed_items[3]["status_to_update"], "pass_to_topic")
        self.assertEqual(processed_items[3]["next_agent"], "TopicAgent")
        self.assertEqual(processed_items[3]["next_payload"]["route_tier"], "publish_candidate")
        self.assertEqual(processed_items[3]["next_payload"]["rewrite_required"], False)
        self.assertEqual(processed_items[3]["next_payload"]["publish_candidate"], True)
        self.assertEqual(processed_items[3]["next_payload"]["material_score"], 80.0)

        # item 5: 81.0 -> publish_candidate
        self.assertEqual(processed_items[4]["decision"], "pass_to_topic")
        self.assertEqual(processed_items[4]["status_to_update"], "pass_to_topic")
        self.assertEqual(processed_items[4]["next_agent"], "TopicAgent")
        self.assertEqual(processed_items[4]["next_payload"]["route_tier"], "publish_candidate")
        self.assertEqual(processed_items[4]["next_payload"]["rewrite_required"], False)
        self.assertEqual(processed_items[4]["next_payload"]["publish_candidate"], True)
        self.assertEqual(processed_items[4]["next_payload"]["material_score"], 81.0)

        # item 6: 85.0_duplicate -> discard
        self.assertEqual(processed_items[5]["decision"], "discard")
        self.assertIsNone(processed_items[5]["next_agent"])

        # item 7: 85.0_risk -> discard
        self.assertEqual(processed_items[6]["decision"], "discard")
        self.assertIsNone(processed_items[6]["next_agent"])

        # item 8: 85.0_badsource -> discard
        self.assertEqual(processed_items[7]["decision"], "discard")
        self.assertIsNone(processed_items[7]["next_agent"])

        # item 9: 85.0_notopic -> discard
        self.assertEqual(processed_items[8]["decision"], "discard")
        self.assertIsNone(processed_items[8]["next_agent"])

    @patch("workflows.crawler_workflow.update_crawler_status")
    @patch("workflows.crawler_workflow.check_duplicate")
    @patch("workflows.crawler_workflow.evaluate_content")
    def test_routing_payload_persistence(self, mock_evaluate, mock_check_duplicate, mock_update_status):
        async def mock_evaluate_side_effect(title, content, source_url, target_keywords, config):
            score = float(title.split("_")[-1])
            return {
                "success": True,
                "material_score": score,
                "has_risk": False,
                "source_ok": True,
                "topic_hint": "some_topic",
                "reason": "mocked",
                "word_count": 100,
                "details": {},
            }

        async def mock_check_duplicate_side_effect(title, content, source_url, published_articles, threshold, algorithm, config):
            return {
                "success": True,
                "is_duplicate": False,
                "similarity_score": 0.0,
                "matched_article": None,
                "details": {},
            }

        async def mock_update_status_side_effect(config, record_id, new_status, error_message=None, routing_payload=None):
            return {"success": True}

        mock_evaluate.side_effect = mock_evaluate_side_effect
        mock_check_duplicate.side_effect = mock_check_duplicate_side_effect
        mock_update_status.side_effect = mock_update_status_side_effect

        async def run():
            from workflows.crawler_workflow import run_crawler_workflow
            items = [
                {"id": 101, "title": "score_85.0", "content": "content_test", "source_url": "https://example.com/101"},
                {"id": 102, "title": "score_35.0", "content": "content_test", "source_url": "https://example.com/102"},
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["EMBA"],
                dry_run=False,  # This triggers status update
                config=_config(),
                persist_run=False,
            )
            return out

        out = asyncio.run(run())

        # Verify mock_update_status was called twice
        self.assertEqual(mock_update_status.call_count, 2)

        calls = mock_update_status.call_args_list

        # First item (85.0 -> publish_candidate)
        args_1, kwargs_1 = calls[0]
        # Allow checking either args or kwargs
        self.assertEqual(kwargs_1.get("record_id") or args_1[1], 101)
        self.assertEqual(kwargs_1.get("new_status") or args_1[2], "pass_to_topic")
        payload_1 = kwargs_1.get("routing_payload")
        self.assertIsNotNone(payload_1)
        self.assertEqual(payload_1["material_score"], 85.0)
        self.assertEqual(payload_1["route_tier"], "publish_candidate")
        self.assertEqual(payload_1["rewrite_required"], False)
        self.assertEqual(payload_1["publish_candidate"], True)
        self.assertEqual(payload_1["topic_hint"], "some_topic")
        self.assertEqual(payload_1["source_title"], "score_85.0")
        self.assertEqual(payload_1["source_url"], "https://example.com/101")
        self.assertEqual(payload_1["source_summary"], "content_test")

        # Second item (35.0 -> discard)
        args_2, kwargs_2 = calls[1]
        self.assertEqual(kwargs_2.get("record_id") or args_2[1], 102)
        self.assertEqual(kwargs_2.get("new_status") or args_2[2], "discarded")
        payload_2 = kwargs_2.get("routing_payload")
        self.assertIsNotNone(payload_2)
        self.assertEqual(payload_2["material_score"], 35.0)
        self.assertIsNone(payload_2["route_tier"])
        self.assertEqual(payload_2["rewrite_required"], False)
        self.assertEqual(payload_2["publish_candidate"], False)
        self.assertEqual(payload_2["topic_hint"], "some_topic")
        self.assertEqual(payload_2["source_title"], "score_35.0")


if __name__ == "__main__":
    unittest.main()
