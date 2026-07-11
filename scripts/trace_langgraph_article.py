#!/usr/bin/env python3
"""Trace one LangGraph article across runtime logs, prompt audit, and deadletter.

This is an operator tool, not part of the article pipeline itself. It reads the
append-only log files already produced by the runner and prints a compact report
for one article_id so debugging does not require hand-grepping huge JSONL files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is present in normal project envs.
    load_dotenv = None


ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT / ".env")


def _path_from_env(name: str, default: str) -> Path:
    """Read a path from env and resolve relative values from the repo root."""

    value = os.environ.get(name, default)
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _short(value: Any, limit: int) -> str:
    """Render a value as one readable string with a maximum length."""

    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = re.sub(r"\s+", " ", str(text)).strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated {len(text) - limit} chars]"


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    """Yield valid JSON objects from one JSONL file, skipping malformed lines."""

    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                yield item


def _article_id_matches(value: Any, article_id: str) -> bool:
    """Return whether a JSON article_id field matches the requested id."""

    return str(value) == str(article_id)


def _runtime_lines(article_id: str, log_path: Path) -> List[str]:
    """Collect runtime log lines containing article_id=<id>."""

    if not log_path.exists():
        return []
    needle = f"article_id={article_id}"
    lines = []
    with log_path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            if needle in line:
                lines.append(line.rstrip())
    return lines


def _prompt_events(article_id: str, audit_dir: Path) -> List[Dict[str, Any]]:
    """Collect prompt audit JSONL entries for one article id."""

    if not audit_dir.exists():
        return []
    events: List[Dict[str, Any]] = []
    for path in sorted(audit_dir.glob("*.jsonl")):
        for item in _read_jsonl(path):
            if _article_id_matches(item.get("article_id"), article_id):
                item["_file"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
                events.append(item)
    return events


def _deadletter_events(article_id: str, deadletter_path: Path) -> List[Dict[str, Any]]:
    """Collect deadletter entries for one article id."""

    events = []
    for item in _read_jsonl(deadletter_path):
        if _article_id_matches(item.get("article_id"), article_id):
            events.append(item)
    return events


def _print_runtime(lines: List[str]) -> None:
    """Print the runtime timeline section."""

    print("\n== Runtime timeline ==")
    if not lines:
        print("(no runtime log lines)")
        return
    for line in lines:
        print(line)


def _print_deadletter(events: List[Dict[str, Any]], *, limit: int) -> None:
    """Print any deadletter entries for the article."""

    print("\n== Deadletter ==")
    if not events:
        print("(none)")
        return
    for item in events:
        print(_short(item, limit))


def _selected_payload(payload: Any, *, limit: int, full: bool) -> str:
    """Format a payload for the report, compact by default and full on request."""

    if full:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return _short(payload, limit)


def _print_prompt_event(event: Dict[str, Any], *, limit: int, full: bool) -> None:
    """Print one prompt audit event with useful scoring/prompt/status fields."""

    print(
        f"\n[{event.get('ts')}] {event.get('stage')} / {event.get('agent_name')} / "
        f"{event.get('prompt_type')} status={event.get('status')}"
    )
    if event.get("model_name"):
        print(f"model: {event.get('model_name')}")
    if event.get("prompt_text"):
        print("prompt_text:")
        print(_selected_payload(event.get("prompt_text"), limit=limit, full=full))
    if event.get("input_payload") is not None:
        print("input_payload:")
        print(_selected_payload(event.get("input_payload"), limit=limit, full=full))
    if event.get("output_payload") is not None:
        print("output_payload:")
        print(_selected_payload(event.get("output_payload"), limit=limit, full=full))
    if event.get("error_message"):
        print(f"error: {event.get('error_message')}")


def _print_prompt_events(events: List[Dict[str, Any]], *, limit: int, full: bool) -> None:
    """Print all prompt audit events in timestamp order."""

    print("\n== Prompt / scoring / payload audit ==")
    if not events:
        print("(no prompt audit entries)")
        return
    events = sorted(events, key=lambda item: str(item.get("ts") or ""))
    for event in events:
        _print_prompt_event(event, limit=limit, full=full)


def parse_args() -> argparse.Namespace:
    """Parse trace CLI arguments."""

    parser = argparse.ArgumentParser(description="Trace one LangGraph article across local logs.")
    parser.add_argument("article_id", help="Article id to trace, for example 213")
    parser.add_argument("--full", action="store_true", help="Print full payloads instead of compact excerpts")
    parser.add_argument("--limit", type=int, default=1600, help="Characters per payload in compact mode")
    parser.add_argument("--runtime-log", type=Path, default=_path_from_env("LANGGRAPH_RUN_LOG", "logs/langgraph_batch.log"))
    parser.add_argument("--prompt-dir", type=Path, default=_path_from_env("PROMPT_AUDIT_LOG_DIR", "logs/prompt_audit"))
    parser.add_argument("--deadletter", type=Path, default=_path_from_env("LANGGRAPH_DEADLETTER_PATH", "output/langgraph_deadletter.jsonl"))
    return parser.parse_args()


def main() -> int:
    """Build and print the trace report."""

    args = parse_args()
    article_id = str(args.article_id)
    print(f"# LangGraph trace article_id={article_id}")
    print(f"runtime_log: {args.runtime_log}")
    print(f"prompt_dir: {args.prompt_dir}")
    print(f"deadletter: {args.deadletter}")

    _print_runtime(_runtime_lines(article_id, args.runtime_log))
    _print_deadletter(_deadletter_events(article_id, args.deadletter), limit=args.limit)
    _print_prompt_events(_prompt_events(article_id, args.prompt_dir), limit=args.limit, full=args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
