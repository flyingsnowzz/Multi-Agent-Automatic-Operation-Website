#!/usr/bin/env python3
"""Redis Streams shared infrastructure.

Beginner mental model:
    Redis is acting as the conveyor belt between modules. Each worker reads from
    one named stream and usually writes to the next named stream.

Important words:
    stream:
        A queue-like list of messages, for example pipeline:quality.
    consumer group:
        A group of workers sharing the same stream. Redis uses this to ensure
        each message is handled by one worker in that group.
    ACK:
        A worker tells Redis "I finished this message".
    pending:
        A message was delivered to a worker but not ACKed yet.
    deadletter:
        A failed message that should be inspected manually.

Every worker imports this module for:
    - Redis connection creation
    - stream and consumer-group names
    - idempotent stream/group setup
    - ACK/retry/deadletter helpers
    - pending-message recovery after worker crashes

Business logic should stay in worker_*.py files; this file should stay generic.
"""

import json, logging, os
import redis.asyncio as redis
from typing import Dict, Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# Redis Streams are the hand-off points between workers. A worker only ACKs a
# message after its DB write and next stream push succeed, which lets another
# consumer recover stuck pending messages after a crash.
#
# Stage order:
#   pipeline:scoring -> pipeline:quality -> pipeline:rewrite or pipeline:publish
#   pipeline:publish -> pipeline:image -> pipeline:cms
#   failures after retries -> pipeline:deadletter
STREAM_SCORING  = "pipeline:scoring"
STREAM_QUALITY  = "pipeline:quality"
STREAM_REWRITE  = "pipeline:rewrite"
STREAM_PUBLISH  = "pipeline:publish"
STREAM_IMAGE = "pipeline:image"
STREAM_CMS = "pipeline:cms"
STREAM_DEADLETTER = "pipeline:deadletter"

GROUP_SCORING = "scoring-workers"
GROUP_QUALITY = "quality-workers"
GROUP_REWRITE = "rewrite-workers"
GROUP_PUBLISH = "publish-workers"
GROUP_IMAGE = "image-workers"
GROUP_CMS = "cms-workers"

# These REDIS_* worker constants are legacy/shared defaults. The current local
# supervisor mostly uses PIPELINE_* variables in run_redis_workers.py. Keep these
# here because some scripts/tests import BATCH_SCORING/MAX_RETRIES from this
# infrastructure module.
WORKERS_SCORING = int(os.environ.get("REDIS_SCORING_WORKERS", "4"))
WORKERS_QUALITY = int(os.environ.get("REDIS_QUALITY_WORKERS", "8"))
WORKERS_REWRITE = int(os.environ.get("REDIS_REWRITE_WORKERS", "32"))
WORKERS_PUBLISH = int(os.environ.get("REDIS_PUBLISH_WORKERS", "4"))
BATCH_SCORING   = int(os.environ.get("REDIS_BATCH_SCORING", "30"))
MAX_RETRIES = int(os.environ.get("REDIS_MAX_RETRIES", "3"))
PENDING_IDLE_MS = int(os.environ.get("REDIS_PENDING_IDLE_MS", "300000"))
PENDING_CLAIM_COUNT = int(os.environ.get("REDIS_PENDING_CLAIM_COUNT", "100"))


async def get_redis() -> redis.Redis:
    # Prefer REDIS_URL because Docker/cloud deployments often provide a single
    # connection string. If REDIS_PASSWORD is set separately, inject it into the
    # URL when the URL itself has no password.
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        password = os.environ.get("REDIS_PASSWORD") or None
        parsed = urlsplit(redis_url)
        if password and "@" not in parsed.netloc:
            # Some deployments provide REDIS_URL=redis://host:6379/0 and a
            # separate REDIS_PASSWORD. Inject the password only when the URL
            # does not already contain user/pass information.
            auth = f":{quote(password, safe='')}@"
            redis_url = urlunsplit((parsed.scheme, auth + parsed.netloc, parsed.path, parsed.query, parsed.fragment))
        return redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT", "5")),
            socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT", "30")),
        )

    # Local-development fallback: host/port/db fields from .env.
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
    # Idempotent startup migration for Redis. New machines can call this on
    # every worker boot without manually creating streams/groups first.
    streams = [STREAM_SCORING, STREAM_QUALITY, STREAM_REWRITE, STREAM_PUBLISH, STREAM_IMAGE, STREAM_CMS]
    groups  = [GROUP_SCORING, GROUP_QUALITY, GROUP_REWRITE, GROUP_PUBLISH, GROUP_IMAGE, GROUP_CMS]
    for stream, group in zip(streams, groups):
        try:
            # id="0" means the group is allowed to read messages that already
            # exist in the stream. mkstream=True creates the stream if missing.
            await r.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                # BUSYGROUP simply means the group already exists. Anything
                # else is a real Redis setup problem and should crash startup.
                raise


async def push_article(r: redis.Redis, stream: str, article: Dict[str, Any]):
    # All pipeline messages are stored under one "data" field as JSON. This
    # keeps stream schema simple and lets the payload evolve without Redis changes.
    await r.xadd(stream, {"data": json.dumps(article, ensure_ascii=False)})


async def ack_message(r: redis.Redis, stream: str, group: str, msg_id: str):
    # ACK means "this consumer group no longer needs to process msg_id".
    # Workers should call this only after side effects have succeeded.
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
    # "0" asks Redis for messages already assigned to this consumer but not ACKed.
    # This lets a restarted worker finish its own interrupted work before reading
    # brand-new messages with ">".
    pending = await r.xreadgroup(group, consumer, {stream: "0"}, count=count)
    if _stream_message_count(pending) > 0:
        # Returning pending first prevents the same consumer from abandoning work
        # it already claimed before a restart.
        return pending
    # ">" means "give me messages this group has never delivered before".
    # block keeps workers from busy-looping when the queue is empty.
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
    # Transient failures are requeued onto the same stream with retry_count.
    # Permanent failures and exhausted retries go to deadletter for inspection.
    retry_limit = MAX_RETRIES if max_retries is None else int(max_retries)
    failed_item = dict(item)
    # retry_count lives inside the message payload because a retry is requeued as
    # a new Redis entry. The original stream entry is ACKed at the end.
    retry_count = int(failed_item.get("retry_count") or 0) + 1
    failed_item["retry_count"] = retry_count
    failed_item["last_error"] = str(error)[:1000]
    failed_item["failed_stage"] = stage

    if retry_count <= retry_limit:
        target_stream = retry_stream or stream
        # Requeue the message as a new stream entry. This is simpler than trying
        # to mutate the old Redis entry and works across all stream stages.
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
        # Deadletter keeps the original payload and error context. It is not
        # consumed automatically; it is for manual debugging/replay decisions.
        # This is where you inspect poison messages that keep failing.
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
    # Always ACK the failed original message after either requeue or deadletter.
    # Without this, Redis would keep the old failed message pending forever.
    await ack_message(r, stream, group, msg_id)


async def recover_pending(r: redis.Redis, stream: str, group: str, consumer: str):
    try:
        # Pending means Redis delivered messages to some consumer, but they were
        # never ACKed. xautoclaim moves old pending messages to this consumer so
        # a dead worker does not permanently hold work.
        pending_info = await r.xpending(stream, group)
        pending_count = _pending_message_count(pending_info)
        if pending_count > 0:
            # xautoclaim only claims messages idle longer than PENDING_IDLE_MS.
            # Fresh pending messages are probably still being processed by a
            # live worker and should not be stolen immediately.
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
