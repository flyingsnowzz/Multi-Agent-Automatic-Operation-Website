#!/usr/bin/env python3
"""Continuously feed new MySQL crawler articles into Redis Streams."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from scripts.redis_pipeline import STREAM_SCORING, get_redis, push_article, setup_streams


DEFAULT_STATE_PATH = ROOT / "output" / "redis_feeder_state.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll MySQL and feed new crawler articles into Redis.")
    parser.add_argument("--interval", type=int, default=60, help="轮询间隔秒数")
    parser.add_argument("--limit", type=int, default=100, help="每轮最多推入文章数")
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出")
    parser.add_argument("--from-id", type=int, default=None, help="首次启动时从指定 article id 之后开始")
    parser.add_argument(
        "--bootstrap-latest",
        action="store_true",
        help="无状态文件时从当前最大 id 开始，只处理之后新增文章",
    )
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH, help="feeder 状态文件路径")
    return parser.parse_args()


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def create_pool():
    import aiomysql

    return await aiomysql.create_pool(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        connect_timeout=10,
        minsize=1,
        maxsize=3,
    )


async def max_article_id(pool) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COALESCE(MAX(id), 0) FROM crawler_news_main")
            row = await cur.fetchone()
    return int(row[0] or 0)


async def fetch_new_articles(pool, *, after_id: int, limit: int) -> List[Dict[str, Any]]:
    import aiomysql

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, title, description, original_url, publish_date "
                "FROM crawler_news_main "
                "WHERE id > %s "
                "  AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 "
                "  AND description IS NOT NULL AND CHAR_LENGTH(description) > 50 "
                "ORDER BY id ASC LIMIT %s",
                (after_id, limit),
            )
            return list(await cur.fetchall())


def article_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "source_url": row.get("original_url", ""),
        "publish_date": str(row.get("publish_date", "")),
    }


async def feed_once(*, pool, redis_client, state: Dict[str, Any], args: argparse.Namespace) -> int:
    last_id = int(state.get("last_id") or 0)
    if last_id <= 0:
        if args.from_id is not None:
            last_id = int(args.from_id)
        elif args.bootstrap_latest:
            last_id = await max_article_id(pool)
            state["last_id"] = last_id
            save_state(args.state_path, state)
            print(f"📍 初始化 feeder 状态: last_id={last_id}，之后只处理新增文章")
            return 0

    rows = await fetch_new_articles(pool, after_id=last_id, limit=max(args.limit, 1))
    if not rows:
        print(f"📭 暂无新文章 | last_id={last_id}")
        return 0

    pushed = 0
    for row in rows:
        await push_article(redis_client, STREAM_SCORING, article_payload(row))
        pushed += 1
        last_id = max(last_id, int(row["id"]))

    state["last_id"] = last_id
    save_state(args.state_path, state)
    print(f"✅ 推入 {pushed} 篇到 {STREAM_SCORING} | last_id={last_id}")
    return pushed


async def main() -> int:
    args = parse_args()
    shutdown = False

    def on_signal(signum, frame):
        nonlocal shutdown
        shutdown = True
        print("\n🛑 feeder 收到停止信号")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    state = load_state(args.state_path)
    r = await get_redis()
    await setup_streams(r)
    pool = await create_pool()
    print(f"🚚 Redis feeder 启动 | interval={args.interval}s limit={args.limit}")

    try:
        while not shutdown:
            try:
                await feed_once(pool=pool, redis_client=r, state=state, args=args)
            except Exception as e:
                print(f"❌ feeder 本轮失败: {type(e).__name__}: {e}")
            if args.once:
                break
            await asyncio.sleep(max(args.interval, 1))
    finally:
        pool.close()
        await pool.wait_closed()
        await r.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
