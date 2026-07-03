#!/usr/bin/env python3
"""Health checks for the Redis pipeline.

Exit code:
  0 = healthy
  1 = one or more thresholds failed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from scripts.redis_pipeline import (
    GROUP_PUBLISH,
    GROUP_QUALITY,
    GROUP_REWRITE,
    GROUP_SCORING,
    STREAM_DEADLETTER,
    STREAM_PUBLISH,
    STREAM_QUALITY,
    STREAM_REWRITE,
    STREAM_SCORING,
    get_redis,
)


STREAM_GROUPS: List[Tuple[str, str]] = [
    (STREAM_SCORING, GROUP_SCORING),
    (STREAM_QUALITY, GROUP_QUALITY),
    (STREAM_REWRITE, GROUP_REWRITE),
    (STREAM_PUBLISH, GROUP_PUBLISH),
]

WORKER_PATTERNS = [
    "scripts/worker_scoring.py",
    "scripts/worker_quality.py",
    "scripts/worker_rewrite.py",
    "scripts/worker_publish.py",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Redis pipeline health.")
    parser.add_argument("--max-deadletter", type=int, default=int(os.environ.get("MONITOR_MAX_DEADLETTER", "20")))
    parser.add_argument("--max-pending", type=int, default=int(os.environ.get("MONITOR_MAX_PENDING", "50")))
    parser.add_argument("--require-workers", action="store_true", help="Fail if local worker processes are not running.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def local_worker_counts() -> Dict[str, int]:
    try:
        proc = subprocess.run(["ps", "aux"], check=True, text=True, capture_output=True)
    except Exception:
        return {}
    counts: Dict[str, int] = {}
    for pattern in WORKER_PATTERNS:
        counts[pattern] = sum(1 for line in proc.stdout.splitlines() if pattern in line and "monitor_pipeline.py" not in line)
    return counts


async def pending_count(r, stream: str, group: str) -> int:
    try:
        info = await r.xpending(stream, group)
    except Exception:
        return 0
    if isinstance(info, dict):
        return int(info.get("pending") or 0)
    if isinstance(info, (list, tuple)) and info:
        return int(info[0] or 0)
    return 0


async def collect_health(args: argparse.Namespace) -> Dict[str, Any]:
    r = await get_redis()
    try:
        stream_lengths = {stream: int(await r.xlen(stream)) for stream, _group in STREAM_GROUPS}
        stream_lengths[STREAM_DEADLETTER] = int(await r.xlen(STREAM_DEADLETTER))
        pending = {stream: await pending_count(r, stream, group) for stream, group in STREAM_GROUPS}
    finally:
        await r.aclose()

    workers = local_worker_counts()
    failures: List[str] = []

    if stream_lengths[STREAM_DEADLETTER] > args.max_deadletter:
        failures.append(f"deadletter_count>{args.max_deadletter}:{stream_lengths[STREAM_DEADLETTER]}")

    for stream, count in pending.items():
        if count > args.max_pending:
            failures.append(f"pending_count>{args.max_pending}:{stream}={count}")

    if args.require_workers:
        for pattern in WORKER_PATTERNS:
            if workers.get(pattern, 0) <= 0:
                failures.append(f"worker_missing:{pattern}")

    return {
        "ok": not failures,
        "failures": failures,
        "stream_lengths": stream_lengths,
        "pending": pending,
        "workers": workers,
        "thresholds": {
            "max_deadletter": args.max_deadletter,
            "max_pending": args.max_pending,
            "require_workers": args.require_workers,
        },
    }


def print_human(report: Dict[str, Any]) -> None:
    status = "OK" if report["ok"] else "FAILED"
    print(f"pipeline_health={status}")
    print("stream_lengths:")
    for key, value in report["stream_lengths"].items():
        print(f"  {key}: {value}")
    print("pending:")
    for key, value in report["pending"].items():
        print(f"  {key}: {value}")
    if report["workers"]:
        print("workers:")
        for key, value in report["workers"].items():
            print(f"  {key}: {value}")
    if report["failures"]:
        print("failures:")
        for failure in report["failures"]:
            print(f"  - {failure}")


async def main() -> int:
    args = parse_args()
    report = await collect_health(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
