#!/usr/bin/env python3
import argparse, asyncio, json, os, re, sys, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker.publish")
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_PUBLISH,
    GROUP_PUBLISH, ack_message, recover_pending, handle_failure, read_group_messages)
from scripts.prompt_db_logger import log_agent_prompt
import redis.asyncio as redis
import unicodedata

CONSUMER = f"publish-{os.getpid()}"

class PermanentPublishError(RuntimeError):
    pass

def parse_args():
    parser = argparse.ArgumentParser(description="Publish worker for Redis pipeline.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="允许真实发布，仍需 CMS_ENABLE_REAL_PUBLISH=true")
    mode.add_argument("--dry-run", action="store_true", help="只生成发布 payload，不真实发布")
    parser.add_argument("--once", action="store_true", help="只处理一条消息后退出")
    parser.add_argument("--max-messages", type=int, default=0, help="最多处理 N 条消息，0 表示持续运行")
    parser.add_argument("--block-ms", type=int, default=5000, help="Redis 阻塞读取毫秒数")
    return parser.parse_args()

def env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

def preflight_publish_config(dry_run):
    if dry_run:
        logger.info("publish mode: dry-run")
        return
    missing = []
    if not env_flag("CMS_ENABLE_REAL_PUBLISH", False):
        missing.append("CMS_ENABLE_REAL_PUBLISH=true")
    if not (os.environ.get("CMS_API_URL") or os.environ.get("CMS_BASE_URL")):
        missing.append("CMS_API_URL")
    auth_ok = bool(os.environ.get("CMS_API_KEY") or (os.environ.get("CMS_USERNAME") and os.environ.get("CMS_PASSWORD")))
    if not auth_ok:
        missing.append("CMS_API_KEY 或 CMS_USERNAME/CMS_PASSWORD")
    if missing:
        raise RuntimeError("真实发布配置不完整: " + ", ".join(missing))
    logger.info("publish mode: real publish")

def is_forwarded_article(item):
    """Return True for direct-publish/forwarded articles that were not rewritten."""
    rewritten_markers = (
        "content_md",
        "generated_content_md",
        "edited_content_md",
        "generated_title",
        "edited_title",
        "quality_after",
    )
    return not any(item.get(key) for key in rewritten_markers)

def cover_decision(item, *, existing_cover, source_image, title):
    """Decide whether to reuse an image or generate a new cover."""
    forwarded = is_forwarded_article(item)
    if forwarded:
        if source_image:
            return {
                "image_prompt": "复用原文封面",
                "image_url": source_image,
                "image_local_path": "",
                "should_generate": False,
                "reason": "forwarded_source_cover",
                "is_forwarded": True,
            }
        return {
            "image_prompt": "转发文章无原文封面",
            "image_url": "",
            "image_local_path": "",
            "should_generate": False,
            "reason": "forwarded_missing_source_cover",
            "is_forwarded": True,
        }

    return {
        "image_prompt": f"新闻配图: {title}",
        "image_url": "",
        "image_local_path": "",
        "should_generate": True,
        "reason": "rewritten_generate_cover",
        "is_forwarded": False,
    }

async def fill_article_content(item):
    has_content = bool(item.get("content_md") or item.get("content") or item.get("description"))
    has_source_image = bool(item.get("source_image") or item.get("image") or item.get("cover_image"))
    if (has_content and has_source_image) or not item.get("article_id"):
        return item
    try:
        import aiomysql
        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=1)
        async with pool.acquire() as c:
            async with c.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT title, description, original_url, image FROM crawler_news_main WHERE id=%s LIMIT 1",
                    (item.get("article_id"),))
                row = await cur.fetchone()
        pool.close(); await pool.wait_closed()
        if row:
            item["title"] = item.get("title") or row.get("title", "")
            if not has_content:
                item["description"] = row.get("description", "") or ""
                item["content"] = item["description"]
            item["source_url"] = item.get("source_url") or row.get("original_url", "")
            item["source_image"] = item.get("source_image") or row.get("image", "")
    except Exception as e:
        logger.warning("content backfill failed: %s", e)
    return item

