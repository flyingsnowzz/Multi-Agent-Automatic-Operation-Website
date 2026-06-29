"""Emit a complete LangGraph workflow_event log sequence.

This smoke script verifies that the global logging setup writes workflow
stage events to both stdout and logs/app.log without invoking LLMs or CMS APIs.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging
from yaojiayk.workflows.langgraph_workflow import MultiAgentWorkflow, WorkflowStage

EXPECTED_STAGE_SEQUENCE = [
    WorkflowStage.START,
    WorkflowStage.RESEARCH,
    WorkflowStage.WRITE,
    WorkflowStage.EDIT,
    WorkflowStage.SEO,
    WorkflowStage.IMAGE,
    WorkflowStage.CMS,
    WorkflowStage.EVOLVE,
    WorkflowStage.END,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a smoke-test workflow_event chain into the configured app.log.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=PROJECT_ROOT / "logs",
        help="Directory that contains app.log.",
    )
    parser.add_argument(
        "--topic-id",
        default="log-smoke-topic",
        help="Topic id written into workflow_event.input_id.",
    )
    parser.add_argument(
        "--trace-id",
        default="log_smoke_trace",
        help="Trace id written into workflow_event.trace_id.",
    )
    return parser.parse_args()


def emit_stage_sequence(workflow: MultiAgentWorkflow, state: dict[str, object]) -> None:
    workflow._log_stage(WorkflowStage.START, "start", state)
    workflow._log_stage(WorkflowStage.RESEARCH, "start", state)
    workflow._log_stage(WorkflowStage.RESEARCH, "success", state, statistics_count=2, cases_count=1)
    workflow._log_stage(WorkflowStage.WRITE, "start", state)
    workflow._log_stage(WorkflowStage.WRITE, "success", state, word_count=1200)
    workflow._log_stage(WorkflowStage.EDIT, "start", state)
    workflow._log_stage(WorkflowStage.EDIT, "success", state, quality_score=88)
    workflow._log_stage(WorkflowStage.SEO, "start", state)
    workflow._log_stage(WorkflowStage.SEO, "success", state)
    workflow._log_stage(WorkflowStage.IMAGE, "start", state)
    workflow._log_stage(WorkflowStage.IMAGE, "success", state, image_count=1)
    workflow._log_stage(WorkflowStage.CMS, "start", state)
    workflow._log_stage(WorkflowStage.CMS, "success", state, publish_status="dry_run")
    workflow._log_stage(WorkflowStage.EVOLVE, "start", state)
    workflow._log_stage(WorkflowStage.EVOLVE, "success", state, page_views=0)
    workflow._log_stage(WorkflowStage.END, "success", state)


def main() -> None:
    args = parse_args()
    setup_logging(log_dir=args.log_dir)

    logger = logging.getLogger("scripts.emit_workflow_event_log")
    workflow = object.__new__(MultiAgentWorkflow)

    state = {
        "topic": {
            "id": args.topic_id,
            "title": "workflow_event log smoke test",
            "primary_keyword": "workflow_event",
        },
        "trace_id": args.trace_id,
    }

    logger.info("Starting workflow_event smoke log")
    emit_stage_sequence(workflow, state)
    logger.info("Finished workflow_event smoke log | trace_id=%s", state["trace_id"])
    logger.info(
        "Emitted %d workflow stages | trace_id=%s stages=%s",
        len(EXPECTED_STAGE_SEQUENCE),
        state["trace_id"],
        ",".join(stage.value for stage in EXPECTED_STAGE_SEQUENCE),
    )


if __name__ == "__main__":
    main()
