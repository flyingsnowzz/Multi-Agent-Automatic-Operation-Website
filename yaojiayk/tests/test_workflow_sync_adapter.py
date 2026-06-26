import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestWorkflowSyncAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_hybrid_sync_adapter_rejects_running_loop(self):
        from workflows.hybrid_workflow import _run_async_sync

        async def _sample():
            return {"ok": True}

        with self.assertRaisesRegex(
            RuntimeError,
            r"running_event_loop_not_supported_for_sync_workflow stage=edit input_id=topic-1 trace_id=trace123",
        ):
            _run_async_sync(_sample(), stage="edit", state={"topic": {"id": "topic-1"}, "trace_id": "trace123"})

    async def test_langgraph_sync_adapter_rejects_running_loop(self):
        with patch("workflows.langgraph_workflow.ChatOpenAI", return_value=SimpleNamespace()):
            from workflows.langgraph_workflow import MultiAgentWorkflow

            wf = MultiAgentWorkflow(config_dir="agents")

        async def _sample():
            return {"ok": True}

        with self.assertRaisesRegex(
            RuntimeError,
            r"running_event_loop_not_supported_for_sync_workflow stage=write input_id=topic-2 trace_id=trace456",
        ):
            wf._run_async_sync(_sample(), stage="write", state={"topic": {"id": "topic-2"}, "trace_id": "trace456"})


if __name__ == "__main__":
    unittest.main()
