#!/usr/bin/env python3
"""One-shot MySQL -> Redis loader.

Beginner mental model:
    This is a manual loading tool, not a long-running service. Use it when you
    want to push a fixed batch of existing articles into Redis immediately.

Difference from redis_feeder.py:
    redis_fill.py runs once and exits.
    redis_feeder.py keeps running and checks MySQL again and again.

Use this when you want to manually enqueue a fixed batch of existing crawler
articles. The normal long-running mode uses redis_feeder.py instead.

With --clear, it also deletes all active pipeline streams before filling.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from scripts.redis_pipeline import get_redis, push_article, setup_streams, STREAM_SCORING
from scripts.pipeline_text import clean_article_text

# Articles shorter than this after HTML cleanup are usually crawler fragments,
# navigation text, or empty pages. They are skipped before reaching scoring.
MIN_CONTENT_CHARS = int(os.environ.get("FEED_MIN_CONTENT_CHARS", "50"))

# We fetch more MySQL candidates than the requested limit because some rows will
# be skipped for short/empty body text. Example: limit=100 and multiplier=5
# reads up to 500 candidate metadata rows, then keeps the first 100 valid bodies.
FETCH_MULTIPLIER = int(os.environ.get("FEED_FETCH_MULTIPLIER", "5"))


def parse_args():
    parser = argparse.ArgumentParser(description="Fill Redis scoring stream from MySQL crawler table.")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("PIPELINE_FILL_LIMIT", "100")), help="灌入文章数量")
    parser.add_argument("--clear", action="store_true", help="灌入前清空 4 个 pipeline stream")
    return parser.parse_args()


async def main():
    args = parse_args()

    # Connect to Redis first because this script's job is to enqueue messages.
    # If Redis is unavailable, fail before opening MySQL.
    r = await get_redis()
    if args.clear:
        # --clear is useful before a clean rerun. It only deletes Redis streams,
        # not MySQL source data.
        from scripts.redis_pipeline import STREAM_CMS, STREAM_IMAGE, STREAM_PUBLISH, STREAM_QUALITY, STREAM_REWRITE

        await r.delete(STREAM_SCORING, STREAM_QUALITY, STREAM_REWRITE, STREAM_PUBLISH, STREAM_IMAGE, STREAM_CMS)
        print("🧹 已清空 Redis pipeline streams")

    # setup_streams() creates consumer groups idempotently. Calling it every time
    # makes a fresh Redis instance usable without a separate migration command.
    await setup_streams(r)
    print(f"🔌 Redis OK，准备从 MySQL 读取 {args.limit} 篇")

    import aiomysql

    # This pool is intentionally tiny. redis_fill.py runs once, performs a few
    # SELECTs, then exits; it does not need a large DB connection pool.
    pool = await aiomysql.create_pool(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        connect_timeout=10,
        minsize=1,
        maxsize=3,
    )
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # Grab more candidates than limit because some rows will be skipped
            # after body-text length filtering.
            await cur.execute(
                "SELECT id, title, description, original_url, publish_date, image "
                "FROM crawler_news_main "
                "WHERE title IS NOT NULL AND CHAR_LENGTH(title) > 10 "
                "  AND COALESCE(article_usage_status, '') <> 'used' "
                "ORDER BY id DESC LIMIT %s",
                (max(args.limit * max(FETCH_MULTIPLIER, 1), args.limit),),
            )
            candidates = await cur.fetchall()
            ids = [row["id"] for row in candidates]
            contents = {}
            if ids:
                # Full content is sharded by news_id across crawler_news_0..4.
                placeholders = ",".join(["%s"] * len(ids))
                for idx in range(5):
                    await cur.execute(
                        f"SELECT news_id, content FROM crawler_news_{idx} WHERE news_id IN ({placeholders})",
                        ids,
                    )
                    for row in await cur.fetchall():
                        contents[row["news_id"]] = row.get("content") or ""
            rows = []
            skipped = 0
            for row in candidates:
                # Combine description and shard body so downstream scoring sees
                # the real article, not only a short summary.
                source_content = clean_article_text(
                    ((row.get("description") or "") + "\n" + (contents.get(row["id"]) or "")).strip()
                )
                if len(source_content) < MIN_CONTENT_CHARS:
                    skipped += 1
                    continue
                row["content"] = source_content
                rows.append(row)
                if len(rows) >= args.limit:
                    break
    pool.close(); await pool.wait_closed()
    print(f"📦 MySQL 读取 {len(rows)} 篇，跳过 {skipped} 篇正文过短文章，开始写入 Redis")

    count = 0
    for row in rows:
        source_content = row["content"]
        # This payload shape matches redis_feeder.article_payload(). Both tools
        # feed the same first stream: pipeline:scoring.
        await push_article(r, STREAM_SCORING, {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "content": source_content,
            "source_content": source_content,
            "source_url": row.get("original_url", ""),
            "source_image": row.get("image", ""),
            "publish_date": str(row.get("publish_date", "")),
        })
        count += 1
    print(f"✅ 灌入 {count} 篇到 {STREAM_SCORING}")
    await r.aclose()

if __name__ == "__main__":
    asyncio.run(main())