async def fetch_existing_cover(article_id):
    if not article_id:
        return {}
    try:
        import aiomysql
        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=1)
        async with pool.acquire() as c:
            async with c.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT image_url, image_local_path "
                    "FROM pipeline_audit WHERE article_id=%s "
                    "AND (NULLIF(image_url, '') IS NOT NULL OR NULLIF(image_local_path, '') IS NOT NULL) "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (article_id,))
                row = await cur.fetchone()
        pool.close(); await pool.wait_closed()
        return row or {}
    except Exception as e:
        logger.warning("existing cover lookup failed: %s", e)
        return {}

async def update_audit_seo(article_id, *, meta_title, meta_desc, keywords, status="seo_ready"):
    if not article_id:
        return
    try:
        import aiomysql
        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=1)
        async with pool.acquire() as c:
            async with c.cursor() as cur:
                await cur.execute(
                    "UPDATE pipeline_audit SET seo_meta_title=%s, seo_meta_description=%s, "
                    "seo_keywords=%s, cms_status=%s WHERE article_id=%s",
                    (
                        meta_title,
                        meta_desc,
                        json.dumps(keywords or [], ensure_ascii=False),
                        status,
                        article_id,
                    ),
                )
            await c.commit()
        pool.close(); await pool.wait_closed()
    except Exception:
        logger.exception("seo audit write error")

def slugify(title):
    s = unicodedata.normalize("NFKD", title)
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "-", s)[:60].strip("-") or "article"

def validate_publish_prerequisites(item, *, title, content):
    missing = []
    if not item.get("article_id"):
        missing.append("article_id")
    if not str(title or "").strip():
        missing.append("title")
    if not str(content or "").strip():
        missing.append("content")
    if not is_forwarded_article(item):
        if item.get("quality_after") is None:
            missing.append("quality_after")
        if not (item.get("content_md") or item.get("edited_content_md") or item.get("generated_content_md")):
            missing.append("rewritten_content")
    if missing:
        raise RuntimeError("publish_prerequisite_missing:" + ",".join(missing))

def validate_cover_ready(item, cover, *, featured_image):
    if cover.get("is_forwarded"):
        return
    if not str(featured_image or "").strip():
        raise RuntimeError("rewritten_cover_missing")

