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


if __name__ == "__main__":
    unittest.main()
