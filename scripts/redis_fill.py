#!/usr/bin/env python3
"""从 MySQL 灌文章到 Redis Scoring Stream"""
import argparse, asyncio, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_SCORING, push_article)
from scripts.pipeline_text import clean_article_text


def parse_args():
    parser = argparse.ArgumentParser(description="Fill Redis scoring stream from MySQL crawler table.")
    parser.add_argument("--limit", type=int, default=100, help="灌入文章数量")
    parser.add_argument("--clear", action="store_true", help="灌入前清空 4 个 pipeline stream")
    return parser.parse_args()


async def main():
    args = parse_args()
    r = await get_redis()
    if args.clear:
        from scripts.redis_pipeline import STREAM_QUALITY, STREAM_REWRITE, STREAM_PUBLISH
        await r.delete(STREAM_SCORING, STREAM_QUALITY, STREAM_REWRITE, STREAM_PUBLISH)
        print("🧹 已清空 Redis pipeline streams")
    await setup_streams(r)
    print(f"🔌 Redis OK，准备从 MySQL 读取 {args.limit} 篇")

    import aiomysql
    pool = await aiomysql.create_pool(
        host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DATABASE"], charset="utf8mb4", connect_timeout=10,
        minsize=1, maxsize=3,
    )
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, title, description, original_url, publish_date, image "
            "FROM crawler_news_main "
            "WHERE title IS NOT NULL AND CHAR_LENGTH(title) > 10 "
            "  AND description IS NOT NULL AND CHAR_LENGTH(description) > 50 "
            "  AND COALESCE(article_usage_status, '') <> 'used' "
            "ORDER BY id DESC LIMIT %s",
            (args.limit,)
            )
            rows = await cur.fetchall()
            ids = [row["id"] for row in rows]
            contents = {}
            if ids:
                placeholders = ",".join(["%s"] * len(ids))
                for idx in range(5):
                    await cur.execute(
                        f"SELECT news_id, content FROM crawler_news_{idx} WHERE news_id IN ({placeholders})",
                        ids,
                    )
                    for row in await cur.fetchall():
                        contents[row["news_id"]] = row.get("content") or ""
    pool.close(); await pool.wait_closed()
    print(f"📦 MySQL 读取 {len(rows)} 篇，开始写入 Redis")

    count = 0
    for row in rows:
        source_content = clean_article_text(((row.get("description") or "") + "\n" + (contents.get(row["id"]) or "")).strip())
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
