import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, create_engine, insert, select


class TestRewriteTaskPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "rewrite_tasks.db"
        self.db_url = f"sqlite:///{self.db_path}"
        self.engine = create_engine(self.db_url)
        metadata = MetaData()
        self.tasks = Table(
            "tasks",
            metadata,
            Column("id", String, primary_key=True),
            Column("workflow_id", String, nullable=True),
            Column("agent_name", String, nullable=False),
            Column("task_type", String, nullable=True),
            Column("input_data", JSON, nullable=True),
            Column("output_data", JSON, nullable=True),
            Column("status", String, nullable=True),
            Column("error_message", String, nullable=True),
            Column("retry_count", Integer, nullable=True, default=0),
            Column("started_at", DateTime, nullable=True),
            Column("completed_at", DateTime, nullable=True),
            Column("created_at", DateTime, nullable=True),
        )
        metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _insert_task(self, **values):
        defaults = {
            "workflow_id": None,
            "task_type": None,
            "input_data": None,
            "output_data": None,
            "status": "pending",
            "error_message": None,
            "retry_count": 0,
            "started_at": None,
            "completed_at": None,
            "created_at": None,
        }
        defaults.update(values)
        with self.engine.begin() as conn:
            conn.execute(insert(self.tasks).values(**defaults))

    def _task(self, task_id: str):
        with self.engine.begin() as conn:
            row = conn.execute(select(self.tasks).where(self.tasks.c.id == task_id)).mappings().first()
            return dict(row) if row else None

    def _tasks_by_agent(self, agent_name: str):
        with self.engine.begin() as conn:
            rows = conn.execute(select(self.tasks).where(self.tasks.c.agent_name == agent_name)).mappings().all()
            return [dict(row) for row in rows]

    def _insert_editor_flow_tasks(self, *, writer_task_id: str, editor_task_id: str, writer_output):
        self._insert_task(
            id=writer_task_id,
            agent_name="WriterAgent",
            task_type="rewrite_from_research",
            status="completed",
            input_data={
                "workflow_route": "full_rewrite_flow",
                "route_tier": "rewrite_candidate",
                "topic_id": "topic_123",
                "candidate_id": 123,
                "title": "EMBA报考条件详解",
                "primary_keyword": "EMBA报考条件",
                "secondary_keywords": ["EMBA申请流程"],
                "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
                "search_intent": "informational",
                "content_type": "guide",
                "content_angle": "conditions",
                "research_task_id": "research_task_ok",
            },
            output_data=writer_output,
        )
        self._insert_task(
            id=editor_task_id,
            agent_name="EditorAgent",
            task_type="edit_from_writer",
            input_data={
                "workflow_route": "full_rewrite_flow",
                "route_tier": "rewrite_candidate",
                "topic_id": "topic_123",
                "candidate_id": 123,
                "title": "EMBA报考条件详解",
                "primary_keyword": "EMBA报考条件",
                "secondary_keywords": ["EMBA申请流程"],
                "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
                "search_intent": "informational",
                "content_type": "guide",
                "content_angle": "conditions",
                "writer_task_id": writer_task_id,
            },
        )

    def test_run_research_task_persists_output_and_creates_writer_task(self):
        from workflows.rewrite_task_workflow import run_research_task

        research_task_id = "research_task_123"
        self._insert_task(
            id=research_task_id,
            agent_name="ResearchAgent",
            task_type="research_for_rewrite",
            input_data={
                "workflow_route": "full_rewrite_flow",
                "route_tier": "rewrite_candidate",
                "rewrite_required": True,
                "publish_candidate": False,
                "topic_id": "topic_123",
                "candidate_id": 123,
                "title": "EMBA报考条件详解",
                "primary_keyword": "EMBA报考条件",
                "secondary_keywords": ["EMBA申请流程"],
                "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
                "search_intent": "informational",
                "content_type": "guide",
                "content_angle": "conditions",
                "source_title": "EMBA 报考条件",
                "source_summary": "EMBA报考通常需要一定工作年限与管理经验。",
                "source_content": "EMBA报考通常需要一定工作年限与管理经验。申请流程包括材料准备、面试与时间安排。",
                "source_url": "https://example.com/source",
                "material_score": 65,
                "routing_payload": {},
            },
        )

        out = asyncio.run(run_research_task(task_id=research_task_id, db_url=self.db_url))

        self.assertEqual(out["research_task_id"], research_task_id)
        self.assertTrue(out["writer_task_id"])

        research_task = self._task(research_task_id)
        self.assertEqual(research_task["status"], "completed")
        self.assertIsNone(research_task["error_message"])
        self.assertIsInstance(research_task["output_data"], dict)
        self.assertIn("research_brief", research_task["output_data"])
        self.assertIn("outline", research_task["output_data"])

        writer_task = self._task(out["writer_task_id"])
        self.assertEqual(writer_task["agent_name"], "WriterAgent")
        self.assertEqual(writer_task["task_type"], "rewrite_from_research")
        self.assertEqual(writer_task["status"], "pending")
        self.assertEqual(writer_task["input_data"]["research_task_id"], research_task_id)
        self.assertIsNone(writer_task["input_data"]["research_brief_id"])
        self.assertEqual(writer_task["input_data"]["topic_id"], "topic_123")

    def test_run_writer_task_reads_research_from_db_and_persists_result(self):
        from workflows.rewrite_task_workflow import run_writer_task

        research_task_id = "research_task_ok"
        writer_task_id = "writer_task_ok"
        research_output = {
            "research_brief": {
                "brief_type": "rewrite_candidate_research_brief",
                "writer_outline": {
                    "title": "EMBA报考条件详解",
                    "sections": [{"title": "核心条件", "key_points": ["工作经验"], "notes": "rule"}],
                },
            },
            "outline": {"sections": [{"title": "核心条件", "key_points": ["工作经验"], "notes": "rule"}]},
            "sources": [{"title": "EMBA 报考条件", "url": "https://example.com/source"}],
            "citations": [{"title": "EMBA 报考条件", "url": "https://example.com/source", "source": "crawler_candidate", "authority": "medium", "citation": "EMBA 报考条件", "note": "rewrite_candidate_source"}],
        }
        self._insert_task(
            id=research_task_id,
            agent_name="ResearchAgent",
            task_type="research_for_rewrite",
            status="completed",
            input_data={"topic_id": "topic_123"},
            output_data=research_output,
        )
        self._insert_task(
            id=writer_task_id,
            agent_name="WriterAgent",
            task_type="rewrite_from_research",
            input_data={
                "workflow_route": "full_rewrite_flow",
                "route_tier": "rewrite_candidate",
                "topic_id": "topic_123",
                "candidate_id": 123,
                "title": "EMBA报考条件详解",
                "primary_keyword": "EMBA报考条件",
                "secondary_keywords": ["EMBA申请流程"],
                "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
                "search_intent": "informational",
                "content_type": "guide",
                "content_angle": "conditions",
                "research_task_id": research_task_id,
                "research_brief_id": None,
            },
        )

        expected = {
            "article": {"title": "EMBA报考条件详解", "content_md": "# EMBA", "meta_description": "desc"},
            "statistics": {"word_count": 10, "reading_time_minutes": 1},
            "quality_checks": {},
            "warnings": [],
        }

        async def _fake_execute(*, topic, outline=None, materials=None, brand_config=None, dry_run=False, mode=None):
            self.assertEqual(topic["topic_id"], "topic_123")
            self.assertEqual(topic["primary_keyword"], "EMBA报考条件")
            self.assertEqual(outline, research_output["outline"])
            self.assertEqual(materials["research_brief"]["brief_type"], "rewrite_candidate_research_brief")
            self.assertTrue(dry_run)
            return expected

        with patch("agents.writer_agent.writer_agent.WriterAgent.execute", new=AsyncMock(side_effect=_fake_execute)):
            out = asyncio.run(run_writer_task(task_id=writer_task_id, db_url=self.db_url, dry_run=True))

        self.assertEqual(out["status"], "completed")
        self.assertTrue(out["editor_task_id"])
        writer_task = self._task(writer_task_id)
        self.assertEqual(writer_task["status"], "completed")
        self.assertIsNone(writer_task["error_message"])
        self.assertEqual(writer_task["output_data"]["article"]["title"], "EMBA报考条件详解")
        editor_task = self._task(out["editor_task_id"])
        self.assertEqual(editor_task["agent_name"], "EditorAgent")
        self.assertEqual(editor_task["task_type"], "edit_from_writer")
        self.assertEqual(editor_task["status"], "pending")
        self.assertEqual(editor_task["input_data"]["writer_task_id"], writer_task_id)
        self.assertNotIn("article", editor_task["input_data"])
        self.assertNotIn("content_md", editor_task["input_data"])

    def test_run_writer_task_blocks_when_research_brief_missing(self):
        from workflows.rewrite_task_workflow import run_writer_task

        research_task_id = "research_task_missing"
        writer_task_id = "writer_task_blocked"
        self._insert_task(
            id=research_task_id,
            agent_name="ResearchAgent",
            task_type="research_for_rewrite",
            status="completed",
            input_data={"topic_id": "topic_123"},
            output_data={"outline": {"sections": []}},
        )
        self._insert_task(
            id=writer_task_id,
            agent_name="WriterAgent",
            task_type="rewrite_from_research",
            input_data={
                "workflow_route": "full_rewrite_flow",
                "route_tier": "rewrite_candidate",
                "topic_id": "topic_123",
                "title": "EMBA报考条件详解",
                "primary_keyword": "EMBA报考条件",
                "secondary_keywords": [],
                "target_keywords": ["EMBA报考条件"],
                "search_intent": "informational",
                "content_type": "guide",
                "content_angle": "conditions",
                "research_task_id": research_task_id,
                "research_brief_id": None,
            },
        )

        out = asyncio.run(run_writer_task(task_id=writer_task_id, db_url=self.db_url, dry_run=True))

        self.assertEqual(out["status"], "writing_blocked")
        self.assertEqual(out["error_message"], "missing_research_brief")
        writer_task = self._task(writer_task_id)
        self.assertEqual(writer_task["status"], "writing_blocked")
        self.assertEqual(writer_task["error_message"], "missing_research_brief")
        self.assertEqual(writer_task["output_data"]["block_reason"], "missing_research_brief")
        self.assertEqual(writer_task["output_data"]["research_task_id"], research_task_id)

    def test_run_editor_task_approved_sets_editor_approved_without_creating_seo_task(self):
        from workflows.rewrite_task_workflow import run_editor_task

        writer_task_id = "writer_task_done"
        editor_task_id = "editor_task_ok"
        writer_output = {
            "article": {
                "title": "EMBA报考条件详解",
                "content_md": "# EMBA报考条件详解\n\n正文。\n",
                "meta_description": "desc",
            },
            "statistics": {"word_count": 10, "reading_time_minutes": 1},
            "quality_checks": {},
            "warnings": [],
        }
        self._insert_editor_flow_tasks(
            writer_task_id=writer_task_id,
            editor_task_id=editor_task_id,
            writer_output=writer_output,
        )

        expected = {
            "success": True,
            "article": writer_output["article"],
            "quality_score": {"overall": 88.0, "dimensions": {}},
            "issues_found": [],
            "polishing_notes": [],
            "approval_status": "approved",
            "tool_results": {},
            "auto_fix": {"applied_patches": []},
            "llm": {"used": False},
            "reviewed_article": {
                "title": writer_output["article"]["title"],
                "content": writer_output["article"]["content_md"],
                "meta_description": writer_output["article"]["meta_description"],
            },
            "revised_article": {"content_md": writer_output["article"]["content_md"]},
        }

        async def _fake_execute(*, article, topic=None, dry_run=True):
            self.assertEqual(article["title"], "EMBA报考条件详解")
            self.assertEqual(topic["topic_id"], "topic_123")
            self.assertTrue(dry_run)
            return expected

        with patch("agents.editor_agent.editor_agent.EditorAgent.execute", new=AsyncMock(side_effect=_fake_execute)):
            out = asyncio.run(run_editor_task(task_id=editor_task_id, db_url=self.db_url, dry_run=True))

        self.assertEqual(out["status"], "editor_approved")
        self.assertEqual(out["next_action"], "ready_for_seo")
        self.assertIsNone(out["next_task_id"])
        editor_task = self._task(editor_task_id)
        self.assertEqual(editor_task["status"], "editor_approved")
        self.assertIsNone(editor_task["error_message"])
        self.assertEqual(editor_task["output_data"]["approval_status"], "approved")
        self.assertEqual(editor_task["output_data"]["quality_score"], expected["quality_score"])
        self.assertEqual(editor_task["output_data"]["issues_found"], [])
        self.assertEqual(editor_task["output_data"]["next_action"], "ready_for_seo")
        self.assertIsNone(editor_task["output_data"]["next_task_id"])
        self.assertIn("generated_at", editor_task["output_data"])
        self.assertEqual(len(self._tasks_by_agent("SEOAgent")), 0)

    def test_run_editor_task_conditional_sets_editor_conditional(self):
        from workflows.rewrite_task_workflow import run_editor_task

        writer_task_id = "writer_task_conditional"
        editor_task_id = "editor_task_conditional"
        writer_output = {
            "article": {
                "title": "EMBA报考条件详解",
                "content_md": "# EMBA报考条件详解\n\n正文。\n",
                "meta_description": "desc",
            }
        }
        self._insert_editor_flow_tasks(
            writer_task_id=writer_task_id,
            editor_task_id=editor_task_id,
            writer_output=writer_output,
        )
        expected = {
            "success": True,
            "article": writer_output["article"],
            "quality_score": {"overall": 76.0, "dimensions": {}},
            "issues_found": [{"severity": "medium", "message": "needs polish"}],
            "approval_status": "conditional",
            "reviewed_article": {"title": writer_output["article"]["title"]},
        }

        with patch("agents.editor_agent.editor_agent.EditorAgent.execute", new=AsyncMock(return_value=expected)):
            out = asyncio.run(run_editor_task(task_id=editor_task_id, db_url=self.db_url, dry_run=True))

        self.assertEqual(out["status"], "editor_conditional")
        self.assertEqual(out["next_action"], "manual_review_or_revision")
        self.assertIsNone(out["next_task_id"])
        editor_task = self._task(editor_task_id)
        self.assertEqual(editor_task["status"], "editor_conditional")
        self.assertEqual(editor_task["error_message"], "editor_conditional")
        self.assertEqual(editor_task["output_data"]["approval_status"], "conditional")
        self.assertEqual(editor_task["output_data"]["quality_score"], expected["quality_score"])
        self.assertEqual(editor_task["output_data"]["issues_found"], expected["issues_found"])
        self.assertEqual(editor_task["output_data"]["next_action"], "manual_review_or_revision")
        self.assertIsNone(editor_task["output_data"]["next_task_id"])
        self.assertEqual(editor_task["output_data"]["block_reason"], "editor_conditional")

    def test_run_editor_task_rejected_sets_editor_rejected_without_creating_writer_retry(self):
        from workflows.rewrite_task_workflow import run_editor_task

        writer_task_id = "writer_task_rejected"
        editor_task_id = "editor_task_rejected"
        writer_output = {
            "article": {
                "title": "EMBA报考条件详解",
                "content_md": "# EMBA报考条件详解\n\n正文。\n",
                "meta_description": "desc",
            }
        }
        self._insert_editor_flow_tasks(
            writer_task_id=writer_task_id,
            editor_task_id=editor_task_id,
            writer_output=writer_output,
        )
        expected = {
            "success": True,
            "article": writer_output["article"],
            "quality_score": {"overall": 48.0, "dimensions": {}},
            "issues_found": [{"severity": "high", "message": "rewrite required"}],
            "approval_status": "rejected",
            "revised_article": {"content_md": writer_output["article"]["content_md"]},
        }

        with patch("agents.editor_agent.editor_agent.EditorAgent.execute", new=AsyncMock(return_value=expected)):
            out = asyncio.run(run_editor_task(task_id=editor_task_id, db_url=self.db_url, dry_run=True))

        self.assertEqual(out["status"], "editor_rejected")
        self.assertEqual(out["next_action"], "rewrite_required")
        self.assertIsNone(out["next_task_id"])
        editor_task = self._task(editor_task_id)
        self.assertEqual(editor_task["status"], "editor_rejected")
        self.assertEqual(editor_task["error_message"], "editor_rejected")
        self.assertEqual(editor_task["output_data"]["approval_status"], "rejected")
        self.assertEqual(editor_task["output_data"]["quality_score"], expected["quality_score"])
        self.assertEqual(editor_task["output_data"]["issues_found"], expected["issues_found"])
        self.assertEqual(editor_task["output_data"]["next_action"], "rewrite_required")
        self.assertIsNone(editor_task["output_data"]["next_task_id"])
        self.assertEqual(editor_task["output_data"]["block_reason"], "editor_rejected")
        self.assertEqual(len(self._tasks_by_agent("WriterAgent")), 1)

    def test_run_editor_task_marks_editor_failed_when_execute_raises(self):
        from workflows.rewrite_task_workflow import run_editor_task

        writer_task_id = "writer_task_error"
        editor_task_id = "editor_task_error"
        writer_output = {
            "article": {
                "title": "EMBA报考条件详解",
                "content_md": "# EMBA报考条件详解\n\n正文。\n",
                "meta_description": "desc",
            }
        }
        self._insert_editor_flow_tasks(
            writer_task_id=writer_task_id,
            editor_task_id=editor_task_id,
            writer_output=writer_output,
        )

        with patch(
            "agents.editor_agent.editor_agent.EditorAgent.execute",
            new=AsyncMock(side_effect=RuntimeError("editor exploded")),
        ):
            with self.assertRaisesRegex(RuntimeError, "editor exploded"):
                asyncio.run(run_editor_task(task_id=editor_task_id, db_url=self.db_url, dry_run=True))

        editor_task = self._task(editor_task_id)
        self.assertEqual(editor_task["status"], "editor_failed")
        self.assertEqual(editor_task["error_message"], "editor exploded")
        self.assertEqual(editor_task["output_data"]["block_reason"], "editor_failed")
        self.assertEqual(editor_task["output_data"]["error"], "editor exploded")
        self.assertEqual(editor_task["output_data"]["next_action"], "fix_editor_error")
        self.assertIsNone(editor_task["output_data"]["next_task_id"])
        self.assertIn("generated_at", editor_task["output_data"])

    def test_run_editor_task_blocks_when_writer_output_missing(self):
        from workflows.rewrite_task_workflow import run_editor_task

        writer_task_id = "writer_task_missing"
        editor_task_id = "editor_task_blocked"
        self._insert_task(
            id=writer_task_id,
            agent_name="WriterAgent",
            task_type="rewrite_from_research",
            status="completed",
            input_data={"topic_id": "topic_123"},
            output_data={"statistics": {"word_count": 0}},
        )
        self._insert_task(
            id=editor_task_id,
            agent_name="EditorAgent",
            task_type="edit_from_writer",
            input_data={
                "workflow_route": "full_rewrite_flow",
                "route_tier": "rewrite_candidate",
                "topic_id": "topic_123",
                "title": "EMBA报考条件详解",
                "primary_keyword": "EMBA报考条件",
                "secondary_keywords": [],
                "target_keywords": ["EMBA报考条件"],
                "search_intent": "informational",
                "content_type": "guide",
                "content_angle": "conditions",
                "writer_task_id": writer_task_id,
            },
        )

        out = asyncio.run(run_editor_task(task_id=editor_task_id, db_url=self.db_url, dry_run=True))

        self.assertEqual(out["status"], "editing_blocked")
        self.assertEqual(out["error_message"], "missing_writer_output")
        self.assertIsNone(out["next_action"])
        self.assertIsNone(out["next_task_id"])
        editor_task = self._task(editor_task_id)
        self.assertEqual(editor_task["status"], "editing_blocked")
        self.assertEqual(editor_task["error_message"], "missing_writer_output")
        self.assertEqual(editor_task["output_data"]["block_reason"], "missing_writer_output")
        self.assertEqual(editor_task["output_data"]["writer_task_id"], writer_task_id)

    def test_editor_input_contract_uses_writer_task_id_without_copying_article(self):
        from workflows.rewrite_task_workflow import _editor_input_from_writer_task

        payload = _editor_input_from_writer_task(
            {
                "id": "writer_task_contract",
                "input_data": {
                    "workflow_route": "full_rewrite_flow",
                    "route_tier": "rewrite_candidate",
                    "topic_id": "topic_123",
                    "candidate_id": 123,
                    "title": "EMBA报考条件详解",
                    "primary_keyword": "EMBA报考条件",
                    "secondary_keywords": ["EMBA申请流程"],
                    "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
                    "search_intent": "informational",
                    "content_type": "guide",
                    "content_angle": "conditions",
                    "article": {
                        "title": "不应复制",
                        "content_md": "# 不应复制",
                    },
                    "content_md": "# 不应复制",
                },
            }
        )

        self.assertEqual(payload["writer_task_id"], "writer_task_contract")
        self.assertEqual(payload["topic_id"], "topic_123")
        self.assertEqual(payload["primary_keyword"], "EMBA报考条件")
        self.assertNotIn("article", payload)
        self.assertNotIn("content_md", payload)


if __name__ == "__main__":
    unittest.main()
