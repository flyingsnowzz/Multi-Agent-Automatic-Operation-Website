import asyncio
import unittest

from workflows.crawler_workflow import run_crawler_workflow


def _config():
    return {
        "execution": {"auto_publish_threshold": 90, "rewrite_threshold": 40, "llm_decision_enabled": False},
        "crawler_db": {
            "ready_to_publish_status": "ready_to_publish",
            "ready_to_rewrite_status": "ready_to_rewrite",
            "discard_status": "discarded",
        },
        "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
        "evaluation_criteria": {
            "min_quality_score": 40,
            "min_relevance_score": 40,
            "min_seo_potential_score": 40,
            "min_word_count": 80,
            "max_word_count": 5000,
            "required_fields": ["title", "content", "source_url"],
        },
    }


class TestCrawlerWorkflowScoreRouting(unittest.TestCase):
    def test_score_boundaries_route_on_zero_to_hundred_scale(self):
        async def run():
            content = ("EMBA " * 120) + "\n\n" + "https://example.com"
            scores = [100, 99, 90, 89.99, 89, 40, 39.99, 39, 0]
            out = await run_crawler_workflow(
                items=[
                    {
                        "id": idx + 1,
                        "title": f"EMBA article {score}",
                        "content": content,
                        "source_url": f"https://example.com/{idx}",
                        "score": score,
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
            ["publish", "publish", "publish", "rewrite", "rewrite", "rewrite", "discard", "discard", "discard"],
        )
        statuses = [item["status_to_update"] for item in out["items"]]
        self.assertEqual(
            statuses,
            [
                "ready_to_publish",
                "ready_to_publish",
                "ready_to_publish",
                "ready_to_rewrite",
                "ready_to_rewrite",
                "ready_to_rewrite",
                "discarded",
                "discarded",
                "discarded",
            ],
        )
        reasons = [item.get("reason") for item in out["items"]]
        self.assertEqual(
            reasons,
            [
                "score_gte_90_publish",
                "score_gte_90_publish",
                "score_gte_90_publish",
                "score_between_40_and_89_rewrite",
                "score_between_40_and_89_rewrite",
                "score_between_40_and_89_rewrite",
                "score_lt_40_discard",
                "score_lt_40_discard",
                "score_lt_40_discard",
            ],
        )

    def test_invalid_or_missing_item_score_uses_content_evaluator_score(self):
        old = run_crawler_workflow.__globals__["evaluate_content"]

        async def fake_evaluate_content(**_):
            return {
                "success": True,
                "quality_score": 50,
                "relevance_score": 80,
                "seo_potential_score": 70,
                "word_count": 800,
                "readability_score": 80,
                "has_copyright_risk": False,
                "details": {},
            }

        run_crawler_workflow.__globals__["evaluate_content"] = fake_evaluate_content

        async def run():
            content = ("EMBA " * 120) + "\n\n" + "https://example.com"
            items = [
                {"id": 1, "title": "a", "content": content, "source_url": "https://example.com/1", "score": -1},
                {"id": 2, "title": "b", "content": content, "source_url": "https://example.com/2", "score": 101},
                {"id": 3, "title": "c", "content": content, "source_url": "https://example.com/3", "score": "abc"},
                {"id": 4, "title": "d", "content": content, "source_url": "https://example.com/4", "score": None},
                {"id": 5, "title": "e", "content": content, "source_url": "https://example.com/5"},
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

        try:
            out = asyncio.run(run())
        finally:
            run_crawler_workflow.__globals__["evaluate_content"] = old

        for processed in out["items"]:
            evaluation = processed.get("evaluation") or {}
            self.assertEqual(processed.get("decision"), "rewrite")
            self.assertEqual(evaluation.get("quality_score"), 50)
            self.assertEqual(evaluation.get("score_source"), "content_evaluator")
            self.assertEqual(processed.get("reason"), "score_between_40_and_89_rewrite")

        self.assertIn("invalid_score_ignored", (out["items"][0].get("evaluation") or {}).get("warnings") or [])
        self.assertIn("invalid_score_ignored", (out["items"][1].get("evaluation") or {}).get("warnings") or [])
        self.assertIn("invalid_score_ignored", (out["items"][2].get("evaluation") or {}).get("warnings") or [])
        self.assertIn("invalid_score_ignored", (out["items"][3].get("evaluation") or {}).get("warnings") or [])
        self.assertFalse(bool((out["items"][4].get("evaluation") or {}).get("warnings")))

    def test_valid_item_score_overrides_content_evaluator(self):
        old = run_crawler_workflow.__globals__["evaluate_content"]

        async def fake_evaluate_content(**_):
            return {
                "success": True,
                "quality_score": 50,
                "relevance_score": 80,
                "seo_potential_score": 70,
                "word_count": 800,
                "readability_score": 80,
                "has_copyright_risk": False,
                "details": {},
            }

        run_crawler_workflow.__globals__["evaluate_content"] = fake_evaluate_content

        async def run():
            content = ("EMBA " * 120) + "\n\n" + "https://example.com"
            scores = [0, 40, 90, 100]
            out = await run_crawler_workflow(
                items=[
                    {
                        "id": idx + 1,
                        "title": f"t{score}",
                        "content": content,
                        "source_url": f"https://example.com/{idx}",
                        "score": score,
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

        try:
            out = asyncio.run(run())
        finally:
            run_crawler_workflow.__globals__["evaluate_content"] = old

        decisions = [item["decision"] for item in out["items"]]
        self.assertEqual(decisions, ["discard", "rewrite", "publish", "publish"])

        for processed in out["items"]:
            evaluation = processed.get("evaluation") or {}
            self.assertEqual(evaluation.get("score_source"), "item.score")

        reasons = [item.get("reason") for item in out["items"]]
        self.assertEqual(
            reasons,
            [
                "score_lt_40_discard",
                "score_between_40_and_89_rewrite",
                "score_gte_90_publish",
                "score_gte_90_publish",
            ],
        )

        rewrite_payload = out["items"][1].get("next_payload") or {}
        self.assertEqual(rewrite_payload.get("rewrite_goal"), "提升到90分以上")
        self.assertTrue(isinstance(rewrite_payload.get("avoid"), list) and rewrite_payload.get("avoid"))
        self.assertEqual(rewrite_payload.get("target_keywords"), ["EMBA"])
        self.assertTrue(rewrite_payload.get("original_content"))
        self.assertEqual((rewrite_payload.get("meta") or {}).get("crawler_record_id"), 2)

    def test_scoring_failed_discards_and_sets_reason(self):
        old = run_crawler_workflow.__globals__["evaluate_content"]

        async def fake_evaluate_content(**_):
            return {"success": False, "error": "x"}

        run_crawler_workflow.__globals__["evaluate_content"] = fake_evaluate_content

        async def run():
            content = ("EMBA " * 120) + "\n\n" + "https://example.com"
            out = await run_crawler_workflow(
                items=[
                    {
                        "id": 1,
                        "title": "t",
                        "content": content,
                        "source_url": "https://example.com/1",
                        "score": 90,
                    }
                ],
                published_articles=[],
                target_keywords=["EMBA"],
                dry_run=True,
                config=_config(),
                persist_run=False,
            )
            return out

        try:
            out = asyncio.run(run())
        finally:
            run_crawler_workflow.__globals__["evaluate_content"] = old

        self.assertTrue(out.get("items"))
        item = out["items"][0]
        self.assertEqual(item.get("decision"), "discard")
        self.assertIsNone(item.get("next_payload"))
        self.assertEqual((item.get("evaluation") or {}).get("score_source"), "content_evaluator")
        self.assertEqual(item.get("reason"), "scoring_failed")

    def test_crawler_workflow_has_no_topicagent_or_hybrid_references(self):
        import os

        here = os.path.dirname(__file__)
        root = os.path.abspath(os.path.join(here, ".."))
        p = os.path.join(root, "workflows", "crawler_workflow.py")
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("agents.topic_agent", text)
        self.assertNotIn("TopicAgent", text)
        self.assertNotIn("HybridWorkflow", text)
        self.assertNotIn("workflows.topic_to_hybrid_adapter", text)


if __name__ == "__main__":
    unittest.main()
