#!/usr/bin/env python3
"""Production-capable batch runner for the standalone LangGraph pipeline.

Why this exists:
    Scoring is intentionally computed for a batch so articles in the same feed
    window are comparable. A single-article graph run cannot reproduce that
    normalization. This runner computes batch scores first, then feeds each
    article into the LangGraph pipeline with ai_score already filled.

By default it is still safe: one batch, dry-run publishing, no audit write
unless --persist-audit is supplied. For unattended production-style runs, use
--production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agents.scoring_agent.scoring_summary import summarize_crawler_topics
from agents.cms_agent.cms_agent import CMSAgent
from scripts.db_config import crawler_table_config
from scripts.pipeline_text import clean_article_text
from scripts.prompt_db_logger import log_agent_prompt
from scripts.publish_common import normalize_forwarded_content_md, preflight_publish_config, slugify, update_audit_cms
from workflows.langgraph_article_pipeline import (
    load_source_node,
    run_article_graph,
    save_audit_node,
    summarize_graph_result,
)


STOP_REQUESTED = False
DEFAULT_STATE_PATH = ROOT / "output" / "langgraph_feeder_state.json"
DEFAULT_DEADLETTER_PATH = ROOT / "output" / "langgraph_deadletter.jsonl"
DEFAULT_FEED_IDLE_BACKOFF_HOURS = "1,2,4,8,12,24"
LOG = logging.getLogger("langgraph.batch")


def _configure_logging() -> None:
    """Configure readable one-line runtime logs for foreground and daemon runs."""

    # Prompt/deadletter logs remain JSONL. Runtime logs are optimized for humans:
    # time, level, logger name, then a concise event message with key=value fields.
    app_level = os.environ.get("LANGGRAPH_LOG_LEVEL", "INFO").upper()
    third_party_level = os.environ.get("LANGGRAPH_THIRD_PARTY_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=app_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for noisy_logger in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(third_party_level)


def _title_for_log(value: Any, *, limit: int = 42) -> str:
    """Return a compact single-line title for runtime log messages."""

    title = " ".join(str(value or "").split())
    if len(title) <= limit:
        return title
    return title[: limit - 3] + "..."


def _log_result_summary(results: List[Dict[str, Any]]) -> None:
    """Write one readable completion line per article result."""

    for item in results:
        LOG.info(
            "article_done article_id=%s ai_score=%s quality_score=%s rewrite_quality=%s cms_status=%s stop_reason=%s audit=%s title=%s",
            item.get("article_id"),
            item.get("ai_score"),
            item.get("quality_score"),
            item.get("rewrite_quality_after"),
            item.get("cms_status"),
            item.get("stop_reason") or "-",
            item.get("audit_persisted"),
            json.dumps(_title_for_log(item.get("title")), ensure_ascii=False),
        )


def _content_html_from_markdown(content: str) -> str:
    """Convert stored Markdown/plain text into CMS-ready HTML for pending release."""

    text = str(content or "").strip()
    if not text:
        return ""
    try:
        from markdown import markdown as markdown_to_html

        return markdown_to_html(text, extensions=["extra", "sane_lists"])
    except Exception:
        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
        return "\n".join(f"<p>{p}</p>" for p in paragraphs) or text


def _ensure_reprint_title(title: str) -> str:
    """Prefix forwarded article titles before pending CMS release."""

    text = str(title or "").strip()
    if not text:
        return "转载"
    if text.startswith(("转载｜", "转载 |", "转载：", "【转载】")):
        return text
    return f"转载｜{text}"


def _ensure_reprint_credit(content: str, *, source_title: Any, source_url: Any) -> str:
    """Keep forwarded content unchanged; public reprint credits are disabled."""

    return str(content or "").strip()


def _strip_public_source_markers(content: str) -> str:
    """Remove visible source/reprint labels from CMS-facing article content."""

    text = str(content or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?ms)\n*^##\s*(参考来源|参考资料)\s*$.*\Z", "", text).strip()
    text = re.sub(r"(?m)^\s*>\s*(转载来源|原文来源|参考来源|参考资料)[:：]?.*$\n?", "", text).strip()
    return text


def _looks_like_flat_forwarded_content(content: Any) -> bool:
    """Detect old forwarded snapshots that were flattened into one paragraph."""

    text = str(content or "").strip()
    return len(text) > 800 and "\n\n" not in text


def _audit_text(value: Any) -> str:
    """Return source/generated text for JSONL audit, capped by environment config."""

    text = str(value or "")
    limit = _env_int("PROMPT_AUDIT_TEXT_LIMIT", 12000)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


async def _log_batch_scoring_audit(states: List[Dict[str, Any]], scores_by_id: Dict[int, Dict[str, Any]]) -> None:
    """Write per-article batch scoring details into prompt audit JSONL."""

    for state in states:
        article_id = int(state.get("article_id") or state.get("id") or 0)
        score = scores_by_id.get(article_id) or {}
        await log_agent_prompt(
            article_id=article_id,
            stage="langgraph_scoring",
            agent_name="ScoringAgent",
            prompt_type="batch_scoring_detail",
            input_payload={
                "title": state.get("title"),
                "source_url": state.get("source_url") or state.get("original_url"),
                "source_content_chars": len(state.get("source_content") or ""),
                "source_content": _audit_text(state.get("source_content")),
                "scoring_mode": "batch_normalized",
            },
            output_payload={
                "overall_score": score.get("overall_score"),
                "title_style_score": score.get("title_style_score"),
                "content_importance_score": score.get("content_importance_score"),
                "raw_content_importance_score": score.get("raw_content_importance_score"),
                "notice_score": score.get("notice_score"),
                "freshness_score": score.get("freshness_score"),
                "freshness_factor": score.get("freshness_factor"),
                "freshness_weight_active": score.get("freshness_weight_active"),
                "score_breakdown": score.get("score_breakdown"),
                "topics": score.get("topics"),
                "reasons": score.get("reasons"),
                "ai_reason": score.get("ai_reason"),
                "raw_scoring_result": score,
            },
            model_name=os.environ.get("ARTICLE_SCORING_MODEL", ""),
        )


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a safe fallback."""

    # Numeric env knobs should be safe to edit by hand. Invalid values fall back
    # instead of preventing the runner from starting.
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float environment setting and fall back on invalid values."""

    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_optional_int(name: str) -> Optional[int]:
    """Read an optional integer environment variable.

    Empty or missing values return None so callers can distinguish "not set"
    from a real numeric zero.
    """

    # Optional ints are used for "unset means decide automatically" settings
    # such as LANGGRAPH_FEED_FROM_ID.
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable using common true spellings."""

    # Accept common true spellings so .env remains friendly to non-Python users.
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_feed_idle_backoff_hours(raw: str) -> List[float]:
    """Parse the feed-only idle backoff schedule from CLI/env.

    The schedule is expressed in hours because operators think about "check
    again in 1/2/4 hours" more naturally than seconds. Bad values fall back to
    the safe default instead of crashing the production runner on startup.
    """

    values: List[float] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hours = float(part)
        except (TypeError, ValueError):
            return _parse_feed_idle_backoff_hours(DEFAULT_FEED_IDLE_BACKOFF_HOURS)
        if hours <= 0:
            return _parse_feed_idle_backoff_hours(DEFAULT_FEED_IDLE_BACKOFF_HOURS)
        values.append(hours)
    if not values:
        return _parse_feed_idle_backoff_hours(DEFAULT_FEED_IDLE_BACKOFF_HOURS)
    return values


