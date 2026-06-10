import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import workflows.hybrid_workflow as hw
from workflows.crawler_workflow import run_crawler_workflow
from workflows.run_artifacts import write_run_artifacts


class FakeCompiledWorkflow:
    def invoke(self, state):
        state["current_stage"] = "evolve"
        state["cms_result"] = {"dry_run": True}
        return state


class TestWorkflowRunArtifacts(unittest.TestCase):
    def test_write_run_artifacts_creates_expected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = write_run_artifacts(
                workflow="hybrid",
                run_id="abc123",
                input_payload={"topic": "t"},
                result_payload={"status": "success"},
                error_payload=None,
                runs_root=tmp,
            )

            self.assertEqual(run_dir, Path(tmp) / "hybrid" / "abc123")
            self.assertTrue((run_dir / "input.json").is_file())
            self.assertTrue((run_dir / "result.json").is_file())
            self.assertTrue((run_dir / "error.json").is_file())
            self.assertEqual(json.loads((run_dir / "error.json").read_text(encoding="utf-8")), {"error": None})

    def test_hybrid_run_persists_input_result_and_error(self):
        old_trace_id = hw._trace_id
        hw._trace_id = lambda: "hybridtest123"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workflow = hw.HybridWorkflow()
                workflow.compiled = FakeCompiledWorkflow()

                out = workflow.run(
                    {
                        "title": "T",
                        "primary_keyword": "k",
                        "secondary_keywords": [],
                        "content_type": "guide",
                    },
                    runs_root=tmp,
                )

                run_dir = Path(out["artifact_dir"])
                self.assertEqual(run_dir, Path(tmp) / "hybrid" / "hybridtest123")
                self.assertEqual(json.loads((run_dir / "input.json").read_text(encoding="utf-8"))["topic"]["title"], "T")
                self.assertEqual(json.loads((run_dir / "result.json").read_text(encoding="utf-8"))["run_id"], "hybridtest123")
                self.assertEqual(json.loads((run_dir / "error.json").read_text(encoding="utf-8")), {"error": None})
        finally:
            hw._trace_id = old_trace_id

    def test_crawler_run_persists_input_result_and_error(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                out = await run_crawler_workflow(
                    items=[
                        {
                            "id": 1,
                            "title": "k test",
                            "content": ("k " * 700) + "\n\n" * 5 + "http://a.com",
                            "source_url": "https://example.com/a",
                        }
                    ],
                    published_articles=[],
                    target_keywords=["k"],
                    dry_run=True,
                    config={
                        "execution": {"auto_publish_threshold": 0.8, "rewrite_threshold": 0.5, "llm_decision_enabled": False},
                        "crawler_db": {"ready_to_publish_status": "ready_to_publish", "ready_to_rewrite_status": "ready_to_rewrite", "discard_status": "discarded"},
                        "dedup": {"threshold": 0.8, "algorithm": "cosine", "action_on_duplicate": "discard"},
                        "evaluation_criteria": {
                            "min_quality_score": 0.5,
                            "min_relevance_score": 0.4,
                            "min_seo_potential_score": 0.4,
                            "min_word_count": 80,
                            "max_word_count": 5000,
                            "required_fields": ["title", "content", "source_url"],
                        },
                    },
                    runs_root=tmp,
                )

                run_dir = Path(out["artifact_dir"])
                self.assertEqual(run_dir.parent, Path(tmp) / "crawler")
                self.assertTrue((run_dir / "input.json").is_file())
                self.assertEqual(json.loads((run_dir / "input.json").read_text(encoding="utf-8"))["target_keywords"], ["k"])
                self.assertEqual(json.loads((run_dir / "result.json").read_text(encoding="utf-8"))["workflow"], "crawler")
                self.assertEqual(json.loads((run_dir / "error.json").read_text(encoding="utf-8")), {"error": None})

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
