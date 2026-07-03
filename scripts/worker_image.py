#!/usr/bin/env python3
"""Image worker: reuse/generate cover images, then queue CMS publishing."""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import redis.asyncio as redis
from dotenv import load_dotenv


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker.image")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from scripts.prompt_db_logger import log_agent_prompt
from scripts.publish_common import (
    PermanentPublishError,
    cover_decision,
    fetch_existing_cover,
    update_audit_image,
    update_audit_status,
    validate_cover_ready,
)
from scripts.redis_pipeline import (
    GROUP_IMAGE,
    STREAM_CMS,
    STREAM_IMAGE,
    ack_message,
    get_redis,
    handle_failure,
    read_group_messages,
    recover_pending,
    setup_streams,
)


CONSUMER = f"image-{os.getpid()}"


def parse_args():
    parser = argparse.ArgumentParser(description="Image worker for Redis pipeline.")
    parser.add_argument("--once", action="store_true", help="只处理一条消息后退出")
    parser.add_argument("--max-messages", type=int, default=0, help="最多处理 N 条消息，0 表示持续运行")
    parser.add_argument("--block-ms", type=int, default=5000, help="Redis 阻塞读取毫秒数")
    return parser.parse_args()


async def main():
    args = parse_args()
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_IMAGE, GROUP_IMAGE, CONSUMER)
    logger.info("started")
    processed_count = 0

    while True:
        try:
            msgs = await read_group_messages(
                r,
                group=GROUP_IMAGE,
                consumer=CONSUMER,
                stream=STREAM_IMAGE,
                count=1,
                block=args.block_ms,
            )
        except redis.ResponseError:
            await asyncio.sleep(5)
            continue
        if not msgs:
            if args.once or (args.max_messages and processed_count >= args.max_messages):
                break
            continue

        for stream, entries in msgs:
            for msg_id, fields in entries:
                try:
                    item = json.loads(fields.get("data", "{}"))
                except Exception as exc:
                    await handle_failure(
                        r,
                        stream=STREAM_IMAGE,
                        group=GROUP_IMAGE,
                        msg_id=msg_id,
                        item={"raw_data": fields.get("data", "")},
                        stage="image_parse",
                        error=str(exc),
                        max_retries=0,
                    )
                    continue

                title = item.get("title", "")
                try:
                    existing_cover = await fetch_existing_cover(item.get("article_id"))
                    source_image = (item.get("source_image") or item.get("image") or item.get("cover_image") or "").strip()
                    cover = cover_decision(item, existing_cover=existing_cover, source_image=source_image, title=title)
                    image_prompt = cover["image_prompt"]
                    image_url = cover["image_url"]
                    image_local_path = cover["image_local_path"]
                    if cover["reason"] == "existing_cover":
                        logger.info("id=%s reuse existing cover", item.get("article_id"))
                    elif cover["reason"] in {"source_cover", "forwarded_source_cover"}:
                        logger.info("id=%s reuse source cover", item.get("article_id"))
                    elif cover["reason"] == "forwarded_missing_source_cover":
                        logger.warning("id=%s forwarded article has no source cover; skip image generation", item.get("article_id"))
                    elif cover["should_generate"]:
                        from agents.image_agent.tools.provider_factory import get_image_provider

                        cp = get_image_provider()
                        try:
                            img = await cp.generate(prompt=image_prompt, n=1)
                        finally:
                            close = getattr(cp, "close", None)
                            if close:
                                await close()
                        image_item = (img.get("images") or [{}])[0] if img.get("success") and img.get("images") else {}
                        image_url = image_item.get("url", "")
                        image_local_path = image_item.get("local_path", "")
                        if not image_url and not image_local_path:
                            image_error = str(img.get("error") or "no_images_generated")
                            if "coze_http_402" in image_error:
                                raise PermanentPublishError(f"image_generation_failed:{image_error}")
                            raise RuntimeError(f"image_generation_failed:{image_error}")

                    featured_image = image_local_path or image_url
                    validate_cover_ready(item, cover, featured_image=featured_image)
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="image",
                        agent_name="ImageAgent",
                        prompt_type="cover_image_prompt",
                        prompt_text=image_prompt,
                        input_payload={
                            "title": title,
                            "is_forwarded": cover["is_forwarded"],
                            "cover_reason": cover["reason"],
                            "reuse_existing_cover": bool(image_url or image_local_path),
                            "source_image": source_image,
                        },
                        output_payload={"image_url": image_url, "image_local_path": image_local_path},
                        model_name=os.environ.get("COZE_IMAGE_MODEL", ""),
                    )
                    await update_audit_image(
                        item.get("article_id"),
                        image_url=image_url,
                        image_local_path=image_local_path,
                    )
                    item.update(
                        {
                            "image_url": image_url,
                            "image_local_path": image_local_path,
                            "featured_image": featured_image,
                        }
                    )
                    await r.xadd(STREAM_CMS, {"data": json.dumps(item, ensure_ascii=False)})
                    await ack_message(r, STREAM_IMAGE, GROUP_IMAGE, msg_id)
                    processed_count += 1
                    logger.info("id=%s image ready → cms", item.get("article_id"))
                except Exception as exc:
                    logger.exception("image error")
                    await update_audit_status(item.get("article_id"), "image_blocked")
                    await handle_failure(
                        r,
                        stream=STREAM_IMAGE,
                        group=GROUP_IMAGE,
                        msg_id=msg_id,
                        item=item,
                        stage="image",
                        error=str(exc),
                        max_retries=0 if isinstance(exc, PermanentPublishError) else None,
                    )
                    continue
                if args.once or (args.max_messages and processed_count >= args.max_messages):
                    await r.aclose()
                    return 0
    await r.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