def _feed_idle_sleep_seconds(idle_rounds: int, schedule_hours: List[float]) -> int:
    """Return how long feed mode should sleep after an empty scan.

    idle_rounds is zero-based:
      0 -> first empty scan  -> 1 hour
      1 -> second empty scan -> 2 hours
      ...
    After the schedule is exhausted, keep using the last value (24h by default).
    """

    schedule = schedule_hours or _parse_feed_idle_backoff_hours(DEFAULT_FEED_IDLE_BACKOFF_HOURS)
    index = min(max(int(idle_rounds or 0), 0), len(schedule) - 1)
    return max(1, int(schedule[index] * 3600))


def _parse_cms_schedule_slots() -> List[Tuple[int, int]]:
    """Return configured CMS schedule slots as sorted (hour, minute) tuples."""

    slots: List[Tuple[int, int]] = []
    for part in str(os.environ.get("CMS_SCHEDULE_TIMES", "09:00") or "").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            hour_text, minute_text = item.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            slots.append((hour, minute))
    return sorted(set(slots)) or [(9, 0)]


def _cms_schedule_times() -> List[str]:
    """Return configured CMS schedule time strings."""

    return [f"{hour:02d}:{minute:02d}" for hour, minute in _parse_cms_schedule_slots()]


def _cms_daily_publish_target() -> int:
    """Return how many CMS publish slots should be filled for one day."""

    configured = _env_int("CMS_DAILY_PUBLISH_TARGET", 0)
    if configured > 0:
        return configured
    return len(_cms_schedule_times()) * max(1, _env_int("CMS_SCHEDULE_PER_SLOT", 1))


def _cms_schedule_state_path() -> Path:
    """Return the CMS schedule state path used by CMSAgent."""

    return Path(os.environ.get("CMS_SCHEDULE_STATE_PATH", "output/cms_publish_schedule_state.json"))


def _cms_schedule_filled_today() -> int:
    """Estimate how many publish slots have been assigned today."""

    target = _cms_daily_publish_target()
    path = _cms_schedule_state_path()
    today = datetime.now().strftime("%Y-%m-%d")
    if not path.exists():
        return 0
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(state, dict):
        return 0
    state_date = str(state.get("date") or "")
    if state_date > today:
        return target
    if state_date < today:
        return 0
    slot_index = max(0, int(state.get("slot_index") or 0))
    used = max(0, int(state.get("used") or 0))
    per_slot = max(1, _env_int("CMS_SCHEDULE_PER_SLOT", 1))
    return min(target, slot_index * per_slot + used)


def _cms_publish_slots_remaining_today() -> int:
    """Return remaining scheduled publish slots for today."""

    if not _env_bool("CMS_SCHEDULE_ENABLED", False):
        return 0
    return max(0, _cms_daily_publish_target() - _cms_schedule_filled_today())


def _cms_slot_datetime(day: datetime, slot: Tuple[int, int]) -> datetime:
    """Build a local datetime for one CMS schedule slot on the given day."""

    return day.replace(hour=slot[0], minute=slot[1], second=0, microsecond=0)


def _cms_schedule_dispatch_status(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return whether unattended latest publish may dispatch a slot right now.

    CMSAgent can still assign future `publictime` for manual runs. This helper
    is only for the long-running latest publisher: it prevents `make run` from
    immediately filling every configured publish slot for the day.
    """

    now = now or datetime.now()
    slots = _parse_cms_schedule_slots()
    per_slot = max(1, _env_int("CMS_SCHEDULE_PER_SLOT", 1))
    window_seconds = max(60, _env_int("CMS_SCHEDULE_SLOT_WINDOW_SECONDS", 900))
    today = now.strftime("%Y-%m-%d")
    due_index: Optional[int] = None
    for index, slot in enumerate(slots):
        slot_dt = _cms_slot_datetime(now, slot)
        age_seconds = (now - slot_dt).total_seconds()
        if 0 <= age_seconds <= window_seconds:
            due_index = index
            break

    def seconds_until_next_slot() -> int:
        candidates = []
        for day_offset in (0, 1):
            base_day = now + timedelta(days=day_offset)
            for slot in slots:
                slot_dt = _cms_slot_datetime(base_day, slot)
                if slot_dt > now:
                    candidates.append(slot_dt)
        next_slot = min(candidates) if candidates else now + timedelta(hours=1)
        return max(1, int((next_slot - now).total_seconds()))

    if due_index is None:
        return {
            "due": False,
            "remaining": 0,
            "sleep_seconds": seconds_until_next_slot(),
            "slot": None,
        }

    state = {}
    path = _cms_schedule_state_path()
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            state = loaded if isinstance(loaded, dict) else {}
    except Exception:
        state = {}

    state_date = str(state.get("date") or "")
    state_index = int(state.get("slot_index") or 0)
    state_used = max(0, int(state.get("used") or 0))
    if state_date > today:
        # Manual/bulk publishes can push CMSAgent's schedule cursor into a
        # future day. The unattended dispatcher should still honor today's
        # configured slot when it arrives, so rewind the cursor to the current
        # due slot instead of treating the day as already full.
        state = {"date": today, "slot_index": due_index, "used": 0}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            LOG.warning("cms_schedule_state_rewind_failed error=%s path=%s", exc, path)
        LOG.warning("cms_schedule_state_rewound previous_date=%s current_date=%s slot=%s", state_date, today, due_index)
        remaining = per_slot
    elif state_date == today and state_index > due_index:
        remaining = 0
    elif state_date == today and state_index == due_index:
        remaining = max(0, per_slot - state_used)
    else:
        remaining = per_slot

    return {
        "due": remaining > 0,
        "remaining": remaining,
        "sleep_seconds": seconds_until_next_slot(),
        "slot": f"{slots[due_index][0]:02d}:{slots[due_index][1]:02d}",
    }


def _published_slot_count(results: List[Dict[str, Any]]) -> int:
    """Count results that consumed a real CMS publish slot."""

    count = 0
    for item in results:
        status = str(item.get("cms_status") or "").lower()
        if status in {"published", "scheduled"} and (item.get("cms_article_id") or item.get("cms_article_url")):
            count += 1
    return count


def _shard_content_join_sql() -> str:
    """Build a derived table with the best body length for each crawler row."""

    tables = crawler_table_config()
    selects = [
        f"SELECT news_id, CHAR_LENGTH(COALESCE(content, '')) AS content_len FROM {tables.shard_sql(idx)}"
        for idx in range(tables.shard_count)
    ]
    return (
        "LEFT JOIN ("
        " SELECT news_id, MAX(content_len) AS content_len FROM ("
        + " UNION ALL ".join(selects)
        + ") shard_bodies GROUP BY news_id"
        ") body ON body.news_id=m.id "
    )


async def _count_pending_audit_articles() -> int:
    """Count generated articles waiting for a CMS publish window."""

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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SELECT COUNT(*) FROM {tables.audit_sql} WHERE cms_status='pending'")
                row = await cur.fetchone()
                return int((row or [0])[0] or 0)
    finally:
        pool.close()
        await pool.wait_closed()


async def _load_pending_audit_rows(limit: int) -> List[Dict[str, Any]]:
    """Load pending generated articles from pipeline_audit, newest first."""

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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"""
                    SELECT
                      pa.article_id, pa.ai_score, pa.quality_score, pa.rewrite_quality_after,
                      pa.generated_title, pa.generated_content_md,
                      pa.edited_title, pa.edited_content_md,
                      pa.image_url, pa.image_local_path,
                      pa.seo_meta_title, pa.seo_meta_description, pa.seo_keywords,
                      m.title AS source_title, m.original_url, m.image AS source_image
                    FROM {tables.audit_sql} pa
                    LEFT JOIN {tables.main_sql} m ON m.id=pa.article_id
                    WHERE pa.cms_status='pending'
                      AND COALESCE(pa.cms_article_id, '')=''
                      AND COALESCE(pa.cms_article_url, '')=''
                    ORDER BY pa.updated_at DESC, pa.article_id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(row) for row in await cur.fetchall()]
    finally:
        pool.close()
        await pool.wait_closed()


