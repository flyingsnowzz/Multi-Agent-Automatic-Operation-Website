import asyncio
import unittest
from unittest.mock import patch


def _config():
    return {
        "crawler_db": {
            "pass_to_scoring_status": "pass_to_scoring",
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
    def test_gate_result_and_duplicate_control_routing(self, mock_evaluate, mock_check_duplicate):
        async def mock_evaluate_side_effect(title, content, source_url, target_keywords, config):
            import re
            match = re.search(r"score_([\d\.]+)", title)
            score = float(match.group(1)) if match else 0.0
            source_ok = "badsource" not in title
            content_complete = "incomplete" not in title
            gate_failures = []
            if not source_ok:
                gate_failures.append("invalid_source")
            if not content_complete:
                gate_failures.append("content_incomplete")
            gate_passed = not gate_failures
            return {
                "success": True,
                "material_score": score,
                "quality_score": 0.8,
                "relevance_score": 0.8,
                "seo_potential_score": 0.8,
                "source_ok": source_ok,
                "content_complete": content_complete,
                "noise_ratio": 0.1,
                "topic_hint": "" if "notopic" in title else "some_topic",
                "reason": "mocked",
                "base_relevance_score": 0.8,
                "base_usability_score": 0.8,
                "gate_passed": gate_passed,
                "gate_result": "pass_to_scoring" if gate_passed else "discard",
                "word_count": 100,
                "details": {"gate_failures": gate_failures},
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
                {"id": 1, "title": "score_40.0", "content": "content", "source_url": "https://example.com/1"},
                {"id": 2, "title": "score_81.0", "content": "content", "source_url": "https://example.com/2"},
                {"id": 3, "title": "score_85.0_duplicate", "content": "content", "source_url": "https://example.com/3"},
                {"id": 4, "title": "score_85.0_badsource", "content": "content", "source_url": "https://example.com/4"},
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

        self.assertEqual(processed_items[0]["decision"], "pass_to_scoring")
        self.assertEqual(processed_items[0]["status_to_update"], "pass_to_scoring")
        self.assertEqual(processed_items[0]["next_agent"], "ScoringAgent")
        self.assertEqual(processed_items[0]["next_payload"]["gate_result"], "pass_to_scoring")
        self.assertEqual(processed_items[0]["next_payload"]["material_score"], 40.0)

        self.assertEqual(processed_items[1]["decision"], "pass_to_scoring")
        self.assertEqual(processed_items[1]["status_to_update"], "pass_to_scoring")
        self.assertEqual(processed_items[1]["next_agent"], "ScoringAgent")
        self.assertEqual(processed_items[1]["next_payload"]["material_score"], 81.0)

        self.assertEqual(processed_items[2]["decision"], "discard")
        self.assertEqual(processed_items[2]["status_to_update"], "discarded")
        self.assertIsNone(processed_items[2]["next_agent"])

        self.assertEqual(processed_items[3]["decision"], "discard")
        self.assertEqual(processed_items[3]["status_to_update"], "discarded")
        self.assertIsNone(processed_items[3]["next_agent"])

    @patch("workflows.crawler_workflow.update_crawler_status")
    @patch("workflows.crawler_workflow.check_duplicate")
    @patch("workflows.crawler_workflow.evaluate_content")
    def test_legacy_status_fallback_keeps_topic_status_for_db_write(self, mock_evaluate, mock_check_duplicate, mock_update_status):
        async def mock_evaluate_side_effect(title, content, source_url, target_keywords, config):
            score = float(title.split("_")[-1])
            return {
                "success": True,
                "material_score": score,
                "quality_score": 0.8,
                "relevance_score": 0.8,
                "seo_potential_score": 0.8,
                "source_ok": True,
                "content_complete": True,
                "noise_ratio": 0.1,
                "topic_hint": "some_topic",
                "reason": "mocked",
                "base_relevance_score": 0.8,
                "base_usability_score": 0.8,
                "gate_passed": True,
                "gate_result": "pass_to_scoring",
                "word_count": 100,
                "details": {"gate_failures": []},
            }

        async def mock_check_duplicate_side_effect(title, content, source_url, published_articles, threshold, algorithm, config):
            return {
                "success": True,
                "is_duplicate": False,
                "similarity_score": 0.0,
                "matched_article": None,
                "details": {},
            }

        async def mock_update_status_side_effect(config, record_id, new_status, error_message=None):
            return {"success": True}

        mock_evaluate.side_effect = mock_evaluate_side_effect
        mock_check_duplicate.side_effect = mock_check_duplicate_side_effect
        mock_update_status.side_effect = mock_update_status_side_effect

        async def run():
            from workflows.crawler_workflow import run_crawler_workflow
            items = [
                {"id": 101, "title": "score_85.0", "content": "content_test", "source_url": "https://example.com/101"},
            ]
            out = await run_crawler_workflow(
                items=items,
                published_articles=[],
                target_keywords=["EMBA"],
                dry_run=False,  # This triggers status update
                config={
                    "crawler_db": {"pass_to_topic_status": "pass_to_topic", "discard_status": "discarded"},
                    "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                    "evaluation_criteria": {"min_word_count": 80, "max_word_count": 5000},
                },
                persist_run=False,
            )
            return out

        out = asyncio.run(run())

        self.assertEqual(mock_update_status.call_count, 1)
        self.assertEqual(out["items"][0]["decision"], "pass_to_scoring")
        self.assertEqual(out["items"][0]["status_to_update"], "pass_to_topic")
        self.assertEqual(out["items"][0]["next_agent"], "ScoringAgent")

        args_1, kwargs_1 = mock_update_status.call_args_list[0]
        self.assertEqual(kwargs_1.get("record_id") or args_1[1], 101)
        self.assertEqual(kwargs_1.get("new_status") or args_1[2], "pass_to_topic")


if __name__ == "__main__":
    unittest.main()
