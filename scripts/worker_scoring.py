#!/usr/bin/env python3
"""AI 评分 Worker — 批量打分 → 写 MySQL → 推 Quality"""
import asyncio, json, os, sys, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker.scoring")

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_SCORING,
    STREAM_QUALITY, GROUP_SCORING, BATCH_SCORING, ack_message, recover_pending,
    handle_failure, read_group_messages)
from scripts.prompt_db_logger import log_agent_prompt
from scripts.pipeline_text import article_source_content
from agents.scoring_agent.scoring_summary import summarize_crawler_topics
import redis.asyncio as redis

CONSUMER = f"scorer-{os.getpid()}"
AI_SCORE_THRESHOLD = float(os.environ.get("AI_SCORE_THRESHOLD", "75"))

async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_SCORING, GROUP_SCORING, CONSUMER)
    logger.info("启动，批量 %d 篇", BATCH_SCORING)

    while True:
        try:
            msgs = await read_group_messages(
                r,
                group=GROUP_SCORING,
                consumer=CONSUMER,
                stream=STREAM_SCORING,
                count=BATCH_SCORING,
                block=5000,
            )
        except redis.ResponseError:
            await asyncio.sleep(5); continue
        if not msgs: continue

        batch, msg_ids = [], []
        for stream, entries in msgs:
            for msg_id, fields in entries:
                try:
                    item = json.loads(fields.get("data", "{}"))
                    source_content = article_source_content(item)
                    if source_content:
                        item["source_content"] = source_content
                        item["content"] = source_content
                    batch.append(item)
                    msg_ids.append((stream, msg_id, item))
                except Exception as exc:
                    logger.exception("parse error")
                    await handle_failure(
                        r,
                        stream=stream,
                        group=GROUP_SCORING,
                        msg_id=msg_id,
                        item={"raw_data": fields.get("data", "")},
                        stage="scoring_parse",
                        error=str(exc),
                        max_retries=0,
                    )
        if not batch: continue

        try:
            result = await asyncio.to_thread(summarize_crawler_topics, batch, use_ai=True, ai_concurrency=4)
            articles_by_id = {str(item.get("id") or item.get("article_id")): item for item in batch}
            import aiomysql
            try:
                pool = await aiomysql.create_pool(
                    host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                    user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                    db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=3)
                for se in result.get("article_scores", []):
                    if se.get("overall_score") is not None:
                        original = articles_by_id.get(str(se.get("article_id"))) or {}
                        source_content = article_source_content(original)
                        await log_agent_prompt(
                            article_id=se.get("article_id"),
                            stage="scoring",
                            agent_name="ScoringAgent",
                            prompt_type="scoring_result",
                            prompt_text=None,
                            input_payload={
                                "title": se.get("title"),
                                "source_url": se.get("source_url"),
                                "source_content_chars": len(source_content),
                                "source_content_excerpt": source_content[:500],
                            },
                            output_payload={
                                "ai_score": se.get("overall_score"),
                                "scoring_reason": se.get("ai_reason") or se.get("reason"),
                                "scoring_breakdown": se.get("score_breakdown"),
                            },
                            model_name=os.environ.get("ARTICLE_SCORING_MODEL", ""),
                        )
                        source_image = original.get("source_image") or original.get("image") or ""
                        try:
                            async with pool.acquire() as c:
                                async with c.cursor() as cur:
                                    await cur.execute(
                                        "INSERT INTO pipeline_audit (article_id, ai_score, image_url) "
                                        "VALUES (%s,%s,%s) "
                                        "ON DUPLICATE KEY UPDATE "
                                        "ai_score=VALUES(ai_score), "
                                        "image_url=IF(NULLIF(image_url, '') IS NULL, VALUES(image_url), image_url)",
                                        (se.get("article_id"), se.get("overall_score"), source_image))
                                await c.commit()
                        except Exception:
                            logger.exception("pipeline_audit write error")
                    if se.get("overall_score") is not None:
                        try:
                            async with pool.acquire() as c:
                                async with c.cursor() as cur:
                                    await cur.execute(
                                        "UPDATE crawler_news_main "
                                        "SET article_overall_score=%s, article_scored_at=NOW(), "
                                        "article_usage_status='used', article_used_at=NOW() "
                                        "WHERE id=%s",
                                        (se.get("overall_score"), se.get("article_id")))
                                await c.commit()
                        except Exception:
                            logger.exception("crawler_news_main write error")
                pool.close(); await pool.wait_closed()
            except Exception:
                logger.exception("MySQL audit write error")

            for se in result.get("article_scores", []):
                if se.get("overall_score") is not None:
                    ai_score = float(se["overall_score"])
                    if ai_score < AI_SCORE_THRESHOLD:
                        logger.info(
                            "id=%s AI=%.1f threshold=%.1f → discard",
                            se.get("article_id"),
                            ai_score,
                            AI_SCORE_THRESHOLD,
                        )
                        continue
                    original = articles_by_id.get(str(se.get("article_id"))) or {}
                    source_content = article_source_content(original)
                    await r.xadd(STREAM_QUALITY, {"data": json.dumps({
                        "article_id": se.get("article_id"), "ai_score": ai_score,
                        "title": se.get("title", ""), "source_url": se.get("source_url", ""),
                        "source_image": original.get("source_image") or original.get("image", ""),
                        "description": original.get("description", ""),
                        "content": source_content,
                        "source_content": source_content,
                        "publish_date": original.get("publish_date", ""),
                    }, ensure_ascii=False)})
                    logger.info(
                        "id=%s AI=%.1f threshold=%.1f → quality",
                        se.get("article_id"),
                        ai_score,
                        AI_SCORE_THRESHOLD,
                    )
        except Exception as exc:
            logger.exception("batch scoring error")
            for stream, msg_id, item in msg_ids:
                await handle_failure(
                    r,
                    stream=stream,
                    group=GROUP_SCORING,
                    msg_id=msg_id,
                    item=item,
                    stage="scoring",
                    error=str(exc),
                )
            continue

        for stream, msg_id, _item in msg_ids:
            try: await ack_message(r, stream, GROUP_SCORING, msg_id)
            except Exception: logger.exception("ack error")
        logger.info("batch %d done", len(batch))

if __name__ == "__main__":
    asyncio.run(main())
