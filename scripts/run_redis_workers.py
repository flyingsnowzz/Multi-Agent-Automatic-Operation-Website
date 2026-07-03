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

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Redis pipeline workers.")
    parser.add_argument(
        "--fill",
        action="store_true",
        default=_env_bool("PIPELINE_FILL_ON_START", False),
        help="先执行 redis_fill.py 灌入文章",
    )
    parser.add_argument(
        "--fill-limit",
        type=int,
        default=_env_int("PIPELINE_FILL_LIMIT", 100),
        help="redis_fill.py 一次性灌入文章数",
    )
    parser.add_argument(
        "--feed",
        action="store_true",
        default=_env_bool("PIPELINE_FEED_ENABLED", False),
        help="启动 MySQL -> Redis 定时 feeder",
    )
    parser.add_argument("--feed-interval", type=int, default=_env_int("PIPELINE_FEED_INTERVAL_SECONDS", 60), help="feeder 轮询间隔秒数")
    parser.add_argument("--feed-limit", type=int, default=_env_int("PIPELINE_FEED_LIMIT", 20), help="feeder 每轮最多推入文章数")
    parser.add_argument(
        "--feed-max-inflight",
        type=int,
        default=_env_int("PIPELINE_FEED_MAX_INFLIGHT", 20),
        help="流水线未完成消息达到该数量时 feeder 暂停灌入；设为 0 关闭限制",
    )
    parser.add_argument("--feed-from-id", type=int, default=os.environ.get("PIPELINE_FEED_FROM_ID"), help="feeder 首次从指定 id 之后开始")
    parser.add_argument(
        "--feed-existing",
        action="store_true",
        default=_env_bool("PIPELINE_FEED_EXISTING", False),
        help="无 feeder 状态时从 id=0 开始灌历史数据；默认只处理启动后的新增文章",
    )
    parser.add_argument("--publish", action="store_true", help="发布 worker 使用真实发布模式")
    parser.add_argument("--dry-run", action="store_true", help="发布 worker 使用 dry-run 模式")
    parser.add_argument("--scoring", type=int, default=_env_int("PIPELINE_SCORING_WORKERS", 1), help="scoring worker 数量")
    parser.add_argument("--quality", type=int, default=_env_int("PIPELINE_QUALITY_WORKERS", 1), help="quality worker 数量")
    parser.add_argument("--rewrite", type=int, default=_env_int("PIPELINE_REWRITE_WORKERS", 1), help="rewrite worker 数量")
    parser.add_argument("--seo-workers", type=int, default=_env_int("PIPELINE_SEO_WORKERS", _env_int("PIPELINE_PUBLISH_WORKERS", 1)), help="SEO/pre-publish worker 数量")
    parser.add_argument("--image-workers", type=int, default=_env_int("PIPELINE_IMAGE_WORKERS", 1), help="image worker 数量")
    parser.add_argument("--cms-workers", type=int, default=_env_int("PIPELINE_CMS_WORKERS", 1), help="CMS worker 数量")
    parser.add_argument("--publish-workers", type=int, default=None, help="兼容旧参数：等同 --seo-workers")
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
        fill_cmd = [python, "scripts/redis_fill.py", "--limit", str(args.fill_limit)]
        print(f"[supervisor] fill: {' '.join(fill_cmd)}")
        subprocess.check_call(fill_cmd, cwd=str(ROOT))

    publish_mode = "--publish" if args.publish else "--dry-run"
    if args.publish and args.dry_run:
        raise SystemExit("--publish 和 --dry-run 不能同时使用")

    seo_workers = args.seo_workers if args.publish_workers is None else args.publish_workers
    specs: list[tuple[str, list[str], int]] = [
        ("scoring", [python, "scripts/worker_scoring.py"], max(args.scoring, 0)),
        ("quality", [python, "scripts/worker_quality.py"], max(args.quality, 0)),
        ("rewrite", [python, "scripts/worker_rewrite.py"], max(args.rewrite, 0)),
        ("publish", [python, "scripts/worker_publish.py", publish_mode], max(seo_workers, 0)),
        ("image", [python, "scripts/worker_image.py"], max(args.image_workers, 0)),
        ("cms", [python, "scripts/worker_cms.py", publish_mode], max(args.cms_workers, 0)),
    ]
    if args.feed:
        feed_cmd = [
            python,
            "scripts/redis_feeder.py",
            "--interval",
            str(args.feed_interval),
            "--limit",
            str(args.feed_limit),
            "--max-inflight",
            str(args.feed_max_inflight),
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