async def main():
    args = parse_args()
    dry_run = not args.publish
    try:
        preflight_publish_config(dry_run)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 2
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
            await asyncio.sleep(5); continue
        if not msgs:
            if args.once or (args.max_messages and processed_count >= args.max_messages):
                break
            continue

        for stream, entries in msgs:
            for msg_id, fields in entries:
                try: item = json.loads(fields.get("data", "{}"))
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
                    s = await SEOAgent().execute(keyword_mode="v2",
                        article={"title": title, "content_md": content, "meta_description": "", "slug": ""},
                        topic=item, page_info={"slug": slugify(title), "category": "news"}, dry_run=True)
                    seo = s if isinstance(s, dict) else {}
                    kw = seo.get("keyword_result", {}).get("keywords", []) if isinstance(seo, dict) else []
                    meta_title = seo.get("meta_title", "") if isinstance(seo, dict) else ""
                    meta_desc = seo.get("meta_description", "") if isinstance(seo, dict) else ""
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="publish",
                        agent_name="SEOAgent",
                        prompt_type="seo_article_context",
                        prompt_text=None,
                        input_payload={"article": {"title": title, "content_md": content}, "topic": item, "page_info": {"slug": slugify(title), "category": "news"}},
                        output_payload={"meta_title": meta_title, "meta_description": meta_desc, "keywords": kw},
                        model_name=os.environ.get("SEO_AGENT_MODEL", ""),
                    )
                    await update_audit_seo(
                        item.get("article_id"),
                        meta_title=meta_title,
                        meta_desc=meta_desc,
                        keywords=kw,
                    )

                    existing_cover = await fetch_existing_cover(item.get("article_id"))
                    source_image = (item.get("source_image") or item.get("image") or item.get("cover_image") or "").strip()
                    cover = cover_decision(
                        item,
                        existing_cover=existing_cover,
                        source_image=source_image,
                        title=title,
                    )
                    image_prompt = cover["image_prompt"]
                    image_url = cover["image_url"]
                    image = cover["image_local_path"]
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
                        image = image_item.get("local_path", "")
                        if not image_url and not image:
                            image_error = str(img.get("error") or "no_images_generated")
                            if "coze_http_402" in image_error:
                                raise PermanentPublishError(f"image_generation_failed:{image_error}")
                            raise RuntimeError(f"image_generation_failed:{image_error}")
                    featured_image = image or image_url
                    validate_cover_ready(item, cover, featured_image=featured_image)
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="publish",
                        agent_name="ImageAgent",
                        prompt_type="cover_image_prompt",
                        prompt_text=image_prompt,
                        input_payload={
                            "title": title,
                            "is_forwarded": cover["is_forwarded"],
                            "cover_reason": cover["reason"],
                            "reuse_existing_cover": bool(image_url or image),
                            "source_image": source_image,
                        },
                        output_payload={"image_url": image_url, "image_local_path": image},
                        model_name=os.environ.get("COZE_IMAGE_MODEL", ""),
                    )

                    from agents.cms_agent import CMSAgent
                    cms_r = await CMSAgent(dry_run=dry_run).execute(
                        article={"title": title, "content_md": content,
                                 "meta": {"meta_title": meta_title, "meta_description": meta_desc},
                                 "slug": slugify(title), "featured_image_url": featured_image},
                        page_info={"category": "news", "tags": kw, "slug": slugify(title)},
                        images={"featured_image_url": featured_image, "featured_alt": title})

                    import aiomysql
                    try:
                        pool = await aiomysql.create_pool(
                            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=3)
                        async with pool.acquire() as c:
                            async with c.cursor() as cur:
                                await cur.execute(
                                    "UPDATE pipeline_audit SET image_url=%s, image_local_path=%s, "
                                    "seo_meta_title=%s, seo_meta_description=%s, seo_keywords=%s, "
                                    "cms_status=%s, cms_article_id=%s, cms_article_url=%s "
                                    "WHERE article_id=%s",
                                    (
                                        image_url,
                                        image,
                                        meta_title,
                                        meta_desc,
                                        json.dumps(kw, ensure_ascii=False),
                                        cms_r.get("status"),
                                        cms_r.get("article_id"),
                                        cms_r.get("article_url"),
                                        item.get("article_id"),
                                    ))
                            await c.commit()
                        pool.close(); await pool.wait_closed()
                    except Exception:
                        logger.exception("audit write error")
                    logger.info("%s → CMS:%s", title[:40], cms_r.get('status'))
                except Exception as exc:
                    logger.exception("publish error")
                    try:
                        import aiomysql
                        pool = await aiomysql.create_pool(
                            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=1)
                        async with pool.acquire() as c:
                            async with c.cursor() as cur:
                                await cur.execute(
                                    "UPDATE pipeline_audit SET cms_status=%s WHERE article_id=%s",
                                    ("image_blocked", item.get("article_id")),
                                )
                            await c.commit()
                        pool.close(); await pool.wait_closed()
                    except Exception:
                        logger.exception("publish failure audit status update error")
                    await handle_failure(
                        r,
                        stream=STREAM_PUBLISH,
                        group=GROUP_PUBLISH,
                        msg_id=msg_id,
                        item=item,
                        stage="publish",
                        error=str(exc),
                        max_retries=0 if isinstance(exc, PermanentPublishError) else None,
                    )
                    continue
                await ack_message(r, STREAM_PUBLISH, GROUP_PUBLISH, msg_id)
                processed_count += 1
                if args.once or (args.max_messages and processed_count >= args.max_messages):
                    await r.aclose()
                    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
