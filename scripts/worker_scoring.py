#!/usr/bin/env python3
"""AI scoring worker: first Redis pipeline gate.

Beginner mental model:
    This worker is the first expensive AI step after redis_feeder.py. The feeder
    puts crawler articles into pipeline:scoring; this worker scores them and
    decides whether an article is worth sending to QualityAgent.

What this worker does:
    1. read a batch of articles from Redis stream pipeline:scoring
    2. normalize the original article body into source_content/content
    3. call summarize_crawler_topics(), which uses ScoringAgent logic
    4. write compact audit data to MySQL
    5. log long scoring reason/breakdown to logs/agent_prompts.jsonl
    6. only push high-score articles into pipeline:quality
    7. ACK Redis messages after all of the above succeeds

Important data boundary:
    MySQL should stay small. ai_score and source image can be stored there.
    Prompt-like/debug-heavy values such as scoring_reason and score_breakdown go
    to JSONL logs through log_agent_prompt(), not into pipeline_audit columns.

Input stream:
    pipeline:scoring

Output stream:
    pipeline:quality, but only when ai_score >= AI_SCORE_THRESHOLD.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import redis.asyncio as redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker.scoring")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from agents.scoring_agent.scoring_summary import summarize_crawler_topics
from scripts.pipeline_text import article_source_content
from scripts.prompt_db_logger import log_agent_prompt
from scripts.redis_pipeline import (
    BATCH_SCORING,
    GROUP_SCORING,
    STREAM_QUALITY,
    STREAM_SCORING,
    ack_message,
    get_redis,
    handle_failure,
    read_group_messages,
    recover_pending,
    setup_streams,
)


# Consumer name is visible in Redis pending/claimed logs. Including the process
# id makes it easy to tell which local worker process owns a stuck message.
CONSUMER = f"scorer-{os.getpid()}"

# First gate: only articles with ai_score >= this value go to QualityAgent.
# Change it in .env, not in code, so production and local tests can differ.
AI_SCORE_THRESHOLD = float(os.environ.get("AI_SCORE_THRESHOLD", "75"))


async def _open_audit_pool():
    """Create the tiny MySQL pool used for scoring audit writes.

    The worker opens this inside the batch so a DB reconnect is enough to heal
    most transient MySQL problems. maxsize=3 is plenty because one scoring
    worker writes audit rows sequentially for the current batch.
    """

    import aiomysql

    return await aiomysql.create_pool(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
        charset="utf8mb4",
        minsize=1,
        maxsize=3,
    )


async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_SCORING, GROUP_SCORING, CONSUMER)
    logger.info("启动，批量 %d 篇", BATCH_SCORING)

    # Long-running worker loop. In production this never exits by itself; the
    # supervisor or Docker stops it when the service is stopped.
    while True:
        try:
            # read_group_messages() is the shared helper that reads from a Redis
            # consumer group. It can also recover messages that were claimed by
            # a worker that crashed before ACK.
            msgs = await read_group_messages(
                r,
                group=GROUP_SCORING,
                consumer=CONSUMER,
                stream=STREAM_SCORING,
                count=BATCH_SCORING,
                block=5000,
            )
        except redis.ResponseError:
            await asyncio.sleep(5)
            continue
        if not msgs:
            continue

        # batch holds the article dicts for ScoringAgent.
        # msg_ids keeps the Redis stream/msg_id beside each article so we can
        # ACK or deadletter the exact message later.
        batch, msg_ids = [], []
        for stream, entries in msgs:
            for msg_id, fields in entries:
                try:
                    item = json.loads(fields.get("data", "{}"))
                    # Feeder payloads may contain different body field names
                    # depending on crawler history. article_source_content()
                    # normalizes them into one source body for every agent.
                    source_content = article_source_content(item)
                    if source_content:
                        # Keep both names because older agent code sometimes
                        # reads content, while newer pipeline code reads
                        # source_content to make the original body explicit.
                        item["source_content"] = source_content
                        item["content"] = source_content
                    batch.append(item)
                    msg_ids.append((stream, msg_id, item))
                except Exception as exc:
                    logger.exception("parse error")
                    # Bad JSON cannot be repaired by retrying, so it goes
                    # straight to deadletter.
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
        if not batch:
            continue

        try:
            # summarize_crawler_topics() is synchronous internally because it
            # uses urllib/thread pools. Run it in a thread so the async Redis
            # worker can stay responsive.
            #
            # ai_concurrency=4 means the scoring agent may score up to 4
            # articles in parallel inside this batch. It is separate from the
            # number of worker_scoring.py processes.
            result = await asyncio.to_thread(
                summarize_crawler_topics,
                batch,
                use_ai=True,
                ai_concurrency=4,
            )

            # Scoring output contains article_scores keyed by article_id. Keep a
            # lookup to recover source_content/source_image from the original
            # Redis payload when building the next-stage payload.
            articles_by_id = {str(item.get("id") or item.get("article_id")): item for item in batch}

            try:
                # Audit writes are best-effort per article. The large scoring
                # reason/breakdown goes to JSONL via log_agent_prompt(); MySQL
                # only stores compact fields needed for filtering and review.
                pool = await _open_audit_pool()
                for se in result.get("article_scores", []):
                    if se.get("overall_score") is None:
                        continue

                    original = articles_by_id.get(str(se.get("article_id"))) or {}
                    source_content = article_source_content(original)
                    source_url = (
                        original.get("source_url")
                        or original.get("original_url")
                        or se.get("source_url")
                        or ""
                    )

                    # Full scoring explanation is useful for debugging, but it
                    # is too large/noisy for pipeline_audit. This JSONL file is
                    # the place to inspect prompts and agent outputs.
                    await log_agent_prompt(
                        article_id=se.get("article_id"),
                        stage="scoring",
                        agent_name="ScoringAgent",
                        prompt_type="scoring_result",
                        prompt_text=None,
                        input_payload={
                            "title": se.get("title"),
                            "source_url": source_url,
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

                    # Store the crawler/source cover early. Rewritten articles
                    # may later replace it with a generated cover; direct or
                    # forwarded articles can reuse it.
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
                                    (se.get("article_id"), se.get("overall_score"), source_image),
                                )
                            await c.commit()
                    except Exception:
                        logger.exception("pipeline_audit write error")

                    try:
                        async with pool.acquire() as c:
                            async with c.cursor() as cur:
                                # Mark source article as used after scoring so
                                # redis_feeder will not enqueue it forever.
                                await cur.execute(
                                    "UPDATE crawler_news_main "
                                    "SET article_overall_score=%s, article_scored_at=NOW(), "
                                    "article_usage_status='used', article_used_at=NOW() "
                                    "WHERE id=%s",
                                    (se.get("overall_score"), se.get("article_id")),
                                )
                            await c.commit()
                    except Exception:
                        logger.exception("crawler_news_main write error")
                pool.close()
                await pool.wait_closed()
            except Exception:
                logger.exception("MySQL audit write error")

            # Route each scored article. Articles below the threshold stop here,
            # which saves all later QualityAgent/rewrite/image/CMS cost.
            for se in result.get("article_scores", []):
                if se.get("overall_score") is None:
                    continue

                ai_score = float(se["overall_score"])
                if ai_score < AI_SCORE_THRESHOLD:
                    logger.info(
                        "id=%s AI=%.1f threshold=%.1f -> discard",
                        se.get("article_id"),
                        ai_score,
                        AI_SCORE_THRESHOLD,
                    )
                    continue

                original = articles_by_id.get(str(se.get("article_id"))) or {}
                source_content = article_source_content(original)
                source_url = (
                    original.get("source_url")
                    or original.get("original_url")
                    or se.get("source_url")
                    or ""
                )

                # This is the compact handoff contract to QualityAgent. Keep
                # original text and source metadata here; downstream workers
                # should not need to query prompt logs to continue.
                await r.xadd(
                    STREAM_QUALITY,
                    {
                        "data": json.dumps(
                            {
                                "article_id": se.get("article_id"),
                                "ai_score": ai_score,
                                "title": se.get("title", ""),
                                "source_url": source_url,
                                "source_image": original.get("source_image") or original.get("image", ""),
                                "description": original.get("description", ""),
                                "content": source_content,
                                "source_content": source_content,
                                "publish_date": original.get("publish_date", ""),
                            },
                            ensure_ascii=False,
                        )
                    },
                )
                logger.info(
                    "id=%s AI=%.1f threshold=%.1f -> quality",
                    se.get("article_id"),
                    ai_score,
                    AI_SCORE_THRESHOLD,
                )
        except Exception as exc:
            logger.exception("batch scoring error")
            # If the batch-level scoring call fails, retry every message in the
            # batch. handle_failure() eventually moves poison messages to
            # deadletter after REDIS_MAX_RETRIES.
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

        # ACK only after scoring, DB writes, and downstream queue pushes have
        # completed. This is what gives Redis Streams at-least-once behavior.
        for stream, msg_id, _item in msg_ids:
            try:
                await ack_message(r, stream, GROUP_SCORING, msg_id)
            except Exception:
                logger.exception("ack error")
        logger.info("batch %d done", len(batch))


if __name__ == "__main__":
    asyncio.run(main())
