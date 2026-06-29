#!/usr/bin/env python3
"""Start Redis pipeline workers together.

Default mode is dry-run publishing. Use --publish only after CMS_* env vars are set.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Redis pipeline workers.")
    parser.add_argument("--fill", action="store_true", help="先执行 redis_fill.py 灌入文章")
    parser.add_argument("--feed", action="store_true", help="启动 MySQL -> Redis 定时 feeder")
    parser.add_argument("--feed-interval", type=int, default=60, help="feeder 轮询间隔秒数")
    parser.add_argument("--feed-limit", type=int, default=100, help="feeder 每轮最多推入文章数")
    parser.add_argument("--feed-from-id", type=int, default=None, help="feeder 首次从指定 id 之后开始")
    parser.add_argument(
        "--feed-existing",
        action="store_true",
        help="无 feeder 状态时从 id=0 开始灌历史数据；默认只处理启动后的新增文章",
    )
    parser.add_argument("--publish", action="store_true", help="发布 worker 使用真实发布模式")
    parser.add_argument("--dry-run", action="store_true", help="发布 worker 使用 dry-run 模式")
    parser.add_argument("--scoring", type=int, default=1, help="scoring worker 数量")
    parser.add_argument("--quality", type=int, default=1, help="quality worker 数量")
    parser.add_argument("--rewrite", type=int, default=1, help="rewrite worker 数量")
    parser.add_argument("--publish-workers", type=int, default=1, help="publish worker 数量")
    return parser.parse_args()


def start(cmd: list[str], *, name: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    print(f"[supervisor] start {name}: {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=str(ROOT), env=env)


def main() -> int:
    args = parse_args()
    python = sys.executable

    if args.fill:
        fill_cmd = [python, "scripts/redis_fill.py"]
        print(f"[supervisor] fill: {' '.join(fill_cmd)}")
        subprocess.check_call(fill_cmd, cwd=str(ROOT))

    publish_mode = "--publish" if args.publish else "--dry-run"
    if args.publish and args.dry_run:
        raise SystemExit("--publish 和 --dry-run 不能同时使用")

    specs: list[tuple[str, list[str], int]] = [
        ("scoring", [python, "scripts/worker_scoring.py"], max(args.scoring, 0)),
        ("quality", [python, "scripts/worker_quality.py"], max(args.quality, 0)),
        ("rewrite", [python, "scripts/worker_rewrite.py"], max(args.rewrite, 0)),
        ("publish", [python, "scripts/worker_publish.py", publish_mode], max(args.publish_workers, 0)),
    ]
    if args.feed:
        feed_cmd = [
            python,
            "scripts/redis_feeder.py",
            "--interval",
            str(args.feed_interval),
            "--limit",
            str(args.feed_limit),
        ]
        if args.feed_from_id is not None:
            feed_cmd.extend(["--from-id", str(args.feed_from_id)])
        elif not args.feed_existing:
            feed_cmd.append("--bootstrap-latest")
        specs.insert(0, ("feeder", feed_cmd, 1))

    procs: list[subprocess.Popen] = []
    stopping = False

    def stop_all(signum=None, frame=None):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print("\n[supervisor] stopping workers...")
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 10
        while time.time() < deadline and any(proc.poll() is None for proc in procs):
            time.sleep(0.2)
        for proc in procs:
            if proc.poll() is None:
                proc.kill()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    for name, cmd, count in specs:
        for index in range(count):
            procs.append(start(cmd, name=f"{name}-{index + 1}"))

    try:
        while not stopping:
            for proc in procs:
                code = proc.poll()
                if code is not None:
                    stop_all()
                    print(f"[supervisor] worker exited with code {code}")
                    return code
            time.sleep(1)
    finally:
        stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
