"""Best-effort prompt audit logging for pipeline workers.

Beginner mental model:
    Some agent inputs/outputs are huge: prompts, scoring reasons, quality
    suggestions, and breakdowns. They are useful for debugging but expensive to
    store in MySQL. This module writes them as local JSONL log lines instead.

Prompt audit entries are written to JSONL files under logs/prompt_audit by
default. This keeps large prompt payloads out of MySQL while preserving enough
context for local debugging and incident review.

The name is historical: this module no longer writes prompts into the database.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> str:
    """Convert non-JSON-native values to strings for prompt audit logging."""
    # Fallback for values JSON does not know how to serialize, such as datetime
    # objects or custom classes returned by an agent.
    return str(value)


def _jsonable(value: Any) -> Any:
    """Return a JSON-serializable representation without losing normal structures."""
    # Keep original dict/list payloads when possible. If something inside cannot
    # be serialized, degrade gracefully to a string instead of breaking the pipeline.
    try:
        json.dumps(value, ensure_ascii=False, default=_json_default)
        return value
    except TypeError:
        return str(value)


def _audit_enabled() -> bool:
    """Check whether local prompt audit logging is enabled by environment flag."""
    # Toggle prompt logging without code changes. Useful if logs become too large
    # during a long production run.
    raw = os.environ.get("PROMPT_AUDIT_ENABLED", "true")
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _audit_path(now: datetime) -> Path:
    """Build the daily JSONL prompt audit file path for a timestamp."""
    # One JSONL file per day keeps files smaller and easier to open.
    log_dir = Path(os.environ.get("PROMPT_AUDIT_LOG_DIR", "logs/prompt_audit"))
    return log_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"


def _write_prompt_log(entry: Mapping[str, Any]) -> Path:
    """Append one prompt audit entry as a JSONL line and return the file path."""
    # JSONL format means each line is one complete JSON object. It can be tailed,
    # grepped, or loaded line-by-line without parsing a giant JSON array.
    now = datetime.now()
    path = _audit_path(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": now.isoformat(timespec="seconds"),
        **entry,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")
    return path


async def log_agent_prompt(
    *,
    article_id: Any = None,
    stage: str,
    agent_name: str,
    prompt_type: Optional[str] = None,
    prompt_text: Optional[str] = None,
    input_payload: Optional[Mapping[str, Any]] = None,
    output_payload: Optional[Mapping[str, Any]] = None,
    model_name: Optional[str] = None,
    status: str = "ok",
    error_message: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Write one prompt audit entry without making pipeline success depend on it."""
    if not _audit_enabled():
        return

    # Standard field names across agents make later debugging easier. For
    # example, you can grep by article_id, stage, or agent_name.
    entry = {
        "article_id": article_id,
        "run_id": run_id,
        "stage": stage,
        "agent_name": agent_name,
        "prompt_type": prompt_type,
        "prompt_text": prompt_text,
        "input_payload": _jsonable(input_payload),
        "output_payload": _jsonable(output_payload),
        "model_name": model_name,
        "status": status,
        "error_message": error_message,
    }
    try:
        path = _write_prompt_log(entry)
        logger.debug("prompt audit log written: %s", path)
    except Exception as exc:
        # Prompt logging is best-effort. The article pipeline should not fail
        # just because the local log directory is unavailable.
        logger.warning("prompt audit log skipped: %s", exc)
