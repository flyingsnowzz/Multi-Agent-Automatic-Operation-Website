#!/usr/bin/env python3
"""Production-capable batch runner for the standalone LangGraph pipeline.

Why this exists:
    The old Redis scoring worker scores articles in batches. The scoring module
    stretches scores within a batch when there are at least three valid scores.
    A single-article LangGraph run cannot reproduce that behavior. This runner
    keeps the old batch scoring behavior, then feeds each article into the
    LangGraph pipeline with ai_score already filled.

This runner keeps that batch scoring behavior, then runs the LangGraph article
workflow. By default it is still safe: one batch, dry-run publishing, no audit
write unless --persist-audit is supplied. For unattended production-style runs,
use --latest --loop --persist-audit --mark-used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agents.scoring_agent.scoring_summary import summarize_crawler_topics
from scripts.pipeline_text import clean_article_text
from scripts.publish_common import preflight_publish_config
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


def _env_int(name: str, default: int) -> int:
    # Numeric env knobs should be safe to edit by hand. Invalid values fall back
    # instead of preventing the runner from starting.
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_optional_int(name: str) -> Optional[int]:
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


def _request_stop(signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print(f"[langgraph-batch] received signal {signum}, stopping after current article", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch LangGraph production runner.")
    # There are three ways to choose input articles:
    #   --article-ids: deterministic debugging for a known set of ids.
    #   --latest: quick manual smoke test against recent rows.
    #   --feed: production-style forward scan with a persisted last_id cursor.
    # Keep them mutually exclusive so one run has exactly one source of truth.
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--article-ids", nargs="+", type=int, help="指定一批 article id，一起做 batch scoring")
    source.add_argument("--latest", action="store_true", help="从数据库取最新文章")
    source.add_argument("--feed", action="store_true", help="按 feeder 状态文件从上次 last_id 往后扫描文章，类似 Redis feeder")
    parser.add_argument(
        "--production",
        action="store_true",
        help="正式运行快捷模式：等同 --feed --loop --persist-audit --mark-used；是否真发布仍取决于 --publish 和 CMS_ENABLE_REAL_PUBLISH",
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
    if args.production:
        # Production means "run forever and persist bookkeeping", not "publish
        # to CMS". Real publishing still requires --publish plus CMS safety envs.
        args.feed = True
        args.loop = True
        args.persist_audit = True
        args.mark_used = True
    if not args.article_ids and not args.latest and not args.feed:
        parser.error("需要指定 --article-ids、--latest、--feed，或使用 --production")
    return args


async def _load_latest_ids(limit: int, *, include_used: bool) -> List[int]:
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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                where = (
                    # Keep manual latest runs focused on rows that at least have
                    # a real title and were not already identified as unusable
                    # source rows by a previous audit.
                    "m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10 "
                    "AND NOT (pa.cms_status='source_blocked' OR (pa.cms_status='blocked' AND pa.ai_score IS NULL))"
                )
                if not include_used:
                    where += " AND COALESCE(m.article_usage_status, '') <> 'used'"
                await cur.execute(
                    "SELECT m.id FROM crawler_news_main m "
                    "LEFT JOIN pipeline_audit pa ON pa.article_id=m.id "
                    f"WHERE {where} ORDER BY m.id DESC LIMIT %s",
                    (limit,),
                )
                return [int(row["id"]) for row in await cur.fetchall()]
    finally:
        pool.close()
        await pool.wait_closed()


def _load_feed_state(path: Path) -> Dict[str, Any]:
    # The feed cursor is intentionally just a JSON file. It is easy to inspect,
    # back up, and reset during manual tests.
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_feed_state(path: Path, state: Dict[str, Any]) -> None:
    # Write the cursor atomically enough for this single-process runner: create
    # the directory if needed, then replace the small JSON file.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_deadletter(event: Dict[str, Any]) -> None:
    # This file replaces Redis deadletter for the standalone runner. Keep it as
    # append-only JSONL so a long-running process can record failures without
    # needing another service.
    path = Path(os.environ.get("LANGGRAPH_DEADLETTER_PATH", DEFAULT_DEADLETTER_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


async def _max_article_id() -> int:
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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Used for bootstrap-latest: start after the current max id so a
                # fresh production run waits for new crawler rows.
                await cur.execute("SELECT COALESCE(MAX(id), 0) FROM crawler_news_main")
                row = await cur.fetchone()
                return int(row[0] or 0)
    finally:
        pool.close()
        await pool.wait_closed()


async def _feed_start_id(args: argparse.Namespace) -> int:
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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Feed mode scans forward by id. This is simpler and safer than
                # "latest" ordering because the cursor can be persisted.
                where = "id > %s AND title IS NOT NULL AND CHAR_LENGTH(title) > 10"
                params: List[Any] = [after_id]
                if not include_used:
                    where += " AND COALESCE(article_usage_status, '') <> 'used'"
                await cur.execute(
                    f"SELECT id FROM crawler_news_main WHERE {where} ORDER BY id ASC LIMIT %s",
                    (*params, limit),
                )
                return [int(row["id"]) for row in await cur.fetchall()]
    finally:
        pool.close()
        await pool.wait_closed()


async def _count_feed_candidates(*, after_id: int) -> Dict[str, int]:
    """Explain why feed mode found no candidate rows after the cursor."""

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
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # Diagnostic query for the common "no candidates" confusion:
                # there may be rows after the cursor, but all are already used.
                await cur.execute(
                    """
                    SELECT
                      COALESCE(MAX(id), 0) AS max_id,
                      SUM(CASE WHEN id > %s THEN 1 ELSE 0 END) AS rows_after_cursor,
                      SUM(CASE WHEN id > %s
                                AND title IS NOT NULL AND CHAR_LENGTH(title) > 10 THEN 1 ELSE 0 END) AS titled_after_cursor,
                      SUM(CASE WHEN id > %s
                                AND title IS NOT NULL AND CHAR_LENGTH(title) > 10
                                AND COALESCE(article_usage_status, '') = 'used' THEN 1 ELSE 0 END) AS used_after_cursor,
                      SUM(CASE WHEN id > %s
                                AND title IS NOT NULL AND CHAR_LENGTH(title) > 10
                                AND COALESCE(article_usage_status, '') <> 'used' THEN 1 ELSE 0 END) AS unused_after_cursor,
                      SUM(CASE WHEN id <= %s
                                AND title IS NOT NULL AND CHAR_LENGTH(title) > 10
                                AND COALESCE(article_usage_status, '') <> 'used' THEN 1 ELSE 0 END) AS unused_before_or_at_cursor
                    FROM crawler_news_main
                    """,
                    (after_id, after_id, after_id, after_id, after_id),
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
        print(
            "[langgraph-batch] feed no candidates "
            f"after last_id={after_id}; max_id={counts['max_id']}; "
            f"rows_after={counts['rows_after_cursor']}; titled_after={counts['titled_after_cursor']}; "
            f"used_after={counts['used_after_cursor']}; unused_after={counts['unused_after_cursor']}; "
            f"unused_before_or_at_cursor={counts['unused_before_or_at_cursor']}; "
            "reset state-path/from-id if you intentionally want to reprocess older unused rows",
            file=sys.stderr,
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
        # A row with stop_reason is still selected so save_audit can record why
        # it was skipped, but it should not count toward the requested number of
        # valid articles for expensive scoring/model work.
        if not state.get("stop_reason"):
            valid_count += 1
        if valid_count >= args.limit:
            break

    print(
        f"[langgraph-batch] feed scanned {len(selected)} candidates after last_id={after_id}; "
        f"valid={valid_count}; pending_last_id={last_scanned_id}",
        file=sys.stderr,
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
                    """
                    SELECT
                      SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10 THEN 1 ELSE 0 END) AS titled,
                      SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10
                                AND COALESCE(m.article_usage_status, '') = 'used' THEN 1 ELSE 0 END) AS used,
                      SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10
                                AND (pa.cms_status='source_blocked' OR (pa.cms_status='blocked' AND pa.ai_score IS NULL)) THEN 1 ELSE 0 END) AS source_blocked,
                      SUM(CASE WHEN m.title IS NOT NULL AND CHAR_LENGTH(m.title) > 10
                                AND NOT (pa.cms_status='source_blocked' OR (pa.cms_status='blocked' AND pa.ai_score IS NULL))
                                AND (%s OR COALESCE(m.article_usage_status, '') <> 'used') THEN 1 ELSE 0 END) AS runnable
                    FROM crawler_news_main m
                    LEFT JOIN pipeline_audit pa ON pa.article_id=m.id
                    """,
                    (1 if include_used else 0,),
                )
                row = await cur.fetchone() or {}
                return {key: int(row.get(key) or 0) for key in ["titled", "used", "source_blocked", "runnable"]}
    finally:
        pool.close()
        await pool.wait_closed()


async def _mark_articles_used(article_ids: List[int], scores_by_id: Dict[int, Dict[str, Any]]) -> None:
    """Mark source rows as consumed after a terminal pipeline result.

    This is what prevents a long-running feed loop from picking the same
    low-score or successfully processed article again. The caller deliberately
    excludes graph exceptions and missing-source rows so transient failures can
    be retried instead of disappearing.
    """

    import aiomysql

    if not article_ids:
        return

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
                    await cur.execute(
                        "UPDATE crawler_news_main "
                        "SET article_overall_score=%s, article_scored_at=NOW(), "
                        "article_usage_status='used', article_used_at=NOW() "
                        "WHERE id=%s",
                        (score.get("overall_score"), article_id),
                    )
            await conn.commit()
    finally:
        pool.close()
        await pool.wait_closed()


async def _load_states(article_ids: List[int]) -> List[Dict[str, Any]]:
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
                print(
                    "[langgraph-batch] no latest candidates "
                    f"(runnable={counts['runnable']}, used={counts['used']}, source_blocked={counts['source_blocked']}, "
                    f"include_used={args.include_used})",
                    file=sys.stderr,
                )
            return []
        print(f"[langgraph-batch] loading {len(article_ids)} articles", file=sys.stderr)
        states = await _load_states(article_ids)

    if not article_ids:
        if args.feed:
            print(f"[langgraph-batch] no feed candidates (state_path={args.state_path})", file=sys.stderr)
        elif args.latest:
            counts = await _count_latest_candidates(include_used=args.include_used)
            print(
                "[langgraph-batch] no latest candidates "
                f"(runnable={counts['runnable']}, used={counts['used']}, source_blocked={counts['source_blocked']}, "
                f"include_used={args.include_used})",
                file=sys.stderr,
            )
        return []

    if args.feed:
        print(f"[langgraph-batch] loading {len(article_ids)} feed-selected articles", file=sys.stderr)
    # Batch scoring intentionally happens outside the graph. The Redis scoring
    # worker normalized scores across a batch, so single-article graph scoring
    # would not be an apples-to-apples replacement.
    scorable = [s for s in states if not s.get("stop_reason")]
    skipped = len(states) - len(scorable)
    if skipped:
        reasons: Dict[str, int] = {}
        for state in states:
            reason = str(state.get("stop_reason") or "")
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        print(f"[langgraph-batch] skipped {skipped} articles before scoring: {reasons}", file=sys.stderr)
    print(f"[langgraph-batch] scoring {len(scorable)} articles as one batch; waiting for ScoringAgent", file=sys.stderr)
    scoring = await asyncio.to_thread(
        summarize_crawler_topics,
        scorable,
        use_ai=True,
        ai_concurrency=args.ai_concurrency,
    )
    # Use article_id as the join key between batch scoring results and graph
    # states. Each graph invocation then starts with ai_score already filled.
    scores_by_id = {int(s["article_id"]): s for s in scoring.get("article_scores", []) if s.get("article_id") is not None}

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
                    "scoring_mode": "batch_normalized_like_redis_worker",
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
            print("[langgraph-batch] stop requested before graph nodes; scoring finished but no more articles will run", file=sys.stderr)
            break
        article_id = int(state.get("article_id") or state.get("id") or 0)
        score = scores_by_id.get(article_id)
        if score and score.get("overall_score") is not None:
            # This makes scoring_node a pass-through and preserves the old Redis
            # batch-normalized scoring behavior.
            state["ai_score"] = float(score["overall_score"])
            state["scoring_mode"] = "batch_normalized_like_redis_worker"
            state["batch_scoring_result"] = score
        state["run_late_stages"] = not args.no_late_stages
        state["publish_dry_run"] = not args.publish
        state["persist_audit"] = args.persist_audit
        print(f"[langgraph-batch] running graph article_id={article_id}", file=sys.stderr)
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
        if result.get("stop_reason") not in {
            "graph_exception",
            "source_article_not_found",
            "source_content_missing",
            "source_content_too_short",
        }:
            # These terminal states are safe to mark used: low score, quality
            # pass, rewrite blocked, image blocked, dry-run CMS, real CMS, etc.
            # Missing source and graph exceptions are excluded so they can be
            # retried after crawler/provider/bug fixes.
            processed_ids.append(article_id)
        results.append(result if args.full_output else summarize_graph_result(result))

    if args.mark_used:
        await _mark_articles_used(processed_ids, scores_by_id)

    if args.feed and feed_state_update and not STOP_REQUESTED:
        # Commit the feed cursor last. If anything above raises, the next loop
        # sees the same candidate ids again instead of silently skipping them.
        _save_feed_state(args.state_path, feed_state_update)
        print(
            f"[langgraph-batch] feed state advanced to last_id={feed_state_update.get('last_id')}",
            file=sys.stderr,
        )

    return results


async def main() -> int:
    args = parse_args()
    preflight_publish_config(dry_run=not args.publish)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    all_results = []
    feed_idle_rounds = 0
    feed_idle_schedule = _parse_feed_idle_backoff_hours(args.feed_idle_backoff_hours)
    while not STOP_REQUESTED:
        try:
            results = await _run_one_batch(args)
        except Exception as exc:
            _write_deadletter(
                {
                    "stage": "batch",
                    "error": str(exc),
                    "source": "feed" if args.feed else "latest" if args.latest else "article_ids",
                }
            )
            print(f"[langgraph-batch] batch exception: {exc}", file=sys.stderr)
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
            print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        elif not args.loop:
            print("[]")
        else:
            print("[langgraph-batch] no articles", file=sys.stderr)

        if not args.loop:
            break
        if args.article_ids:
            print("[langgraph-batch] --article-ids with --loop would rerun the same ids; stopping", file=sys.stderr)
            break
        if STOP_REQUESTED:
            break
        if args.feed and not results:
            # Only feeder idle uses long backoff. Non-feed loop modes and
            # productive feed rounds keep the normal short interval so newly
            # available work is picked up promptly after a successful batch.
            sleep_seconds = _feed_idle_sleep_seconds(feed_idle_rounds, feed_idle_schedule)
            feed_idle_rounds += 1
            print(
                f"[langgraph-batch] feed idle; next check in {sleep_seconds / 3600:g}h "
                f"(idle_round={feed_idle_rounds})",
                file=sys.stderr,
            )
            await asyncio.sleep(sleep_seconds)
        else:
            await asyncio.sleep(max(1, args.interval))

    if args.loop:
        print(f"[langgraph-batch] stopped, total_results={len(all_results)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
