#!/usr/bin/env python3
"""Quality Worker — 写作质量评分 → ≤70 推改写，>70 推发布"""
import asyncio, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_QUALITY,
    STREAM_REWRITE, STREAM_PUBLISH, GROUP_QUALITY, ack_message, recover_pending)
import redis.asyncio as redis

CONSUMER = f"quality-{os.getpid()}"


async def fill_article_content(item):
    if item.get("content") or item.get("description") or not item.get("article_id"):
        return item
    try:
        import aiomysql
        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            db=os.environ["MYSQL_DATABASE"], charset="utf8mb4", minsize=1, maxsize=1)
        async with pool.acquire() as c:
            async with c.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT title, description, original_url FROM crawler_news_main WHERE id=%s LIMIT 1",
                    (item.get("article_id"),))
                row = await cur.fetchone()
        pool.close(); await pool.wait_closed()
        if row:
            item["title"] = item.get("title") or row.get("title", "")
            item["description"] = row.get("description", "") or ""
            item["content"] = item["description"]
            item["source_url"] = item.get("source_url") or row.get("original_url", "")
    except Exception as e:
        print(f"[{CONSUMER}] 正文回填失败: {e}")
    return item


async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_QUALITY, GROUP_QUALITY, CONSUMER)
    print(f"[{CONSUMER}] 启动")
    from agents.quality_agent import QualityAgent

    while True:
        try:
            msgs = await r.xreadgroup(GROUP_QUALITY, CONSUMER,
                                       {STREAM_QUALITY: ">"}, count=1, block=5000)
        except redis.ResponseError:
            await asyncio.sleep(5); continue
        if not msgs: continue

        for stream, entries in msgs:
            for msg_id, fields in entries:
                try: item = json.loads(fields.get("data", "{}"))
                except Exception: await ack_message(r, STREAM_QUALITY, GROUP_QUALITY, msg_id); continue
                item = await fill_article_content(item)
                try:
                    qr = await QualityAgent().score_article({
                        "title": item.get("title", ""),
                        "content": item.get("content", item.get("description", ""))[:3000],
                        "source_url": item.get("source_url", ""),
                    })
                    qs = round(float(qr.get("quality_score", 0)), 1)
                    item["quality_score"] = qs
                    # 写审计表
                    import aiomysql
                    try:
                        pool = await aiomysql.create_pool(
                            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                            db="multi_agent_cms", charset="utf8mb4", minsize=1, maxsize=3)
                        async with pool.acquire() as c:
                            async with c.cursor() as cur:
                                await cur.execute(
                                    "INSERT INTO pipeline_audit (article_id, quality_score, quality_reasons, quality_suggestions) "
                                    "VALUES (%s,%s,%s,%s) "
                                    "ON DUPLICATE KEY UPDATE "
                                    "quality_score=VALUES(quality_score), "
                                    "quality_reasons=VALUES(quality_reasons), "
                                    "quality_suggestions=VALUES(quality_suggestions)",
                                    (item.get("article_id"), qs,
                                     json.dumps(qr.get("reasons") or qr.get("dimension_reasons"), ensure_ascii=False),
                                     json.dumps(qr.get("suggestions"), ensure_ascii=False)))
                            await c.commit()
                        pool.close(); await pool.wait_closed()
                    except Exception as e:
                        print(f"[{CONSUMER}] 审计写入失败: {e}")
                except Exception as e:
                    print(f"[{CONSUMER}] Quality 失败: {e}")
                    await ack_message(r, STREAM_QUALITY, GROUP_QUALITY, msg_id); continue
                target = STREAM_REWRITE if qs <= 70 else STREAM_PUBLISH
                await r.xadd(target, {"data": json.dumps(item, ensure_ascii=False)})
                await ack_message(r, STREAM_QUALITY, GROUP_QUALITY, msg_id)
                print(f"[{CONSUMER}] id={item['article_id']} Q={qs} → {'rewrite' if qs<=70 else 'publish'}")

if __name__ == "__main__":
    asyncio.run(main())
