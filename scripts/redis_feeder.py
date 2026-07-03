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

from scripts.redis_pipeline import (
    GROUP_PUBLISH,
    GROUP_QUALITY,
    GROUP_REWRITE,
    GROUP_SCORING,
    GROUP_IMAGE,
    GROUP_CMS,
    STREAM_IMAGE,
    STREAM_CMS,
    STREAM_PUBLISH,
    STREAM_QUALITY,
    STREAM_REWRITE,
    STREAM_SCORING,
    get_redis,
    push_article,
    setup_streams,
)
from scripts.pipeline_text import clean_article_text


DEFAULT_STATE_PATH = ROOT / "output" / "redis_feeder_state.json"
MIN_CONTENT_CHARS = int(os.environ.get("FEED_MIN_CONTENT_CHARS", "50"))
FETCH_MULTIPLIER = int(os.environ.get("FEED_FETCH_MULTIPLIER", "5"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll MySQL and feed new crawler articles into Redis.")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("PIPELINE_FEED_INTERVAL_SECONDS", "60")), help="轮询间隔秒数")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("PIPELINE_FEED_LIMIT", "20")), help="每轮最多推入文章数")
    parser.add_argument(
        "--max-inflight",
        type=int,
        default=int(os.environ.get("PIPELINE_FEED_MAX_INFLIGHT", "20")),
        help="流水线未完成消息达到该数量时暂停灌入；设为 0 关闭限制",
    )
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


async def fetch_new_articles(pool, *, after_id: int, limit: int) -> tuple[List[Dict[str, Any]], int, int]:
    import aiomysql

    candidate_limit = max(limit * max(FETCH_MULTIPLIER, 1), limit)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, title, description, original_url, publish_date, image "
                "FROM crawler_news_main "
                "WHERE id > %s "
                "  AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 "
                "  AND COALESCE(article_usage_status, '') <> 'used' "
                "ORDER BY id ASC LIMIT %s",
                (after_id, candidate_limit),
            )
            candidates = list(await cur.fetchall())
            ids = [row["id"] for row in candidates]
            contents: Dict[Any, str] = {}
            if ids:
                placeholders = ",".join(["%s"] * len(ids))
                for idx in range(5):
                    await cur.execute(
                        f"SELECT news_id, content FROM crawler_news_{idx} WHERE news_id IN ({placeholders})",
                        ids,
                    )
                    for row in await cur.fetchall():
                        contents[row["news_id"]] = row.get("content") or ""
            rows: List[Dict[str, Any]] = []
            scanned_last_id = after_id
            skipped = 0
            for row in candidates:
                scanned_last_id = max(scanned_last_id, int(row["id"]))
                source_content = clean_article_text(
                    ((row.get("description") or "") + "\n" + (contents.get(row["id"]) or "")).strip()
                )
                if len(source_content) < MIN_CONTENT_CHARS:
                    skipped += 1
                    continue
                row["content"] = source_content
                rows.append(row)
                if len(rows) >= limit:
                    break
            return rows, scanned_last_id, skipped


def article_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    source_content = clean_article_text(row.get("content") or row.get("description", ""))
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "content": source_content,
        "source_content": source_content,
        "source_url": row.get("original_url", ""),
        "source_image": row.get("image", ""),
        "publish_date": str(row.get("publish_date", "")),
    }


async def stream_group_backlog(redis_client, stream: str, group: str) -> int:
    try:
        groups = await redis_client.xinfo_groups(stream)
    except Exception:
        return 0

    for info in groups:
        name = info.get("name")
        if isinstance(name, bytes):
            name = name.decode()
        if name != group:
            continue
        pending = int(info.get("pending") or 0)
        lag = info.get("lag")
        if lag is None:
            return pending
        return pending + int(lag or 0)
    return 0


async def pipeline_backlog(redis_client) -> int:
    checks = [
        (STREAM_SCORING, GROUP_SCORING),
        (STREAM_QUALITY, GROUP_QUALITY),
        (STREAM_REWRITE, GROUP_REWRITE),
        (STREAM_PUBLISH, GROUP_PUBLISH),
        (STREAM_IMAGE, GROUP_IMAGE),
        (STREAM_CMS, GROUP_CMS),
    ]
    return sum([await stream_group_backlog(redis_client, stream, group) for stream, group in checks])


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

    feed_limit = max(args.limit, 1)
    if args.max_inflight > 0:
        backlog = await pipeline_backlog(redis_client)
        if backlog >= args.max_inflight:
            print(f"⏸️ 流水线积压 {backlog} 篇，达到上限 {args.max_inflight}，本轮不灌入 | last_id={last_id}")
            return 0
        feed_limit = min(feed_limit, max(args.max_inflight - backlog, 1))

    rows, scanned_last_id, skipped = await fetch_new_articles(pool, after_id=last_id, limit=feed_limit)
    if not rows:
        if scanned_last_id > last_id:
            state["last_id"] = scanned_last_id
            save_state(args.state_path, state)
            print(
                f"⏭️ 本轮扫描到 {scanned_last_id}，跳过 {skipped} 篇正文过短文章 | last_id={scanned_last_id}"
            )
            return 0
        print(f"📭 暂无新文章 | last_id={last_id}")
        return 0

    pushed = 0
    for row in rows:
        await push_article(redis_client, STREAM_SCORING, article_payload(row))
        pushed += 1
        last_id = max(last_id, int(row["id"]))

    state["last_id"] = max(last_id, scanned_last_id)
    save_state(args.state_path, state)
    print(f"✅ 推入 {pushed} 篇到 {STREAM_SCORING}，跳过 {skipped} 篇正文过短文章 | last_id={state['last_id']}")
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
