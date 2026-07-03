#!/usr/bin/env python3
import asyncio, json, os, sys, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker.quality")
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_QUALITY,
    STREAM_REWRITE, STREAM_PUBLISH, GROUP_QUALITY, ack_message, recover_pending,
    handle_failure, read_group_messages)
from scripts.prompt_db_logger import log_agent_prompt
from scripts.pipeline_text import article_source_content
from agents.quality_agent import QualityAgent
import redis.asyncio as redis

CONSUMER = f"quality-{os.getpid()}"

async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_QUALITY, GROUP_QUALITY, CONSUMER)
    logger.info("started")

    while True:
        try:
            msgs = await read_group_messages(
                r,
                group=GROUP_QUALITY,
                consumer=CONSUMER,
                stream=STREAM_QUALITY,
                count=1,
                block=5000,
            )
        except redis.ResponseError:
            await asyncio.sleep(5); continue
        if not msgs: continue

        for stream, entries in msgs:
            for msg_id, fields in entries:
                try: item = json.loads(fields.get("data", "{}"))
                except Exception as exc:
                    await handle_failure(
                        r,
                        stream=STREAM_QUALITY,
                        group=GROUP_QUALITY,
                        msg_id=msg_id,
                        item={"raw_data": fields.get("data", "")},
                        stage="quality_parse",
                        error=str(exc),
                        max_retries=0,
                    )
                    continue
                try:
                    source_content = article_source_content(item)
                    item["source_content"] = source_content
                    item["content"] = source_content
                    qr = await QualityAgent().score_article({
                        "title": item.get("title", ""),
                        "content": source_content[:3000],
                        "source_url": item.get("source_url", ""),
                    })
                    qs = round(float(qr.get("quality_score", 0)), 1)
                    item["quality_score"] = qs
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="quality",
                        agent_name="QualityAgent",
                        prompt_type="quality_result",
                        prompt_text=None,
                        input_payload={
                            "title": item.get("title", ""),
                            "source_content_chars": len(source_content),
                            "source_content_excerpt": source_content[:500],
                            "source_url": item.get("source_url", ""),
                        },
                        output_payload={
                            "quality_score": qs,
                            "quality_reasons": qr.get("reasons") or qr.get("dimension_reasons"),
                            "quality_suggestions": qr.get("suggestions"),
                            "raw_result": qr,
                        },
                        model_name=os.environ.get("QUALITY_AGENT_MODEL", ""),
                    )
                    import aiomysql
                    try:
                        pool = await aiomysql.create_pool(
                            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=3)
                        async with pool.acquire() as c:
                            async with c.cursor() as cur:
                                await cur.execute(
                                    "INSERT INTO pipeline_audit (article_id, quality_score) "
                                    "VALUES (%s,%s) ON DUPLICATE KEY UPDATE quality_score=VALUES(quality_score)",
                                    (item.get("article_id"), qs))
                            await c.commit()
                        pool.close(); await pool.wait_closed()
                    except Exception:
                        logger.exception("audit write error")
                except Exception as exc:
                    logger.exception("quality error")
                    await handle_failure(
                        r,
                        stream=STREAM_QUALITY,
                        group=GROUP_QUALITY,
                        msg_id=msg_id,
                        item=item,
                        stage="quality",
                        error=str(exc),
                    )
                    continue
                target = STREAM_REWRITE if qs <= 70 else STREAM_PUBLISH
                await r.xadd(target, {"data": json.dumps(item, ensure_ascii=False)})
                await ack_message(r, STREAM_QUALITY, GROUP_QUALITY, msg_id)
                logger.info("id=%s Q=%.1f → %s", item['article_id'], qs, "rewrite" if qs<=70 else "publish")

if __name__ == "__main__":
    asyncio.run(main())
