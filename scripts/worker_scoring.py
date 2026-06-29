#!/usr/bin/env python3
"""AI 评分 Worker — 批量打分 → 写 MySQL → 推 Quality"""
import asyncio, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_SCORING,
    STREAM_QUALITY, GROUP_SCORING, BATCH_SCORING, ack_message, recover_pending)
from agents.scoring_agent.scoring_summary import summarize_crawler_topics
import redis.asyncio as redis

CONSUMER = f"scorer-{os.getpid()}"

async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_SCORING, GROUP_SCORING, CONSUMER)
    print(f"[{CONSUMER}] 启动，批量 {BATCH_SCORING} 篇")

    while True:
        try:
            msgs = await r.xreadgroup(GROUP_SCORING, CONSUMER,
                                       {STREAM_SCORING: ">"}, count=BATCH_SCORING, block=5000)
        except redis.ResponseError:
            await asyncio.sleep(5); continue
        if not msgs: continue

        batch = []; msg_ids = []
        for stream, entries in msgs:
            for msg_id, fields in entries:
                try:
                    article = json.loads(fields.get("data", "{}"))
                    batch.append(article)
                    msg_ids.append((stream, msg_id))
                except Exception: continue
        if not batch: continue

        try:
            result = summarize_crawler_topics(batch, use_ai=True, ai_concurrency=4)
            # 写审计表
            import aiomysql
            try:
                pool = await aiomysql.create_pool(
                    host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                    user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                    db="multi_agent_cms", charset="utf8mb4", minsize=1, maxsize=3)
                for se in result.get("article_scores", []):
                    if se.get("overall_score") is not None:
                        try:
                            async with pool.acquire() as c:
                                async with c.cursor() as cur:
                                    await cur.execute(
                                        "INSERT INTO pipeline_audit (article_id, ai_score, scoring_reason, scoring_breakdown) "
                                        "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE ai_score=VALUES(ai_score)",
                                        (se.get("article_id"), se.get("overall_score"),
                                         se.get("reason"), json.dumps(se.get("score_breakdown"), ensure_ascii=False) if se.get("score_breakdown") else None))
                                await c.commit()
                        except Exception: pass
                pool.close(); await pool.wait_closed()
            except Exception as e:
                print(f"[{CONSUMER}] 审计写入失败: {e}")

            articles_by_id = {article.get("id"): article for article in batch}

            # 推 Quality 队列
            for se in result.get("article_scores", []):
                if se.get("overall_score") is not None:
                    source_article = articles_by_id.get(se.get("article_id"), {})
                    await r.xadd(STREAM_QUALITY, {"data": json.dumps({
                        "article_id": se.get("article_id"), "ai_score": se["overall_score"],
                        "title": se.get("title") or source_article.get("title", ""),
                        "source_url": se.get("source_url") or source_article.get("source_url") or source_article.get("original_url", ""),
                        "description": source_article.get("description", ""),
                        "content": source_article.get("content") or source_article.get("description", ""),
                    }, ensure_ascii=False)})
        except Exception as e:
            print(f"[{CONSUMER}] 批次失败: {e}")
        finally:
            for stream, msg_id in msg_ids:
                try: await ack_message(r, stream, GROUP_SCORING, msg_id)
                except Exception: pass
        print(f"[{CONSUMER}] 批次 {len(batch)} 篇完成")

if __name__ == "__main__":
    asyncio.run(main())
