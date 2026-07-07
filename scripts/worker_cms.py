#!/usr/bin/env python3
"""CMS worker: final publish or dry-run payload generation.

Beginner mental model:
    This is the last station. Everything before this worker prepares the article
    package. This worker either builds a CMS payload in dry-run mode or sends it
    to the real CMS in publish mode.

Why there are two publish switches:
    Real publishing requires both:
        1. command/runtime mode says --publish
        2. .env says CMS_ENABLE_REAL_PUBLISH=true
    This double lock makes accidental publishing less likely.

Input stream:
    pipeline:cms

Preconditions:
    - content exists
    - SEO fields exist
    - featured image exists

Main work:
    - dry-run mode builds the CMS payload without posting
    - publish mode posts to the configured CMS, but only when
      CMS_ENABLE_REAL_PUBLISH=true and credentials are present

Common confusion:
    Dry-run is still useful. It lets you verify article content, SEO, image, and
    CMS payload shape without posting to the website.
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
logger = logging.getLogger("worker.cms")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from scripts.publish_common import (
    PermanentPublishError,
    preflight_publish_config,
    slugify,
    update_audit_cms,
    update_audit_status,
)
from scripts.redis_pipeline import (
    GROUP_CMS,
    STREAM_CMS,
    ack_message,
    get_redis,
    handle_failure,
    read_group_messages,
    recover_pending,
    setup_streams,
)


# Final Redis consumer. Keeping CMS separate makes it easy to run many SEO/image
# workers while limiting real publishing to one cautious worker.
CONSUMER = f"cms-{os.getpid()}"


def parse_args():
    parser = argparse.ArgumentParser(description="CMS worker for Redis pipeline.")
    # Even when --publish is passed, preflight_publish_config() still requires
    # CMS_ENABLE_REAL_PUBLISH=true. This double switch prevents accidental posts.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="允许真实发布，仍需 CMS_ENABLE_REAL_PUBLISH=true")
    mode.add_argument("--dry-run", action="store_true", help="只生成发布 payload，不真实发布")
    parser.add_argument("--once", action="store_true", help="只处理一条消息后退出")
    parser.add_argument("--max-messages", type=int, default=0, help="最多处理 N 条消息，0 表示持续运行")
    parser.add_argument("--block-ms", type=int, default=5000, help="Redis 阻塞读取毫秒数")
    return parser.parse_args()


async def main():
    args = parse_args()
    dry_run = not args.publish
    try:
        # Fail fast before reading Redis if required CMS credentials/safety
        # flags are missing. Otherwise a message could be claimed then fail.
        preflight_publish_config(dry_run)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 2

    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_CMS, GROUP_CMS, CONSUMER)
    logger.info("started dry_run=%s", dry_run)
    processed_count = 0

    # Final stage loop. This is intentionally last so publishing cannot happen
    # before SEO and image checks complete.
    while True:
        try:
            # Read image-ready articles. At this point all earlier pipeline
            # stages should have written their fields into the payload.
            msgs = await read_group_messages(
                r,
                group=GROUP_CMS,
                consumer=CONSUMER,
                stream=STREAM_CMS,
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
                # Malformed CMS payloads cannot be repaired by retrying.
                try:
                    item = json.loads(fields.get("data", "{}"))
                except Exception as exc:
                    await handle_failure(
                        r,
                        stream=STREAM_CMS,
                        group=GROUP_CMS,
                        msg_id=msg_id,
                        item={"raw_data": fields.get("data", "")},
                        stage="cms_parse",
                        error=str(exc),
                        max_retries=0,
                    )
                    continue

                title = item.get("title", "")
                content = item.get("content_md") or item.get("content") or item.get("description", "")
                # These fields should have been added by worker_publish.py and
                # worker_image.py. CMS does not regenerate SEO or covers.
                meta_title = item.get("seo_meta_title", "")
                meta_desc = item.get("seo_meta_description", "")
                keywords = item.get("seo_keywords") or []
                image_url = item.get("image_url", "")
                image_local_path = item.get("image_local_path", "")
                # Prefer the explicit featured_image chosen by worker_image,
                # but allow local_path/url fallback for old pending payloads.
                featured_image = item.get("featured_image") or image_local_path or image_url
                try:
                    # Final safety check: do not publish half-finished articles.
                    # SEO/image workers must finish before CMS can run.
                    if not featured_image:
                        raise RuntimeError("cms_featured_image_missing")
                    from agents.cms_agent import CMSAgent

                    # CMSAgent handles both modes:
                    # - dry_run=True: build/validate payload only
                    # - dry_run=False: post to the configured CMS
                    # Build the exact CMS payload from prepared fields only:
                    # title/content from quality/rewrite path, SEO from publish
                    # worker, and featured image from image worker.
                    cms_r = await CMSAgent(dry_run=dry_run).execute(
                        article={
                            "title": title,
                            "content_md": content,
                            "meta": {"meta_title": meta_title, "meta_description": meta_desc},
                            "slug": slugify(title),
                            "featured_image_url": featured_image,
                        },
                        page_info={"category": "news", "tags": keywords, "slug": slugify(title)},
                        images={"featured_image_url": featured_image, "featured_alt": title},
                    )
                    # Mark final CMS result in pipeline_audit. For dry-run this
                    # records the simulated payload/result; for publish it stores
                    # the returned CMS id/url/status.
                    await update_audit_cms(
                        item.get("article_id"),
                        cms_r=cms_r,
                        image_url=image_url,
                        image_local_path=image_local_path,
                        meta_title=meta_title,
                        meta_desc=meta_desc,
                        keywords=keywords,
                    )
                    await ack_message(r, STREAM_CMS, GROUP_CMS, msg_id)
                    processed_count += 1
                    logger.info("%s → CMS:%s", title[:40], cms_r.get("status"))
                except Exception as exc:
                    logger.exception("cms error")
                    await update_audit_status(item.get("article_id"), "cms_blocked")
                    # CMS failures keep the article out of "done" state and go
                    # through retry/deadletter handling for later inspection.
                    await handle_failure(
                        r,
                        stream=STREAM_CMS,
                        group=GROUP_CMS,
                        msg_id=msg_id,
                        item=item,
                        stage="cms",
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
