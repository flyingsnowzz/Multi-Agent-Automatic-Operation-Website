#!/usr/bin/env python3
"""Redis Streams Pipeline — 共享连接 / Stream 名称 / 工具函数"""

import json, logging, os
import redis.asyncio as redis
from typing import Dict, Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

STREAM_SCORING  = "pipeline:scoring"
STREAM_QUALITY  = "pipeline:quality"
STREAM_REWRITE  = "pipeline:rewrite"
STREAM_PUBLISH  = "pipeline:publish"
STREAM_DEADLETTER = "pipeline:deadletter"

GROUP_SCORING = "scoring-workers"
GROUP_QUALITY = "quality-workers"
GROUP_REWRITE = "rewrite-workers"
GROUP_PUBLISH = "publish-workers"

WORKERS_SCORING = int(os.environ.get("REDIS_SCORING_WORKERS", "4"))
WORKERS_QUALITY = int(os.environ.get("REDIS_QUALITY_WORKERS", "8"))
WORKERS_REWRITE = int(os.environ.get("REDIS_REWRITE_WORKERS", "32"))
WORKERS_PUBLISH = int(os.environ.get("REDIS_PUBLISH_WORKERS", "4"))
BATCH_SCORING   = int(os.environ.get("REDIS_BATCH_SCORING", "30"))
MAX_RETRIES = int(os.environ.get("REDIS_MAX_RETRIES", "3"))
PENDING_IDLE_MS = int(os.environ.get("REDIS_PENDING_IDLE_MS", "300000"))
PENDING_CLAIM_COUNT = int(os.environ.get("REDIS_PENDING_CLAIM_COUNT", "100"))


async def get_redis() -> redis.Redis:
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        password = os.environ.get("REDIS_PASSWORD") or None
        parsed = urlsplit(redis_url)
        if password and "@" not in parsed.netloc:
            auth = f":{quote(password, safe='')}@"
            redis_url = urlunsplit((parsed.scheme, auth + parsed.netloc, parsed.path, parsed.query, parsed.fragment))
        return redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT", "5")),
            socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT", "30")),
        )

    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        db=int(os.environ.get("REDIS_DB", "0")),
        password=os.environ.get("REDIS_PASSWORD") or None,
        ssl=os.environ.get("REDIS_SSL", "").strip().lower() in {"1", "true", "yes"},
        socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT", "5")),
        socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT", "30")),
        decode_responses=True,
    )


async def setup_streams(r: redis.Redis):
    streams = [STREAM_SCORING, STREAM_QUALITY, STREAM_REWRITE, STREAM_PUBLISH]
    groups  = [GROUP_SCORING, GROUP_QUALITY, GROUP_REWRITE, GROUP_PUBLISH]
    for stream, group in zip(streams, groups):
        try:
            await r.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise


async def push_article(r: redis.Redis, stream: str, article: Dict[str, Any]):
    await r.xadd(stream, {"data": json.dumps(article, ensure_ascii=False)})


async def ack_message(r: redis.Redis, stream: str, group: str, msg_id: str):
    await r.xack(stream, group, msg_id)


async def read_group_messages(
    r: redis.Redis,
    *,
    group: str,
    consumer: str,
    stream: str,
    count: int = 1,
    block: int = 5000,
):
    """Read pending messages for this consumer first, then read new stream messages."""
    pending = await r.xreadgroup(group, consumer, {stream: "0"}, count=count)
    if _stream_message_count(pending) > 0:
        return pending
    return await r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block)


async def handle_failure(
    r: redis.Redis,
    *,
    stream: str,
    group: str,
    msg_id: str,
    item: Dict[str, Any],
    stage: str,
    error: str,
    retry_stream: Optional[str] = None,
    max_retries: Optional[int] = None,
) -> None:
    """Retry a failed message, then move it to deadletter after the limit."""
    retry_limit = MAX_RETRIES if max_retries is None else int(max_retries)
    failed_item = dict(item)
    retry_count = int(failed_item.get("retry_count") or 0) + 1
    failed_item["retry_count"] = retry_count
    failed_item["last_error"] = str(error)[:1000]
    failed_item["failed_stage"] = stage

    if retry_count <= retry_limit:
        target_stream = retry_stream or stream
        await r.xadd(target_stream, {"data": json.dumps(failed_item, ensure_ascii=False)})
        logger.warning(
            "redis message retry scheduled stage=%s stream=%s target=%s msg_id=%s retry=%s/%s error=%s",
            stage,
            stream,
            target_stream,
            msg_id,
            retry_count,
            retry_limit,
            str(error)[:200],
        )
    else:
        await r.xadd(
            STREAM_DEADLETTER,
            {
                "stage": stage,
                "source_stream": stream,
                "source_msg_id": msg_id,
                "error": str(error)[:1000],
                "data": json.dumps(failed_item, ensure_ascii=False),
            },
        )
        logger.error(
            "redis message moved to deadletter stage=%s stream=%s msg_id=%s retry=%s/%s error=%s",
            stage,
            stream,
            msg_id,
            retry_count,
            retry_limit,
            str(error)[:200],
        )
    await ack_message(r, stream, group, msg_id)


async def recover_pending(r: redis.Redis, stream: str, group: str, consumer: str):
    try:
        pending_info = await r.xpending(stream, group)
        pending_count = _pending_message_count(pending_info)
        if pending_count > 0:
            claimed = await r.xautoclaim(
                stream,
                group,
                consumer,
                min_idle_time=PENDING_IDLE_MS,
                count=PENDING_CLAIM_COUNT,
            )
            claimed_count = _claimed_message_count(claimed)
            logger.warning(
                "redis pending messages claimed stream=%s group=%s consumer=%s claimed=%s pending=%s idle_ms=%s",
                stream,
                group,
                consumer,
                claimed_count,
                pending_count,
                PENDING_IDLE_MS,
            )
            return claimed
    except Exception:
        logger.exception("redis pending recovery failed stream=%s group=%s consumer=%s", stream, group, consumer)
    return None


def _claimed_message_count(claimed: Any) -> int:
    """Return claimed message count across redis-py xautoclaim response variants."""
    if not claimed:
        return 0
    if isinstance(claimed, dict):
        messages = claimed.get("messages") or claimed.get("entries") or []
        return len(messages)
    if isinstance(claimed, (list, tuple)) and len(claimed) >= 2:
        messages = claimed[1] or []
        return len(messages)
    return 0


def _pending_message_count(pending_info: Any) -> int:
    """Return pending count across redis-py xpending response variants."""
    if not pending_info:
        return 0
    if isinstance(pending_info, dict):
        return int(pending_info.get("pending") or 0)
    if isinstance(pending_info, (list, tuple)) and pending_info:
        return int(pending_info[0] or 0)
    return 0


def _stream_message_count(messages: Any) -> int:
    """Return total stream entries across redis-py xread/xreadgroup variants."""
    if not messages:
        return 0
    if isinstance(messages, dict):
        return sum(len(entries or []) for entries in messages.values())
    if isinstance(messages, (list, tuple)):
        total = 0
        for stream_messages in messages:
            if isinstance(stream_messages, dict):
                total += len(stream_messages.get("messages") or stream_messages.get("entries") or [])
            elif isinstance(stream_messages, (list, tuple)) and len(stream_messages) >= 2:
                total += len(stream_messages[1] or [])
        return total
    return 0
