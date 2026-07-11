#!/usr/bin/env python3
"""Shared helpers for LangGraph publish/image/CMS nodes.

Beginner mental model:
    The late publish stages need many of the same small operations: validate the
    article, decide whether to reuse or generate a cover, update audit columns,
    and build slugs. Instead of copying that logic into three workers, it lives
    here.

Why this matters:
    If the LangGraph image node and CMS node each had their own version of "is
    this a forwarded article?", they could disagree.
    Keeping the rule here gives the pipeline one consistent decision.

This module keeps the late-stage graph nodes small. It owns:
    - real-publish preflight checks
    - direct/forwarded vs rewritten article detection
    - image reuse/generation decision logic
    - small MySQL updates for SEO/image/CMS audit fields
    - final publish precondition validation
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any, Dict

from dotenv import load_dotenv
from pathlib import Path

from scripts.db_config import crawler_table_config


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
logger = logging.getLogger(__name__)


class PermanentPublishError(RuntimeError):
    # Marker exception: do not retry failures that need external action, such as
    # missing credentials or image-provider quota exhaustion.
    pass


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment flag from common .env true/false spellings."""
    # Normalize common "true" spellings from .env. Anything else is false.
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def preflight_publish_config(dry_run: bool) -> None:
    """Validate real-publish credentials unless this run is explicitly dry-run."""
    # Dry-run intentionally skips CMS credential checks. This lets a new machine
    # validate payload generation before real CMS access is configured.
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
    # Rewritten articles carry generated/edited markers. If no marker exists,
    # treat this as a direct/forwarded article and reuse its source media.
    #
    # This decision is intentionally based on pipeline payload fields, not on
    # quality_score. A direct article can have high quality_score and go straight
    # to publish; a rewritten article has generated/edited content markers.
    rewritten_markers = (
        # Current rewritten body fields.
        "content_md",
        "generated_content_md",
        "edited_content_md",
        # Current rewritten title fields.
        "generated_title",
        "edited_title",
        # Rewritten articles pass through a second quality gate. This marker is
        # useful even if content fields are renamed in a future Agent response.
        "quality_after",
    )
    return not any(item.get(key) for key in rewritten_markers)


def cover_decision(item: Dict[str, Any], *, existing_cover: Dict[str, Any], source_image: str, title: str) -> Dict[str, Any]:
    """Decide whether to reuse an image or generate a new cover."""
    # This function is the single source of truth for cover-image behavior.
    # Keeping it here prevents LangGraph image/CMS nodes from disagreeing about
    # whether a cover should be generated or reused.
    forwarded = is_forwarded_article(item)
    if forwarded:
        # Forwarded/direct-publish articles should not spend image-generation
        # quota. Use the crawler/source cover when available.
        if source_image:
            return {
                "image_prompt": "复用原文封面",
                "image_url": source_image,
                "image_local_path": "",
                "should_generate": False,
                "reason": "forwarded_source_cover",
                "is_forwarded": True,
            }
        # No source cover and no rewrite markers means we cannot safely generate
        # a new editorial cover: this article is being forwarded/direct-published
        # and should preserve original presentation. validate_cover_ready/CMS
        # can decide whether missing cover is acceptable for that route.
        return {
            "image_prompt": "转发文章无原文封面",
            "image_url": "",
            "image_local_path": "",
            "should_generate": False,
            "reason": "forwarded_missing_source_cover",
            "is_forwarded": True,
        }

    existing_image_url = str(existing_cover.get("image_url") or "").strip()
    existing_image_local_path = str(existing_cover.get("image_local_path") or "").strip()
    existing_is_source_cover = bool(existing_image_url and source_image and existing_image_url == source_image)
    if existing_image_local_path or (existing_image_url and not existing_is_source_cover):
        # Rewritten articles need a newly generated cover, but reruns should
        # reuse that already-generated cover instead of paying the provider
        # again. pipeline_audit may also contain a reused source image from an
        # earlier direct-publish run; if it equals source_image, it is not a
        # generated rewritten cover and must not block generation.
        return {
            "image_prompt": "复用已生成封面",
            "image_url": existing_image_url,
            "image_local_path": existing_image_local_path,
            "should_generate": False,
            "reason": "existing_cover",
            "is_forwarded": False,
        }

    return {
        # Rewritten article path: image worker should generate a fresh cover.
        # The prompt is intentionally simple and title-grounded here; provider
        # adapters can enrich style/size details without changing pipeline
        # routing behavior.
        "image_prompt": f"新闻配图: {title}",
        "image_url": "",
        "image_local_path": "",
        "should_generate": True,
        "reason": "rewritten_generate_cover",
        "is_forwarded": False,
    }


