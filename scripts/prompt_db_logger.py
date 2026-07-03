"""Best-effort prompt audit logging for pipeline workers.

Prompt audit entries are written to JSONL files under logs/prompt_audit by
default. This keeps large prompt payloads out of MySQL while preserving enough
context for local debugging and incident review.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> str:
    return str(value)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=_json_default)
        return value
    except TypeError:
        return str(value)


def _audit_enabled() -> bool:
    raw = os.environ.get("PROMPT_AUDIT_ENABLED", "true")
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _audit_path(now: datetime) -> Path:
    log_dir = Path(os.environ.get("PROMPT_AUDIT_LOG_DIR", "logs/prompt_audit"))
    return log_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"


def _write_prompt_log(entry: Mapping[str, Any]) -> Path:
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
        logger.warning("prompt audit log skipped: %s", exc)
