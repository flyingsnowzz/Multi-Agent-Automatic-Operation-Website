import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.prompt_db_logger import log_agent_prompt


class TestPromptAuditLogger(unittest.IsolatedAsyncioTestCase):
    async def test_log_agent_prompt_writes_jsonl_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {"PROMPT_AUDIT_LOG_DIR": tmp, "PROMPT_AUDIT_ENABLED": "true"},
                clear=False,
            ):
                await log_agent_prompt(
                    article_id=123,
                    stage="rewrite",
                    agent_name="WriterAgent",
                    prompt_type="rendered_writer_prompt",
                    prompt_text="write this article",
                    input_payload={"topic": "AI"},
                    output_payload={"title": "AI News"},
                    model_name="deepseek-chat",
                )

            files = list(Path(tmp).glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            line = files[0].read_text(encoding="utf-8").strip()
            payload = json.loads(line)
            self.assertEqual(payload["article_id"], 123)
            self.assertEqual(payload["stage"], "rewrite")
            self.assertEqual(payload["agent_name"], "WriterAgent")
            self.assertEqual(payload["prompt_text"], "write this article")
            self.assertEqual(payload["input_payload"], {"topic": "AI"})
            self.assertEqual(payload["output_payload"], {"title": "AI News"})

    async def test_log_agent_prompt_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {"PROMPT_AUDIT_LOG_DIR": tmp, "PROMPT_AUDIT_ENABLED": "false"},
                clear=False,
            ):
                await log_agent_prompt(stage="publish", agent_name="SEOAgent", prompt_text="hidden")

            self.assertEqual(list(Path(tmp).glob("*.jsonl")), [])

    async def test_log_agent_prompt_stringifies_non_json_values(self):
        class NonJson:
            pass

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ",
                {"PROMPT_AUDIT_LOG_DIR": tmp, "PROMPT_AUDIT_ENABLED": "true"},
                clear=False,
            ):
                await log_agent_prompt(
                    stage="publish",
                    agent_name="ImageAgent",
                    input_payload={"bad": NonJson()},
                )

            payload = json.loads(next(Path(tmp).glob("*.jsonl")).read_text(encoding="utf-8"))
            self.assertIn("NonJson", payload["input_payload"]["bad"])
