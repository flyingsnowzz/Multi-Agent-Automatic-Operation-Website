#!/usr/bin/env python3
"""Image worker: make sure every publishable article has a cover image.

Beginner mental model:
    CMS publishing needs a featured image. This worker is responsible for making
    sure that image exists before the final CMS worker runs.

Two image paths:
    Direct/forwarded article:
        Reuse the source image from the crawler. Do not generate a new cover.
    Rewritten article:
        Generate a new cover using the configured IMAGE_PROVIDER.

Why image is a separate worker:
    Image providers are often slow, expensive, or quota-limited. If image work
    lived inside CMS publishing, articles could be published before images are
    ready or CMS workers could be blocked by image failures.

Input stream:
    pipeline:image

Main work:
    - direct/forwarded articles reuse the source crawler image
    - rewritten articles generate a new cover through IMAGE_PROVIDER
    - write image_url/image_local_path to pipeline_audit

Output:
    - valid image -> pipeline:cms
    - missing/failed image -> deadletter; CMS will not publish it

Common confusion:
    image_url is often the original crawler image for forwarded articles. That
    is expected. A generated image is only required for rewritten articles.
"""

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
ROOT = Path(__file__).resolve().parents[2]
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
from legacy.redis_pipeline.redis_pipeline import (
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


# Image work is slow/quota-sensitive, so it has its own scalable consumer group.
CONSUMER = f"image-{os.getpid()}"


def parse_args():
    parser = argparse.ArgumentParser(description="Image worker for Redis pipeline.")
    # --once / --max-messages are useful when manually testing one article
    # without starting the whole supervisor.
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

    # Long-running image worker loop. Image providers may be slow, so multiple
    # image workers can run without blocking SEO or CMS workers.
    while True:
        try:
            # Read an SEO-ready article. It still cannot be published until this
            # worker supplies a featured image.
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
                # Malformed image payloads cannot be retried successfully.
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
                    # Image work is isolated because providers can be slow,
                    # expensive, or quota-blocked. CMS will not run until this
                    # worker has a valid featured image.
                    # Check DB first so reruns do not regenerate an image that
                    # already exists for this article.
                    existing_cover = await fetch_existing_cover(item.get("article_id"))
                    # source_image is the cover from the crawler/original site.
                    # Direct/forwarded articles should normally reuse this.
                    source_image = (item.get("source_image") or item.get("image") or item.get("cover_image") or "").strip()
                    # The cover decision is deliberately computed before any
                    # provider call so forwarded articles never spend image
                    # generation quota by accident.
                    # cover_decision centralizes the important rule:
                    # forwarded/direct articles reuse source covers; rewritten
                    # articles generate new covers.
                    cover = cover_decision(item, existing_cover=existing_cover, source_image=source_image, title=title)
                    # cover_decision returns a small plan:
                    # - should_generate: call image provider or not
                    # - image_prompt: prompt only needed for generated covers
                    # - image_url/local_path: reusable cover if already known
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
                        # Only rewritten articles should generate a new cover.
                        # Direct/forwarded articles should reuse the source
                        # cover to preserve the original article presentation.
                        from agents.image_agent.tools.provider_factory import get_image_provider

                        # Provider is selected by IMAGE_PROVIDER in .env. The
                        # returned object has a common generate(prompt, n) API.
                        # This keeps worker_image.py independent from Coze,
                        # OpenAI, Seedance, or any future image provider.
                        cp = get_image_provider()
                        try:
                            img = await cp.generate(prompt=image_prompt, n=1)
                        finally:
                            close = getattr(cp, "close", None)
                            if close:
                                await close()
                        # Providers return a list because some support n>1. We
                        # only request/use the first cover image for each article.
                        image_item = (img.get("images") or [{}])[0] if img.get("success") and img.get("images") else {}
                        image_url = image_item.get("url", "")
                        image_local_path = image_item.get("local_path", "")
                        if not image_url and not image_local_path:
                            image_error = str(img.get("error") or "no_images_generated")
                            if "coze_http_402" in image_error:
                                raise PermanentPublishError(f"image_generation_failed:{image_error}")
                            raise RuntimeError(f"image_generation_failed:{image_error}")

                    featured_image = image_local_path or image_url
                    # Do not push to CMS unless a usable image exists. This is
                    # the guard that prevents image-less publishing.
                    # For rewritten articles, missing cover is a hard block.
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
                    # Persist image fields after validation. If validation
                    # failed, CMS never sees this article.
                    await update_audit_image(
                        item.get("article_id"),
                        image_url=image_url,
                        image_local_path=image_local_path,
                    )
                    # Put the chosen cover back into the Redis payload so CMS
                    # does not need to recalculate the cover decision.
                    item.update(
                        {
                            "image_url": image_url,
                            "image_local_path": image_local_path,
                            "featured_image": featured_image,
                        }
                    )
                    # Image is ready, so the article can now enter the final CMS
                    # stage. CMS will still run its own final checks.
                    await r.xadd(STREAM_CMS, {"data": json.dumps(item, ensure_ascii=False)})
                    await ack_message(r, STREAM_IMAGE, GROUP_IMAGE, msg_id)
                    processed_count += 1
                    logger.info("id=%s image ready → cms", item.get("article_id"))
                except Exception as exc:
                    logger.exception("image error")
                    await update_audit_status(item.get("article_id"), "image_blocked")
                    # Quota/auth failures such as coze_http_402 are treated as
                    # permanent; other errors can retry according to Redis policy.
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
