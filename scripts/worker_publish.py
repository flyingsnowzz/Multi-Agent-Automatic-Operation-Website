#!/usr/bin/env python3
"""SEO/pre-publish worker: validate article, generate SEO, then queue image work."""

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
logger = logging.getLogger("worker.publish")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from scripts.publish_common import fill_article_content, slugify, update_audit_seo, validate_publish_prerequisites
from scripts.redis_pipeline import (
    GROUP_PUBLISH,
    STREAM_IMAGE,
    STREAM_PUBLISH,
    ack_message,
    get_redis,
    handle_failure,
    read_group_messages,
    recover_pending,
    setup_streams,
)
from scripts.prompt_db_logger import log_agent_prompt


CONSUMER = f"publish-{os.getpid()}"


def parse_args():
    parser = argparse.ArgumentParser(description="SEO/pre-publish worker for Redis pipeline.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="传递真实发布意图给下游 CMS worker")
    mode.add_argument("--dry-run", action="store_true", help="传递 dry-run 发布意图给下游 CMS worker")
    parser.add_argument("--once", action="store_true", help="只处理一条消息后退出")
    parser.add_argument("--max-messages", type=int, default=0, help="最多处理 N 条消息，0 表示持续运行")
    parser.add_argument("--block-ms", type=int, default=5000, help="Redis 阻塞读取毫秒数")
    return parser.parse_args()


async def main():
    args = parse_args()
    dry_run = not args.publish
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_PUBLISH, GROUP_PUBLISH, CONSUMER)
    logger.info("started dry_run=%s", dry_run)
    processed_count = 0

    while True:
        try:
            msgs = await read_group_messages(
                r,
                group=GROUP_PUBLISH,
                consumer=CONSUMER,
                stream=STREAM_PUBLISH,
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
                        stream=STREAM_PUBLISH,
                        group=GROUP_PUBLISH,
                        msg_id=msg_id,
                        item={"raw_data": fields.get("data", "")},
                        stage="publish_parse",
                        error=str(exc),
                        max_retries=0,
                    )
                    continue

                item = await fill_article_content(item)
                title = item.get("title", "")
                content = item.get("content_md") or item.get("content") or item.get("description", "")
                try:
                    validate_publish_prerequisites(item, title=title, content=content)
                    from agents.seo_agent import SEOAgent

                    s = await SEOAgent().execute(
                        keyword_mode="v2",
                        article={"title": title, "content_md": content, "meta_description": "", "slug": ""},
                        topic=item,
                        page_info={"slug": slugify(title), "category": "news"},
                        dry_run=True,
                    )
                    seo = s if isinstance(s, dict) else {}
                    keywords = seo.get("keyword_result", {}).get("keywords", []) if isinstance(seo, dict) else []
                    meta_title = seo.get("meta_title", "") if isinstance(seo, dict) else ""
                    meta_desc = seo.get("meta_description", "") if isinstance(seo, dict) else ""
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="publish",
                        agent_name="SEOAgent",
                        prompt_type="seo_article_context",
                        prompt_text=None,
                        input_payload={
                            "article": {"title": title, "content_md": content},
                            "topic": item,
                            "page_info": {"slug": slugify(title), "category": "news"},
                        },
                        output_payload={"meta_title": meta_title, "meta_description": meta_desc, "keywords": keywords},
                        model_name=os.environ.get("SEO_AGENT_MODEL", ""),
                    )
                    await update_audit_seo(
                        item.get("article_id"),
                        meta_title=meta_title,
                        meta_desc=meta_desc,
                        keywords=keywords,
                    )
                    item.update(
                        {
                            "seo_meta_title": meta_title,
                            "seo_meta_description": meta_desc,
                            "seo_keywords": keywords,
                            "publish_dry_run": dry_run,
                        }
                    )
                    await r.xadd(STREAM_IMAGE, {"data": json.dumps(item, ensure_ascii=False)})
                    await ack_message(r, STREAM_PUBLISH, GROUP_PUBLISH, msg_id)
                    processed_count += 1
                    logger.info("id=%s SEO ready → image", item.get("article_id"))
                except Exception as exc:
                    logger.exception("publish/seo error")
                    await handle_failure(
                        r,
                        stream=STREAM_PUBLISH,
                        group=GROUP_PUBLISH,
                        msg_id=msg_id,
                        item=item,
                        stage="publish_seo",
                        error=str(exc),
                    )
                    continue
                if args.once or (args.max_messages and processed_count >= args.max_messages):
                    await r.aclose()
                    return 0
    await r.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
