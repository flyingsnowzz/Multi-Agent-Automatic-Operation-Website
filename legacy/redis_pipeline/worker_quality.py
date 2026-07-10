#!/usr/bin/env python3
"""Original-article quality worker.

Beginner mental model:
    This is the first quality checkpoint after scoring. Scoring asks "is this
    topic worth doing at all?" Quality asks "is the original crawler article
    good enough to publish/forward directly, or does it need to be rewritten?"

Why this is separate from scoring:
    Scoring and quality look similar, but they answer different questions.
    Scoring filters topic value. Quality decides the route of an already
    accepted article.

Input stream:
    pipeline:quality

Main work:
    - call QualityAgent on the original crawler article body
    - write quality_score to pipeline_audit
    - choose whether the original article can publish directly

Output:
    - quality_score > QUALITY_PASS_THRESHOLD -> pipeline:publish
    - quality_score <= threshold -> pipeline:rewrite

What happens after pipeline:rewrite:
    This worker does not run ResearchAgent or WriterAgent directly. It only
    routes weak original articles into the rewrite stream. worker_rewrite.py is
    the next station and it runs:
        ResearchAgent -> WriterAgent -> second QualityAgent -> EditorAgent

Common confusion:
    Going to pipeline:publish here does not mean CMS has published it. It means
    "send this article to the publish preparation stages", starting with SEO.
    Going to pipeline:rewrite means "let worker_rewrite.py research and rewrite
    it"; no rewriting happens inside this file.
"""
import asyncio, json, os, sys, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker.quality")
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from legacy.redis_pipeline.redis_pipeline import (get_redis, setup_streams, STREAM_QUALITY,
    STREAM_REWRITE, STREAM_PUBLISH, GROUP_QUALITY, ack_message, recover_pending,
    handle_failure, read_group_messages)
from scripts.prompt_db_logger import log_agent_prompt
from scripts.pipeline_text import article_source_content
from agents.quality_agent import QualityAgent
import redis.asyncio as redis

# Redis consumer name. If a process dies, this name appears in pending message
# recovery logs and helps identify which worker owned the message.
CONSUMER = f"quality-{os.getpid()}"

# First quality gate. Articles above this publish directly; articles at or
# below this value go into rewrite. Keep this in .env for easy tuning.
QUALITY_PASS_THRESHOLD = float(os.environ.get("QUALITY_PASS_THRESHOLD", "70"))

async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_QUALITY, GROUP_QUALITY, CONSUMER)
    logger.info("started")

    # One article at a time: quality scoring is slower than cheap parsing, and
    # per-message routing makes failures easier to isolate.
    while True:
        try:
            # Block briefly waiting for Redis messages, then loop again. This
            # keeps the process alive without busy-spinning when the queue is empty.
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
                # Bad JSON cannot be recovered by retrying, so it goes straight
                # through handle_failure(... max_retries=0).
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
                    # Always rebuild the content field from the payload helper.
                    # This lets upstream send either source_content, content, or
                    # description while QualityAgent receives a consistent input.
                    source_content = article_source_content(item)
                    item["source_content"] = source_content
                    item["content"] = source_content
                    # QualityAgent returns a detailed result, but MySQL stores
                    # only the numeric score; detailed reasons are JSONL logs.
                    # The first quality gate uses a 3000-char excerpt to keep
                    # latency/cost predictable. The full source_content remains
                    # in the Redis payload for rewrite if the article fails.
                    qr = await QualityAgent().score_article({
                        "title": item.get("title", ""),
                        "content": source_content[:3000],
                        "source_url": item.get("source_url", ""),
                    })
                    # Normalize the model score to one decimal before both DB
                    # storage and routing, so the UI and route logs match.
                    qs = round(float(qr.get("quality_score", 0)), 1)
                    item["quality_score"] = qs
                    # Reasons/suggestions can be long. They are intentionally
                    # logged to JSONL instead of MySQL to avoid bloating the DB.
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
                        # Upsert allows scoring and quality workers to write the
                        # same audit row independently without ordering races.
                        # Example: scoring may create the row first; quality
                        # later updates quality_score on that same row.
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
                # First quality gate decides whether the original article can
                # publish directly or must be rewritten before spending later
                # editor/SEO/image/CMS work. If should_rewrite=True, this file
                # only pushes the item to STREAM_REWRITE; ResearchAgent and
                # WriterAgent are executed later by legacy/redis_pipeline/worker_rewrite.py.
                # <= means a score exactly equal to the threshold is still
                # rewritten. Direct publish requires strictly better quality.
                should_rewrite = qs <= QUALITY_PASS_THRESHOLD
                target = STREAM_REWRITE if should_rewrite else STREAM_PUBLISH
                # target is the only branch decision in this worker:
                #   rewrite -> ResearchAgent/WriterAgent chain
                #   publish -> SEO pre-publish worker
                # Keep the original payload intact when passing to the next
                # stage. Later workers need article_id, title, source image,
                # content, source_url, ai_score, and quality_score.
                await r.xadd(target, {"data": json.dumps(item, ensure_ascii=False)})
                await ack_message(r, STREAM_QUALITY, GROUP_QUALITY, msg_id)
                logger.info(
                    "id=%s Q=%.1f threshold=%.1f → %s",
                    item["article_id"],
                    qs,
                    QUALITY_PASS_THRESHOLD,
                    "rewrite" if should_rewrite else "publish",
                )

if __name__ == "__main__":
    asyncio.run(main())