async def _publish_pending_audit_batch(limit: int) -> List[Dict[str, Any]]:
    """Publish already-generated pending audit rows without rerunning agents."""

    rows = await _load_pending_audit_rows(limit)
    if not rows:
        LOG.info("pending_publish_no_articles")
        return []

    results: List[Dict[str, Any]] = []
    cms_agent = CMSAgent(dry_run=False)
    try:
        for row in rows:
            if STOP_REQUESTED:
                break
            article_id = int(row.get("article_id") or 0)
            title = str(row.get("edited_title") or row.get("generated_title") or row.get("source_title") or "")
            content_md = str(row.get("edited_content_md") or row.get("generated_content_md") or "")
            forwarded_release = not row.get("edited_content_md")
            if not content_md and article_id:
                source_state = await load_source_node({"article_id": article_id})
                content_md = normalize_forwarded_content_md(
                    source_state.get("raw_content") or source_state.get("content") or source_state.get("source_content") or source_state.get("description") or ""
                )
                title = title or str(source_state.get("title") or "")
            if forwarded_release:
                if article_id and _looks_like_flat_forwarded_content(content_md):
                    source_state = await load_source_node({"article_id": article_id})
                    raw_content = source_state.get("raw_content") or ""
                    if raw_content:
                        content_md = normalize_forwarded_content_md(raw_content)
                        title = title or str(source_state.get("title") or "")
                title = _ensure_reprint_title(title)
                content_md = _ensure_reprint_credit(
                    normalize_forwarded_content_md(content_md),
                    source_title=row.get("source_title") or title,
                    source_url=row.get("original_url") or "",
                )
            content_md = _strip_public_source_markers(content_md)
            image_ref = str(row.get("image_local_path") or row.get("image_url") or row.get("source_image") or "")
            try:
                keywords_raw = row.get("seo_keywords")
                keywords = json.loads(keywords_raw) if isinstance(keywords_raw, str) and keywords_raw else []
            except Exception:
                keywords = []
            page_info = {
                "slug": slugify(title),
                "category": "news",
                "meta_title": row.get("seo_meta_title") or title,
                "meta_description": row.get("seo_meta_description") or "",
                "keywords": keywords,
            }
            images = {
                "featured_image_url": image_ref,
                "cover_url": image_ref,
                "featured_image": image_ref,
                "image_url": row.get("image_url") or "",
                "image_local_path": row.get("image_local_path") or "",
            }
            article = {
                "title": title,
                "content_html": _content_html_from_markdown(content_md),
                "content_md": content_md,
                "meta_description": row.get("seo_meta_description") or "",
                "source": {"article_id": article_id, "url": row.get("original_url") or ""},
            }
            try:
                cms_result = await cms_agent.execute(article=article, page_info=page_info, images=images)
                await update_audit_cms(
                    article_id,
                    cms_r=cms_result,
                    image_url=str(row.get("image_url") or ""),
                    image_local_path=str(row.get("image_local_path") or ""),
                    meta_title=str(row.get("seo_meta_title") or ""),
                    meta_desc=str(row.get("seo_meta_description") or ""),
                    keywords=keywords,
                )
                results.append(
                    {
                        "article_id": article_id,
                        "title": title,
                        "ai_score": row.get("ai_score"),
                        "quality_score": row.get("quality_score"),
                        "rewrite_quality_after": row.get("rewrite_quality_after"),
                        "cms_status": cms_result.get("status"),
                        "cms_article_id": cms_result.get("article_id"),
                        "cms_article_url": cms_result.get("article_url"),
                        "audit_persisted": True,
                        "stop_reason": None,
                        "errors": cms_result.get("errors") or [],
                        "warnings": cms_result.get("warnings") or [],
                    }
                )
            except Exception as exc:
                _write_deadletter({"stage": "pending_publish", "article_id": article_id, "error": str(exc), "title": title})
                LOG.exception("pending_publish_exception article_id=%s error=%s", article_id, exc)
    finally:
        close = getattr(cms_agent, "close", None)
        if close:
            maybe = close()
            if asyncio.iscoroutine(maybe):
                await maybe
    return results


def _request_stop(signum, _frame) -> None:
    """Mark the process for graceful shutdown after the current article."""

    global STOP_REQUESTED
    STOP_REQUESTED = True
    LOG.warning("stop_requested signal=%s action=finish_current_article", signum)


