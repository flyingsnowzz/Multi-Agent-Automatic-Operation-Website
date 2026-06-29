#!/usr/bin/env python3
"""Redis Streams Pipeline — 共享连接 / Stream 名称 / 工具函数"""

import json, os
import redis.asyncio as redis
from typing import Dict, Any

STREAM_SCORING  = "pipeline:scoring"
STREAM_QUALITY  = "pipeline:quality"
STREAM_REWRITE  = "pipeline:rewrite"
STREAM_PUBLISH  = "pipeline:publish"

GROUP_SCORING = "scoring-workers"
GROUP_QUALITY = "quality-workers"
GROUP_REWRITE = "rewrite-workers"
GROUP_PUBLISH = "publish-workers"

WORKERS_SCORING = 4
WORKERS_QUALITY = 8
WORKERS_REWRITE = 32
WORKERS_PUBLISH = 4
BATCH_SCORING   = 30


async def get_redis() -> redis.Redis:
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        db=int(os.environ.get("REDIS_DB", "0")),
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


async def recover_pending(r: redis.Redis, stream: str, group: str, consumer: str):
    try:
        pending_info = await r.xpending(stream, group)
        if pending_info and pending_info.get("pending", 0) > 0:
            claimed = await r.xautoclaim(stream, group, consumer, min_idle_time=300000, count=100)
            return claimed
    except Exception:
        pass
    return None