async def fill_article_content(item: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill missing article body and source image from MySQL when possible."""
    # Graph state should already carry content and source image. This fallback
    # protects manually inserted or partially hydrated payloads.
    has_content = bool(item.get("content_md") or item.get("content") or item.get("description"))
    has_source_image = bool(item.get("source_image") or item.get("image") or item.get("cover_image"))
    if (has_content and has_source_image) or not item.get("article_id"):
        # Nothing to repair: either the payload is complete, or we do not know
        # which DB row to backfill from.
        return item
    try:
        import aiomysql

        tables = crawler_table_config()
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
                    f"SELECT title, description, original_url, image FROM {tables.main_sql} WHERE id=%s LIMIT 1",
                    (item.get("article_id"),),
                )
                row = await cur.fetchone()
        pool.close()
        await pool.wait_closed()
        if row:
            # Do not overwrite fields that are already present in state. Only
            # fill blanks so a newer upstream payload wins over DB fallback data.
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
    """Fetch the latest stored cover image info for an article from audit records."""
    if not article_id:
        return {}
    try:
        import aiomysql

        tables = crawler_table_config()
        # Supports retry/replay: if a previous image attempt already produced a
        # cover, the next attempt can reuse it instead of paying again.
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
                    f"FROM {tables.audit_sql} WHERE article_id=%s "
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
    """Persist SEO metadata produced by the SEO stage into pipeline_audit."""
    if not article_id:
        return
    try:
        import aiomysql

        tables = crawler_table_config()
        # Store keywords as a flat JSON array. meta_title/meta_description have
        # their own columns and should not be nested into seo_keywords.
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
                    f"UPDATE {tables.audit_sql} SET seo_meta_title=%s, seo_meta_description=%s, "
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
    """Persist generated or reused cover image fields into pipeline_audit."""
    if not article_id:
        return
    try:
        import aiomysql

        tables = crawler_table_config()
        # image_url may be a remote crawler/CDN URL; image_local_path is used for
        # downloaded/generated local files. CMS later chooses whichever exists.
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
                    f"UPDATE {tables.audit_sql} SET image_url=%s, image_local_path=%s, cms_status=%s WHERE article_id=%s",
                    (image_url, image_local_path, status, article_id),
                )
            await c.commit()
        pool.close()
        await pool.wait_closed()
    except Exception:
        logger.exception("image audit write error")


async def update_audit_cms(article_id: Any, *, cms_r: Dict[str, Any], image_url: str, image_local_path: str, meta_title: str, meta_desc: str, keywords) -> None:
    """Persist final CMS publication result and metadata into pipeline_audit."""
    if not article_id:
        return
    try:
        import aiomysql

        tables = crawler_table_config()
        # Final audit update records both CMS result and the content metadata
        # that was sent, making post-publish debugging easier.
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
                    f"UPDATE {tables.audit_sql} SET image_url=%s, image_local_path=%s, "
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
    """Update only the pipeline_audit CMS/status field for blocked late stages."""
    if not article_id:
        return
    try:
        import aiomysql

        tables = crawler_table_config()
        # Lightweight status-only update used when a late-stage worker blocks an
        # article, for example image_blocked or cms_blocked.
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
                await cur.execute(f"UPDATE {tables.audit_sql} SET cms_status=%s WHERE article_id=%s", (status, article_id))
            await c.commit()
        pool.close()
        await pool.wait_closed()
    except Exception:
        logger.exception("audit status update error")


def slugify(title: str) -> str:
    """Convert a title into a short URL-safe slug for CMS publishing."""
    # Small local slug generator. CMSAgent may also validate/repair slugs, but
    # workers need a predictable page_info.slug before calling it.
    s = unicodedata.normalize("NFKD", title)
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "-", s)[:60].strip("-") or "article"


def validate_publish_prerequisites(item: Dict[str, Any], *, title: str, content: str) -> None:
    """Raise if an article lacks required data before SEO/image/CMS work starts."""
    # Early validation catches broken messages before spending SEO/image/CMS
    # calls. Rewritten articles have stricter requirements than forwarded ones.
    missing = []
    if not item.get("article_id"):
        missing.append("article_id")
    if not str(title or "").strip():
        missing.append("title")
    if not str(content or "").strip():
        missing.append("content")
    if not is_forwarded_article(item):
        # For rewritten articles, publishing is only allowed after the second
        # quality gate has passed and rewritten markdown exists.
        if item.get("quality_after") is None:
            missing.append("quality_after")
        if not (item.get("content_md") or item.get("edited_content_md") or item.get("generated_content_md")):
            missing.append("rewritten_content")
    if missing:
        raise RuntimeError("publish_prerequisite_missing:" + ",".join(missing))


def validate_cover_ready(item: Dict[str, Any], cover: Dict[str, Any], *, featured_image: str) -> None:
    """Require a real cover image for rewritten articles before CMS publishing."""
    # Forwarded articles are allowed to reuse source images. Rewritten articles
    # must have an actual generated/reused cover before CMS.
    if cover.get("is_forwarded"):
        return
    if not str(featured_image or "").strip():
        raise RuntimeError("rewritten_cover_missing")