def parse_args() -> argparse.Namespace:
    """Parse CLI flags and expand --production into explicit runtime flags."""

    parser = argparse.ArgumentParser(description="Batch LangGraph production runner.")
    # There are three ways to choose input articles:
    #   --article-ids: deterministic debugging for a known set of ids.
    #   --latest: quick manual smoke test against recent rows.
    #   --feed: production-style forward scan with a persisted last_id cursor.
    # Keep them mutually exclusive so one run has exactly one source of truth.
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--article-ids", "--ids", nargs="+", type=int, help="指定一批 article id，一起做 batch scoring")
    source.add_argument("--latest", action="store_true", help="从数据库取最新文章")
    source.add_argument("--feed", action="store_true", help="按 feeder 状态文件从上次 last_id 往后扫描文章")
    parser.add_argument(
        "--production",
        action="store_true",
        help="正式运行快捷模式：等同 --feed --loop --persist-audit --mark-used；是否真发布仍取决于 --publish 和 CMS_ENABLE_REAL_PUBLISH",
    )
    parser.add_argument(
        "--production-source",
        choices=["feed", "latest"],
        default=os.environ.get("LANGGRAPH_PRODUCTION_SOURCE", "feed"),
        help="--production 的取文模式：feed=游标向前扫，latest=每轮取最新未 used 文章",
    )
    parser.add_argument("--limit", type=int, default=_env_int("LANGGRAPH_BATCH_LIMIT", 30), help="--latest 时读取多少篇")
    parser.add_argument("--loop", action="store_true", default=_env_bool("LANGGRAPH_LOOP", False), help="持续循环读取新文章")
    parser.add_argument(
        "--interval",
        type=int,
        default=_env_int("LANGGRAPH_LOOP_INTERVAL_SECONDS", 60),
        help="--loop 模式下每轮之间等待秒数",
    )
    parser.add_argument(
        "--feed-idle-backoff-hours",
        type=str,
        default=os.environ.get("LANGGRAPH_FEED_IDLE_BACKOFF_HOURS", DEFAULT_FEED_IDLE_BACKOFF_HOURS),
        help="--feed loop 没有候选文章时的退避小时序列，默认 1,2,4,8,12,24；有文章后自动恢复正常 interval",
    )
    parser.add_argument("--include-used", action="store_true", help="--latest 时也包含 article_usage_status=used 的文章")
    parser.add_argument("--state-path", type=Path, default=Path(os.environ.get("LANGGRAPH_FEED_STATE_PATH", DEFAULT_STATE_PATH)), help="--feed 模式的 last_id 状态文件")
    parser.add_argument("--from-id", type=int, default=_env_optional_int("LANGGRAPH_FEED_FROM_ID"), help="--feed 首次启动时从指定 id 之后开始")
    parser.add_argument(
        "--feed-existing",
        action="store_true",
        default=_env_bool("LANGGRAPH_FEED_EXISTING", _env_bool("PIPELINE_FEED_EXISTING", False)),
        help="--feed 无状态文件时从 id=0 开始扫历史数据；否则默认从当前最大 id 之后等新文章",
    )
    parser.add_argument("--bootstrap-latest", action="store_true", help="--feed 无状态文件时从当前最大 id 开始，只处理之后新增文章")
    parser.add_argument("--no-late-stages", action="store_true", help="不跑 SEO/image/CMS")
    parser.add_argument("--scoring-only", action="store_true", help="只跑批量 ScoringAgent 并输出 ai_score，不进入 quality/rewrite")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="允许 CMSAgent 进入发布模式；仍需 CMS_ENABLE_REAL_PUBLISH=true 才会真发",
    )
    parser.add_argument(
        "--persist-audit",
        action="store_true",
        default=_env_bool("LANGGRAPH_PERSIST_AUDIT", False),
        help="把每篇 graph 结果写回 pipeline_audit；不加这个参数时只打印结果，不改数据库",
    )
    parser.add_argument(
        "--mark-used",
        action="store_true",
        default=_env_bool("LANGGRAPH_MARK_USED", False),
        help="处理后把 crawler_news_main.article_usage_status 标记为 used，防止循环模式反复处理同一篇",
    )
    parser.add_argument(
        "--ai-concurrency",
        type=int,
        default=_env_int("LANGGRAPH_SCORING_AI_CONCURRENCY", 4),
        help="批量 ScoringAgent 的 AI 并发数",
    )
    parser.add_argument("--full-output", action="store_true", help="输出完整 state")
    args = parser.parse_args()
    args.defer_cms_publish = False
    if args.production:
        # Production means "run forever and persist bookkeeping", not "publish
        # to CMS". Real publishing still requires --publish plus CMS safety envs.
        if args.production_source == "latest":
            args.latest = True
            args.feed = False
        else:
            args.feed = True
            args.latest = False
        args.loop = True
        args.persist_audit = True
        args.mark_used = True
    if not args.article_ids and not args.latest and not args.feed:
        parser.error("需要指定 --article-ids、--latest、--feed，或使用 --production")
    return args


async def _load_latest_ids(limit: int, *, include_used: bool) -> List[int]:
    """Load newest candidate article ids for manual latest-mode runs."""

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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                where = (
                    # Keep manual latest runs focused on rows that at least have
                    # a real title, enough sharded body text, and were not
                    # already identified as unusable source rows.
                    "m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10 "
                    "AND COALESCE(body.content_len, 0) >= %s "
                    "AND COALESCE(pa.cms_status, '') <> 'source_blocked' "
                    "AND NOT (COALESCE(pa.cms_status, '')='blocked' AND pa.ai_score IS NULL)"
                )
                params: List[Any] = [_env_int("FEED_MIN_CONTENT_CHARS", 50)]
                if not include_used and tables.usage_status_sql:
                    where += f" AND COALESCE(m.{tables.usage_status_sql}, '') <> 'used'"
                await cur.execute(
                    f"SELECT DISTINCT m.id FROM {tables.main_sql} m "
                    f"{_shard_content_join_sql()}"
                    f"LEFT JOIN {tables.audit_sql} pa ON pa.article_id=m.id "
                    f"WHERE {where} ORDER BY m.id DESC LIMIT %s",
                    (*params, limit),
                )
                return [int(row["id"]) for row in await cur.fetchall()]
    finally:
        pool.close()
        await pool.wait_closed()


