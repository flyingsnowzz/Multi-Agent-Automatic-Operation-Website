import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, create_engine, insert, select, update

from yaojiayk.workflows.scheduled_publish_workflow import run_scheduled_publish


class TestScheduledPublishWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "publish_tasks.db"
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
            "agent_name": "CMSAgent",
            "task_type": "publish",
            "input_data": {
                "article": {"title": "Test Title", "content_md": "Test Content"},
                "page_info": {"category": "test", "tags": ["t1"], "slug": "test-slug"},
                "images": {}
            },
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

    @patch("agents.cms_agent.cms_agent.CMSAgent.execute")
    def test_limit_fewer_pending_tasks(self, mock_execute):
        # 1. limit=10 时，pending 只有 3 条，只处理 3 条。
        mock_execute.return_value = {"status": "dry_run", "errors": []}
        
        self._insert_task(id="task_1")
        self._insert_task(id="task_2")
        self._insert_task(id="task_3")

        res = asyncio.run(run_scheduled_publish(limit=10, dry_run=True, db_url=self.db_url))
        
        self.assertEqual(res["picked"], 3)
        self.assertEqual(res["dry_run"], 3)
        self.assertEqual(res["published"], 0)
        self.assertEqual(mock_execute.call_count, 3)

    @patch("agents.cms_agent.cms_agent.CMSAgent.execute")
    def test_limit_more_pending_tasks(self, mock_execute):
        # 2. limit=10 时，pending 有 15 条，只处理 10 条，剩余 5 条保持 pending。
        mock_execute.return_value = {"status": "dry_run", "errors": []}
        
        for i in range(15):
            self._insert_task(id=f"task_{i}")

        res = asyncio.run(run_scheduled_publish(limit=10, dry_run=True, db_url=self.db_url))
        
        self.assertEqual(res["picked"], 10)
        self.assertEqual(res["dry_run"], 10)
        self.assertEqual(res["published"], 0)
        self.assertEqual(mock_execute.call_count, 10)
        
        # Verify 5 tasks remain pending and untouched (started_at is None)
        pending_count = 0
        for i in range(15):
            t = self._task(f"task_{i}")
            if t["status"] == "pending" and t["started_at"] is None:
                pending_count += 1
        self.assertEqual(pending_count, 5)

    @patch("agents.cms_agent.cms_agent.CMSAgent.execute")
    def test_dry_run_reverts_to_pending(self, mock_execute):
        # 3. dry_run 返回 dry_run 时，任务状态恢复为 pending，不计入 published，计入 dry_run。
        mock_execute.return_value = {"status": "dry_run", "errors": []}
        self._insert_task(id="task_1")

        res = asyncio.run(run_scheduled_publish(limit=1, dry_run=True, db_url=self.db_url))
        
        self.assertEqual(res["picked"], 1)
        self.assertEqual(res["dry_run"], 1)
        self.assertEqual(res["published"], 0)
        
        t1 = self._task("task_1")
        self.assertEqual(t1["status"], "pending")
        self.assertIsNone(t1["error_message"])
        self.assertEqual(t1["output_data"]["status"], "dry_run")
        self.assertIsNotNone(t1["completed_at"])

    @patch("agents.cms_agent.cms_agent.CMSAgent.execute")
    def test_real_publish_success_states(self, mock_execute):
        # 4. dry_run=False 且 CMSAgent 返回 published/draft/scheduled 时，任务状态变为 published。
        success_statuses = ["published", "draft", "scheduled"]
        for idx, status in enumerate(success_statuses):
            task_id = f"task_{idx}"
            self._insert_task(id=task_id)
            
            mock_execute.reset_mock()
            mock_execute.return_value = {"status": status, "errors": [], "article_id": f"cms_{idx}"}
            
            res = asyncio.run(run_scheduled_publish(limit=1, dry_run=False, db_url=self.db_url))
            self.assertEqual(res["picked"], 1)
            self.assertEqual(res["published"], 1)
            self.assertEqual(res["dry_run"], 0)
            
            t = self._task(task_id)
            self.assertEqual(t["status"], "published")
            self.assertIsNone(t["error_message"])

    @patch("agents.cms_agent.cms_agent.CMSAgent.execute")
    def test_publish_failure_states(self, mock_execute):
        # 5. CMSAgent 返回 failed/publish_blocked/retry_pending 或 errors 非空时，任务状态变为 publish_failed。
        failure_cases = [
            {"status": "failed", "errors": []},
            {"status": "publish_blocked", "errors": []},
            {"status": "retry_pending", "errors": []},
            {"status": "published", "errors": ["invalid_image"]}
        ]
        for idx, case in enumerate(failure_cases):
            task_id = f"task_{idx}"
            self._insert_task(id=task_id)
            
            mock_execute.reset_mock()
            mock_execute.return_value = case
            
            res = asyncio.run(run_scheduled_publish(limit=1, dry_run=True, db_url=self.db_url))
            self.assertEqual(res["picked"], 1)
            self.assertEqual(res["failed"], 1)
            self.assertEqual(res["published"], 0)
            self.assertEqual(res["dry_run"], 0)
            
            t = self._task(task_id)
            self.assertEqual(t["status"], "publish_failed")
            self.assertIsNotNone(t["error_message"])

    def test_atomic_claim_conflict_skipped(self):
        # 6. 原子领取冲突 rowcount == 0 时，不调用 CMSAgent.execute，计入 skipped。
        self._insert_task(id="task_conflict")
        
        original_update = update
        def mock_update(table):
            # Simulate that another process claimed the task right before this update runs
            with self.engine.begin() as conn:
                conn.execute(
                    original_update(table).where(table.c.id == "task_conflict").values(status="publishing")
                )
            return original_update(table)
            
        with patch("yaojiayk.workflows.scheduled_publish_workflow.update", side_effect=mock_update):
            with patch("agents.cms_agent.cms_agent.CMSAgent.execute") as mock_execute:
                res = asyncio.run(run_scheduled_publish(limit=1, dry_run=True, db_url=self.db_url))
                self.assertEqual(res["picked"], 0)
                self.assertEqual(res["skipped"], 1)
                self.assertEqual(res["task_ids"], [])
                mock_execute.assert_not_called()

    @patch("agents.cms_agent.cms_agent.CMSAgent.execute")
    def test_execute_call_count_limit(self, mock_execute):
        # 7. CMSAgent.execute 调用次数不超过 limit。
        mock_execute.return_value = {"status": "dry_run", "errors": []}
        for i in range(12):
            self._insert_task(id=f"task_{i}")
            
        res = asyncio.run(run_scheduled_publish(limit=5, dry_run=True, db_url=self.db_url))
        self.assertEqual(res["picked"], 5)
        self.assertEqual(mock_execute.call_count, 5)

    @patch("agents.cms_agent.cms_agent.CMSAgent.execute")
    def test_compatibility_with_flat_input_data(self, mock_execute):
        # 8. 兼容扁平 input_data。
        mock_execute.return_value = {"status": "dry_run", "errors": []}
        
        self._insert_task(
            id="task_flat",
            input_data={
                "title": "Flat Title Example",
                "source_content": "Flat body content example",
                "content_type": "article",
                "target_keywords": ["kw1", "kw2"],
                "secondary_keywords": ["sec1", "sec2"]
            }
        )

        async def inspect_execute(article, page_info, images=None):
            self.assertEqual(article["title"], "Flat Title Example")
            self.assertEqual(article["content_md"], "Flat body content example")
            self.assertEqual(article["content"], "Flat body content example")
            self.assertEqual(page_info["category"], "article")
            self.assertEqual(page_info["tags"], ["sec1", "sec2"])
            self.assertEqual(page_info["slug"], "")
            self.assertEqual(images, {})
            return {"status": "dry_run", "errors": []}

        mock_execute.side_effect = inspect_execute

        res = asyncio.run(run_scheduled_publish(limit=1, dry_run=True, db_url=self.db_url))
        self.assertEqual(res["picked"], 1)
        self.assertEqual(res["dry_run"], 1)


if __name__ == "__main__":
    unittest.main()
