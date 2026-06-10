from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def write_run_artifacts(
    *,
    workflow: str,
    run_id: str,
    input_payload: Dict[str, Any],
    result_payload: Dict[str, Any],
    error_payload: Optional[Any],
    runs_root: str = "runs",
) -> Path:
    """
    Persist a workflow run in a predictable local layout.

    Layout:
      runs/<workflow>/<run_id>/input.json
      runs/<workflow>/<run_id>/result.json
      runs/<workflow>/<run_id>/error.json
    """
    safe_workflow = str(workflow).strip().replace("\\", "_").replace("/", "_")
    safe_run_id = str(run_id).strip().replace("\\", "_").replace("/", "_")
    if not safe_workflow:
        raise ValueError("workflow is required")
    if not safe_run_id:
        raise ValueError("run_id is required")

    run_dir = Path(runs_root) / safe_workflow / safe_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(run_dir / "input.json", input_payload)
    _write_json(run_dir / "result.json", result_payload)
    _write_json(run_dir / "error.json", {"error": error_payload})
    return run_dir


def _write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