def _load_feed_state(path: Path) -> Dict[str, Any]:
    """Read the JSON feeder cursor state from disk."""

    # The feed cursor is intentionally just a JSON file. It is easy to inspect,
    # back up, and reset during manual tests.
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_feed_state(path: Path, state: Dict[str, Any]) -> None:
    """Persist the JSON feeder cursor state to disk."""

    # Write the cursor atomically enough for this single-process runner: create
    # the directory if needed, then replace the small JSON file.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_deadletter(event: Dict[str, Any]) -> None:
    """Append one batch/graph failure event to the LangGraph deadletter JSONL."""

    # Keep deadletter append-only JSONL so a long-running process can record
    # failures without needing another service.
    path = Path(os.environ.get("LANGGRAPH_DEADLETTER_PATH", DEFAULT_DEADLETTER_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


async def _max_article_id() -> int:
    """Return the current maximum crawler article id."""

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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Used for bootstrap-latest: start after the current max id so a
                # fresh production run waits for new crawler rows.
                await cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {tables.main_sql}")
                row = await cur.fetchone()
                return int(row[0] or 0)
    finally:
        pool.close()
        await pool.wait_closed()


async def _feed_start_id(args: argparse.Namespace) -> int:
    """Decide which article id feed mode should scan after."""

    # Start-id priority:
    #   1. persisted state file from the previous feed loop
    #   2. explicit --from-id / LANGGRAPH_FEED_FROM_ID
    #   3. current max id for "only future articles"
    #   4. zero for intentional historical backfill
    state = _load_feed_state(args.state_path)
    if state.get("last_id") is not None:
        return int(state.get("last_id") or 0)
    if args.from_id is not None:
        return int(args.from_id)
    if args.bootstrap_latest or not args.feed_existing:
        return await _max_article_id()
    return 0


async def _load_feed_candidate_ids(*, after_id: int, limit: int, include_used: bool) -> List[int]:
    """Load forward-scanned candidate ids for feed mode."""

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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Feed mode scans forward by id. This is simpler and safer than
                # "latest" ordering because the cursor can be persisted.
                where = (
                    "m.id > %s AND m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10 "
                    "AND COALESCE(body.content_len, 0) >= %s"
                )
                params: List[Any] = [after_id, _env_int("FEED_MIN_CONTENT_CHARS", 50)]
                if not include_used and tables.usage_status_sql:
                    where += f" AND COALESCE(m.{tables.usage_status_sql}, '') <> 'used'"
                await cur.execute(
                    f"SELECT m.id FROM {tables.main_sql} m "
                    f"{_shard_content_join_sql()}"
                    f"WHERE {where} ORDER BY m.id ASC LIMIT %s",
                    (*params, limit),
                )
                return [int(row["id"]) for row in await cur.fetchall()]
    finally:
        pool.close()
        await pool.wait_closed()


async def _count_feed_candidates(*, after_id: int) -> Dict[str, int]:
    """Explain why feed mode found no candidate rows after the cursor."""

    import aiomysql

    tables = crawler_table_config()
    used_after_sql = "0"
    unused_after_sql = (
        "SUM(CASE WHEN id > %s AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 THEN 1 ELSE 0 END)"
    )
    unused_before_sql = (
        "SUM(CASE WHEN id <= %s AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 THEN 1 ELSE 0 END)"
    )
    params: Tuple[Any, ...]
    if tables.usage_status_sql:
        used_after_sql = (
            f"SUM(CASE WHEN id > %s AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 "
            f"AND COALESCE({tables.usage_status_sql}, '') = 'used' THEN 1 ELSE 0 END)"
        )
        unused_after_sql = (
            f"SUM(CASE WHEN id > %s AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 "
            f"AND COALESCE({tables.usage_status_sql}, '') <> 'used' THEN 1 ELSE 0 END)"
        )
        unused_before_sql = (
            f"SUM(CASE WHEN id <= %s AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 "
            f"AND COALESCE({tables.usage_status_sql}, '') <> 'used' THEN 1 ELSE 0 END)"
        )
        params = (after_id, after_id, after_id, after_id, after_id)
    else:
        params = (after_id, after_id, after_id, after_id)
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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Diagnostic query for the common "no candidates" confusion:
                # there may be rows after the cursor, but all are already used.
                await cur.execute(
                    f"""
                    SELECT
                      COALESCE(MAX(id), 0) AS max_id,
                      SUM(CASE WHEN id > %s THEN 1 ELSE 0 END) AS rows_after_cursor,
                      SUM(CASE WHEN id > %s
                                AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 THEN 1 ELSE 0 END) AS titled_after_cursor,
                      {used_after_sql} AS used_after_cursor,
                      {unused_after_sql} AS unused_after_cursor,
                      {unused_before_sql} AS unused_before_or_at_cursor
                    FROM {tables.main_sql}
                    """,
                    params,
                )
                row = await cur.fetchone() or {}
                keys = [
                    "max_id",
                    "rows_after_cursor",
                    "titled_after_cursor",
                    "used_after_cursor",
                    "unused_after_cursor",
                    "unused_before_or_at_cursor",
                ]
                return {key: int(row.get(key) or 0) for key in keys}
    finally:
        pool.close()
        await pool.wait_closed()


async def _load_feed_states(args: argparse.Namespace) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load the next feed window without committing the feed cursor yet.

    Long unattended runs must not advance last_id merely because rows were
    scanned. Scoring, provider calls, or MySQL writes can still fail after this
    point. The caller saves the returned feed_state_update only after the batch
    has reached a terminal state.
    """

    after_id = await _feed_start_id(args)
    # Fetch more candidate ids than the target batch size because some rows may
    # be missing source body text or may fail minimum-content checks. The loop
    # below stops after args.limit valid rows, but still records how far it
    # scanned so bad rows do not block the feeder forever.
    candidate_limit = max(args.limit * max(_env_int("FEED_FETCH_MULTIPLIER", 5), 1), args.limit)
    candidate_ids = await _load_feed_candidate_ids(
        after_id=after_id,
        limit=candidate_limit,
        include_used=args.include_used,
    )
    if not candidate_ids:
        counts = await _count_feed_candidates(after_id=after_id)
        LOG.info(
            "feed_no_candidates last_id=%s max_id=%s rows_after=%s titled_after=%s used_after=%s unused_after=%s unused_before_or_at_cursor=%s hint=%s",
            after_id,
            counts["max_id"],
            counts["rows_after_cursor"],
            counts["titled_after_cursor"],
            counts["used_after_cursor"],
            counts["unused_after_cursor"],
            counts["unused_before_or_at_cursor"],
            "reset state-path/from-id to reprocess older unused rows",
        )
        return [], {"previous_last_id": after_id, "last_id": after_id, "last_run_valid_count": 0, "last_run_scanned_count": 0}

    states = await _load_states(candidate_ids)
    selected: List[Dict[str, Any]] = []
    valid_count = 0
    last_scanned_id = after_id
    for state in states:
        article_id = int(state.get("article_id") or state.get("id") or 0)
        if article_id:
            last_scanned_id = max(last_scanned_id, article_id)
        selected.append(state)
        # A row with stop_reason is still selected so the feeder cursor can move
        # past bad source rows, but it does not count toward the requested
        # number of valid articles for expensive scoring/model work. The audit
        # layer skips source_blocked rows so pipeline_audit only contains items
        # that actually entered the article pipeline.
        if not state.get("stop_reason"):
            valid_count += 1
        if valid_count >= args.limit:
            break

    LOG.info(
        "feed_scanned selected=%s valid=%s previous_last_id=%s pending_last_id=%s",
        len(selected),
        valid_count,
        after_id,
        last_scanned_id,
    )
    return selected, {
        "previous_last_id": after_id,
        "last_id": last_scanned_id,
        "last_run_valid_count": valid_count,
        "last_run_scanned_count": len(selected),
    }


async def _count_latest_candidates(*, include_used: bool) -> Dict[str, int]:
    """Count why --latest may have no runnable articles."""

    import aiomysql

    tables = crawler_table_config()
    used_sql = "0"
    usage_filter_sql = "1"
    params: Tuple[Any, ...] = ()
    if tables.usage_status_sql:
        used_sql = (
            f"SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10 "
            f"AND COALESCE(m.{tables.usage_status_sql}, '') = 'used' THEN 1 ELSE 0 END)"
        )
        usage_filter_sql = f"(%s OR COALESCE(m.{tables.usage_status_sql}, '') <> 'used')"
        params = (1 if include_used else 0,)
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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Same idea as feed diagnostics, but for latest mode where there
                # is no cursor and ordering is newest-first.
                await cur.execute(
                    f"""
                    SELECT
                      SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10 THEN 1 ELSE 0 END) AS titled,
                      {used_sql} AS used,
                      SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10
                                AND (COALESCE(pa.cms_status, '')='source_blocked' OR (COALESCE(pa.cms_status, '')='blocked' AND pa.ai_score IS NULL)) THEN 1 ELSE 0 END) AS source_blocked,
                      SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10
                                AND COALESCE(pa.cms_status, '') <> 'source_blocked'
                                AND NOT (COALESCE(pa.cms_status, '')='blocked' AND pa.ai_score IS NULL)
                                AND {usage_filter_sql} THEN 1 ELSE 0 END) AS runnable
                    FROM {tables.main_sql} m
                    LEFT JOIN {tables.audit_sql} pa ON pa.article_id=m.id
                    """,
                    params,
                )
                row = await cur.fetchone() or {}
                return {key: int(row.get(key) or 0) for key in ["titled", "used", "source_blocked", "runnable"]}
    finally:
        pool.close()
        await pool.wait_closed()


async def _mark_articles_used(article_ids: List[int], scores_by_id: Dict[int, Dict[str, Any]]) -> None:
    """Mark source rows as consumed after a terminal pipeline result.

    This is what prevents a long-running feed loop from picking the same
    low-score, unusable-source, or successfully processed article again. The
    caller deliberately excludes graph exceptions and missing scores so
    transient failures can be retried instead of disappearing.
    """

    import aiomysql

    if not article_ids:
        return

    tables = crawler_table_config()
    if not tables.usage_status_sql:
        LOG.warning("mark_used_skipped reason=CRAWLER_USAGE_STATUS_COLUMN_empty article_count=%s", len(article_ids))
        return

    def json_or_none(value: Any) -> Optional[str]:
        """Serialize scoring diagnostics for MySQL JSON columns."""

        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                for article_id in article_ids:
                    score = scores_by_id.get(article_id) or {}
                    if score:
                        # Keep the full scoring explanation on crawler_news_main
                        # so operators can inspect why an article passed or
                        # failed without opening JSONL prompt logs.
                        await cur.execute(
                            f"UPDATE {tables.main_sql} "
                            "SET article_overall_score=%s, "
                            "article_title_style_score=%s, "
                            "article_content_importance_score=%s, "
                            "article_freshness_score=%s, "
                            "article_score_breakdown=%s, "
                            "article_word_count=%s, "
                            "article_topic_count=%s, "
                            "article_topics=%s, "
                            "article_score_reasons=%s, "
                            "article_ai_used=%s, "
                            "article_ai_reason=%s, "
                            "article_scoring_model=%s, "
                            "article_scoring_version=%s, "
                            "article_is_notice=%s, "
                            "article_notice_score=%s, "
                            "article_raw_content_importance_score=%s, "
                            "article_freshness_factor=%s, "
                            "article_freshness_weight_active=%s, "
                            "article_scored_at=NOW(), "
                            f"{tables.usage_status_sql}='used', article_used_at=NOW() "
                            "WHERE id=%s",
                            (
                                score.get("overall_score"),
                                score.get("title_style_score"),
                                score.get("content_importance_score"),
                                score.get("freshness_score"),
                                json_or_none(score.get("score_breakdown")),
                                score.get("word_count"),
                                score.get("topic_count"),
                                json_or_none(score.get("topics")),
                                json_or_none(score.get("reasons")),
                                1 if score.get("ai_used") else 0,
                                score.get("ai_reason"),
                                os.environ.get("ARTICLE_SCORING_MODEL", ""),
                                str(score.get("scoring_version") or "langgraph_batch_v1"),
                                (
                                    1
                                    if score.get("is_notice") is True
                                    else 0
                                    if score.get("is_notice") is False
                                    else None
                                ),
                                score.get("notice_score"),
                                score.get("raw_content_importance_score"),
                                score.get("freshness_factor"),
                                (
                                    1
                                    if score.get("freshness_weight_active") is True
                                    else 0
                                    if score.get("freshness_weight_active") is False
                                    else None
                                ),
                                article_id,
                            ),
                        )
                    else:
                        await cur.execute(
                            f"UPDATE {tables.main_sql} "
                            "SET article_scored_at=NOW(), "
                            f"{tables.usage_status_sql}='used', article_used_at=NOW() "
                            "WHERE id=%s",
                            (article_id,),
                        )
            await conn.commit()
    finally:
        pool.close()
        await pool.wait_closed()


def _should_mark_source_used(result: Dict[str, Any]) -> bool:
    """Return true for terminal states that should not be selected again."""

    stop_reason = result.get("stop_reason")
    if stop_reason in {"graph_exception", "ai_score_missing"}:
        return False
    return True


async def _load_states(article_ids: List[int]) -> List[Dict[str, Any]]:
    """Hydrate graph states for article ids and stop rows with weak source text."""

    states = []
    min_content_chars = _env_int("FEED_MIN_CONTENT_CHARS", 50)
    for article_id in article_ids:
        # Reuse the graph's load_source_node here so batch scoring and graph
        # execution see exactly the same source-content extraction behavior.
        state = await load_source_node({"article_id": article_id})
        if state.get("stop_reason"):
            states.append(dict(state))
            continue
        source_content = clean_article_text(state.get("source_content") or "")
        if len(source_content) < min_content_chars:
            # Too-short content usually means the crawler only captured a title
            # or snippet. Stop early so ScoringAgent does not give noisy scores
            # based on almost no source material.
            state["stop_reason"] = "source_content_too_short"
            state["errors"] = [*(state.get("errors") or []), "source_content_too_short"]
            states.append(dict(state))
            continue
        state["content"] = source_content
        state["source_content"] = source_content
        states.append(dict(state))
    return states


async def _run_one_batch(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Run one batch from selection through scoring, graph execution, and audit."""

    feed_state_update: Optional[Dict[str, Any]] = None
    if args.feed:
        # Feed mode returns both article states and the cursor update to commit
        # later. Do not save feed_state_update here; scoring/graph/audit can
        # still fail and we want the batch to be retried in that case.
        states, feed_state_update = await _load_feed_states(args)
        article_ids = [int(s.get("article_id") or s.get("id") or 0) for s in states if s.get("article_id") or s.get("id")]
    else:
        article_ids = args.article_ids or await _load_latest_ids(args.limit, include_used=args.include_used)
        if not article_ids:
            if args.latest:
                counts = await _count_latest_candidates(include_used=args.include_used)
                LOG.info(
                    "latest_no_candidates runnable=%s used=%s source_blocked=%s include_used=%s",
                    counts["runnable"],
                    counts["used"],
                    counts["source_blocked"],
                    args.include_used,
                )
            return []
        LOG.info("load_articles count=%s source=%s", len(article_ids), "latest" if args.latest else "article_ids")
        states = await _load_states(article_ids)

    if not article_ids:
        if args.feed:
            LOG.info("feed_no_articles state_path=%s", args.state_path)
        elif args.latest:
            counts = await _count_latest_candidates(include_used=args.include_used)
            LOG.info(
                "latest_no_candidates runnable=%s used=%s source_blocked=%s include_used=%s",
                counts["runnable"],
                counts["used"],
                counts["source_blocked"],
                args.include_used,
            )
        return []

    if args.feed:
        LOG.info("load_articles count=%s source=feed", len(article_ids))
    # Batch scoring intentionally happens outside the graph. Feed-window
    # normalization makes articles in the same batch comparable; single-article
    # graph scoring would not be an apples-to-apples replacement.
    scorable = [s for s in states if not s.get("stop_reason")]
    skipped = len(states) - len(scorable)
    if skipped:
        reasons: Dict[str, int] = {}
        for state in states:
            reason = str(state.get("stop_reason") or "")
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        LOG.info("skip_before_scoring count=%s reasons=%s", skipped, json.dumps(reasons, ensure_ascii=False))
    if scorable:
        LOG.info("scoring_start count=%s ai_concurrency=%s", len(scorable), args.ai_concurrency)
        scoring = await asyncio.to_thread(
            summarize_crawler_topics,
            scorable,
            use_ai=True,
            ai_concurrency=args.ai_concurrency,
        )
    else:
        LOG.info("scoring_skipped count=0 reason=all_articles_preblocked")
        scoring = {"article_scores": []}
    # Use article_id as the join key between batch scoring results and graph
    # states. Each graph invocation then starts with ai_score already filled.
    scores_by_id = {int(s["article_id"]): s for s in scoring.get("article_scores", []) if s.get("article_id") is not None}
    await _log_batch_scoring_audit(scorable, scores_by_id)

    if args.scoring_only:
        # Diagnostic mode: useful when debugging "scoring is too fast/too slow"
        # or "did scoring actually read the full article body?" without paying
        # for quality/rewrite/image/CMS.
        output = []
        for state in states:
            article_id = int(state.get("article_id") or state.get("id") or 0)
            score = scores_by_id.get(article_id) or {}
            output.append(
                {
                    "article_id": article_id,
                    "title": state.get("title"),
                    "source_content_chars": len(state.get("source_content") or ""),
                    "ai_score": score.get("overall_score"),
                    "scoring_mode": "batch_normalized",
                    "raw_scoring_result": score,
                    "stop_reason": state.get("stop_reason"),
                }
            )
        if args.feed and feed_state_update and not STOP_REQUESTED:
            _save_feed_state(args.state_path, feed_state_update)
        return output

    results = []
    processed_ids = []
    for state in states:
        if STOP_REQUESTED:
            LOG.warning("stop_before_graph scoring_finished=true")
            break
        article_id = int(state.get("article_id") or state.get("id") or 0)
        if state.get("stop_reason"):
            # Source-blocked or otherwise preblocked articles already reached a
            # terminal business state during _load_states(). Persist that state
            # directly instead of invoking the graph again.
            result = dict(state)
            result["persist_audit"] = args.persist_audit
            if args.persist_audit:
                result = await save_audit_node(result)
            LOG.info("graph_skipped article_id=%s stop_reason=%s", article_id, result.get("stop_reason"))
            if _should_mark_source_used(result):
                processed_ids.append(article_id)
            results.append(result if args.full_output else summarize_graph_result(result))
            continue
        score = scores_by_id.get(article_id)
        if score and score.get("overall_score") is not None:
            # This makes scoring_node a pass-through and preserves batch-normalized
            # scoring behavior.
            state["ai_score"] = float(score["overall_score"])
            state["scoring_mode"] = "batch_normalized"
            state["batch_scoring_result"] = score
        state["run_late_stages"] = not args.no_late_stages
        state["publish_dry_run"] = bool(getattr(args, "defer_cms_publish", False)) or not args.publish
        state["defer_cms_publish"] = bool(getattr(args, "defer_cms_publish", False))
        state["persist_audit"] = args.persist_audit
        LOG.info("graph_start article_id=%s title=%s", article_id, json.dumps(_title_for_log(state.get("title")), ensure_ascii=False))
        try:
            result = await run_article_graph(state)
        except Exception as exc:
            # A graph exception should not kill a long production loop or mark
            # the row used. Record it, optionally persist audit, and continue.
            result = {
                **state,
                "stop_reason": "graph_exception",
                "errors": [*(state.get("errors") or []), str(exc)],
                "persist_audit": args.persist_audit,
            }
            _write_deadletter(
                {
                    "stage": "graph",
                    "article_id": article_id,
                    "error": str(exc),
                    "title": state.get("title"),
                }
            )
            if args.persist_audit:
                result = await save_audit_node(result)
        if _should_mark_source_used(result):
            # These terminal states are safe to mark used: low score, quality
            # pass, rewrite blocked, unusable source, image blocked, dry-run
            # CMS, real CMS, etc. Graph exceptions and missing scores are
            # excluded so transient bugs/provider issues can be retried.
            processed_ids.append(article_id)
        results.append(result if args.full_output else summarize_graph_result(result))

    if args.mark_used:
        await _mark_articles_used(processed_ids, scores_by_id)

    if args.feed and feed_state_update and not STOP_REQUESTED:
        # Commit the feed cursor last. If anything above raises, the next loop
        # sees the same candidate ids again instead of silently skipping them.
        _save_feed_state(args.state_path, feed_state_update)
        LOG.info("feed_state_advanced last_id=%s state_path=%s", feed_state_update.get("last_id"), args.state_path)

    return results


