#!/usr/bin/env python3
"""Shared helpers for Redis publish/image/CMS workers."""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any, Dict

from dotenv import load_dotenv
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
logger = logging.getLogger(__name__)


class PermanentPublishError(RuntimeError):
    pass


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def preflight_publish_config(dry_run: bool) -> None:
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


def is_forwarded_article(item: Dict[str, Any]) -> bool:
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


def cover_decision(item: Dict[str, Any], *, existing_cover: Dict[str, Any], source_image: str, title: str) -> Dict[str, Any]:
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


async def fill_article_content(item: Dict[str, Any]) -> Dict[str, Any]:
    has_content = bool(item.get("content_md") or item.get("content") or item.get("description"))
    has_source_image = bool(item.get("source_image") or item.get("image") or item.get("cover_image"))
    if (has_content and has_source_image) or not item.get("article_id"):
        return item
    try:
        import aiomysql

        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
            charset="utf8mb4",
            minsize=1,
            maxsize=1,
        )
        async with pool.acquire() as c:
            async with c.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT title, description, original_url, image FROM crawler_news_main WHERE id=%s LIMIT 1",
                    (item.get("article_id"),),
                )
                row = await cur.fetchone()
        pool.close()
        await pool.wait_closed()
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


async def fetch_existing_cover(article_id: Any) -> Dict[str, Any]:
    if not article_id:
        return {}
    try:
        import aiomysql

        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
            charset="utf8mb4",
            minsize=1,
            maxsize=1,
        )
        async with pool.acquire() as c:
            async with c.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT image_url, image_local_path "
                    "FROM pipeline_audit WHERE article_id=%s "
                    "AND (NULLIF(image_url, '') IS NOT NULL OR NULLIF(image_local_path, '') IS NOT NULL) "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (article_id,),
                )
                row = await cur.fetchone()
        pool.close()
        await pool.wait_closed()
        return row or {}
    except Exception as e:
        logger.warning("existing cover lookup failed: %s", e)
        return {}


async def update_audit_seo(article_id: Any, *, meta_title: str, meta_desc: str, keywords, status: str = "seo_ready") -> None:
    if not article_id:
        return
    try:
        import aiomysql

        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
            charset="utf8mb4",
            minsize=1,
            maxsize=1,
        )
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
        pool.close()
        await pool.wait_closed()
    except Exception:
        logger.exception("seo audit write error")


async def update_audit_image(article_id: Any, *, image_url: str, image_local_path: str, status: str = "image_ready") -> None:
    if not article_id:
        return
    try:
        import aiomysql

        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
            charset="utf8mb4",
            minsize=1,
            maxsize=1,
        )
        async with pool.acquire() as c:
            async with c.cursor() as cur:
                await cur.execute(
                    "UPDATE pipeline_audit SET image_url=%s, image_local_path=%s, cms_status=%s WHERE article_id=%s",
                    (image_url, image_local_path, status, article_id),
                )
            await c.commit()
        pool.close()
        await pool.wait_closed()
    except Exception:
        logger.exception("image audit write error")


async def update_audit_cms(article_id: Any, *, cms_r: Dict[str, Any], image_url: str, image_local_path: str, meta_title: str, meta_desc: str, keywords) -> None:
    if not article_id:
        return
    try:
        import aiomysql

        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
            charset="utf8mb4",
            minsize=1,
            maxsize=3,
        )
        async with pool.acquire() as c:
            async with c.cursor() as cur:
                await cur.execute(
                    "UPDATE pipeline_audit SET image_url=%s, image_local_path=%s, "
                    "seo_meta_title=%s, seo_meta_description=%s, seo_keywords=%s, "
                    "cms_status=%s, cms_article_id=%s, cms_article_url=%s "
                    "WHERE article_id=%s",
                    (
                        image_url,
                        image_local_path,
                        meta_title,
                        meta_desc,
                        json.dumps(keywords or [], ensure_ascii=False),
                        cms_r.get("status"),
                        cms_r.get("article_id"),
                        cms_r.get("article_url"),
                        article_id,
                    ),
                )
            await c.commit()
        pool.close()
        await pool.wait_closed()
    except Exception:
        logger.exception("audit write error")


async def update_audit_status(article_id: Any, status: str) -> None:
    if not article_id:
        return
    try:
        import aiomysql

        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"],
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
            charset="utf8mb4",
            minsize=1,
            maxsize=1,
        )
        async with pool.acquire() as c:
            async with c.cursor() as cur:
                await cur.execute("UPDATE pipeline_audit SET cms_status=%s WHERE article_id=%s", (status, article_id))
            await c.commit()
        pool.close()
        await pool.wait_closed()
    except Exception:
        logger.exception("audit status update error")


def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title)
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "-", s)[:60].strip("-") or "article"


def validate_publish_prerequisites(item: Dict[str, Any], *, title: str, content: str) -> None:
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


def validate_cover_ready(item: Dict[str, Any], cover: Dict[str, Any], *, featured_image: str) -> None:
    if cover.get("is_forwarded"):
        return
    if not str(featured_image or "").strip():
        raise RuntimeError("rewritten_cover_missing")