async def main() -> int:
    """Run the batch runner once or forever depending on CLI flags."""

    _configure_logging()
    args = parse_args()
    LOG.info(
        "runtime_config ai_score_threshold=%s raw_ai_score_threshold=%s quality_pass_threshold=%s rewrite_quality_threshold=%s persist_audit=%s mark_used=%s production=%s",
        _env_float("AI_SCORE_THRESHOLD", 75),
        os.environ.get("AI_SCORE_THRESHOLD", "75"),
        _env_float("QUALITY_PASS_THRESHOLD", 70),
        _env_float("REWRITE_QUALITY_THRESHOLD", 70),
        args.persist_audit,
        args.mark_used,
        args.production,
    )
    preflight_publish_config(dry_run=not args.publish)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    all_results = []
    feed_idle_rounds = 0
    feed_idle_schedule = _parse_feed_idle_backoff_hours(args.feed_idle_backoff_hours)
    while not STOP_REQUESTED:
        run_args = args
        dispatch_only_at_slots = (
            args.loop
            and args.latest
            and args.publish
            and _env_bool("CMS_SCHEDULE_ENABLED", False)
            and _env_bool("CMS_SCHEDULE_DISPATCH_ONLY_AT_SLOTS", True)
        )
        if dispatch_only_at_slots:
            dispatch = _cms_schedule_dispatch_status()
            if dispatch["due"]:
                pending_limit = max(1, min(int(args.limit), int(dispatch["remaining"])))
                LOG.info(
                    "cms_schedule_dispatch slot=%s limit=%s remaining_in_slot=%s source=pending",
                    dispatch.get("slot"),
                    pending_limit,
                    dispatch["remaining"],
                )
                results = await _publish_pending_audit_batch(pending_limit)
                if results:
                    feed_idle_rounds = 0
                    all_results.extend(results)
                    _log_result_summary(results)
                    await asyncio.sleep(max(1, args.interval))
                    continue
                LOG.info("cms_schedule_dispatch_no_pending action=prepare_pending")

            pending_count = await _count_pending_audit_articles()
            prepare_target = max(0, _env_int("CMS_PENDING_PREPARE_TARGET", _cms_daily_publish_target()))
            if pending_count >= prepare_target:
                sleep_seconds = min(max(1, args.interval), int(dispatch["sleep_seconds"]))
                LOG.info(
                    "cms_schedule_wait next_check_seconds=%s next_slot_seconds=%s slot=%s pending=%s prepare_target=%s",
                    sleep_seconds,
                    dispatch["sleep_seconds"],
                    dispatch.get("slot") or "-",
                    pending_count,
                    prepare_target,
                )
                await asyncio.sleep(sleep_seconds)
                continue

            run_args = argparse.Namespace(**vars(args))
            run_args.limit = max(1, min(int(args.limit), prepare_target - pending_count))
            run_args.defer_cms_publish = True
            LOG.info(
                "cms_pending_prepare limit=%s pending=%s prepare_target=%s next_slot_seconds=%s",
                run_args.limit,
                pending_count,
                prepare_target,
                dispatch["sleep_seconds"],
            )

        try:
            results = await _run_one_batch(run_args)
        except Exception as exc:
            _write_deadletter(
                {
                    "stage": "batch",
                    "error": str(exc),
                    "source": "feed" if args.feed else "latest" if args.latest else "article_ids",
                }
            )
            LOG.exception("batch_exception error=%s", exc)
            if not args.loop:
                return 1
            await asyncio.sleep(max(1, args.interval))
            continue
        if results:
            # A productive feed round means new work arrived. Reset idle
            # backoff so the next empty period starts by checking again in 1h,
            # not whatever long delay the previous idle stretch reached.
            feed_idle_rounds = 0
            all_results.extend(results)
            _log_result_summary(results)
            if not args.loop:
                print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
            elif args.latest and args.publish and _env_bool("CMS_SCHEDULE_ENABLED", False):
                filled = _cms_schedule_filled_today()
                target = _cms_daily_publish_target()
                published_now = _published_slot_count(results)
                remaining = max(0, target - filled)
                LOG.info(
                    "daily_publish_progress target=%s filled=%s remaining=%s published_this_round=%s",
                    target,
                    filled,
                    remaining,
                    published_now,
                )
                if dispatch_only_at_slots:
                    # Slot dispatch mode intentionally caps each publish window
                    # at CMS_SCHEDULE_PER_SLOT. The next batch waits for the
                    # next configured time instead of filling the whole day now.
                    pass
                elif remaining > 0 and published_now > 0:
                    # Keep filling today's slots immediately. Without this,
                    # one weak batch could leave the daily target short until
                    # the next regular loop interval.
                    continue
                if remaining <= 0:
                    LOG.info("daily_publish_target_reached target=%s action=wait_next_interval", target)
        elif not args.loop:
            print("[]")
        else:
            LOG.info("no_articles")

        if not args.loop:
            break
        if args.article_ids:
            LOG.warning("stop_loop reason=article_ids_would_rerun")
            break
        if STOP_REQUESTED:
            break
        if args.feed and not results:
            # Only feeder idle uses long backoff. Non-feed loop modes and
            # productive feed rounds keep the normal short interval so newly
            # available work is picked up promptly after a successful batch.
            sleep_seconds = _feed_idle_sleep_seconds(feed_idle_rounds, feed_idle_schedule)
            feed_idle_rounds += 1
            LOG.info("feed_idle next_check_hours=%s idle_round=%s", sleep_seconds / 3600, feed_idle_rounds)
            await asyncio.sleep(sleep_seconds)
        else:
            await asyncio.sleep(max(1, args.interval))

    if args.loop:
        LOG.info("stopped total_results=%s", len(all_results))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
