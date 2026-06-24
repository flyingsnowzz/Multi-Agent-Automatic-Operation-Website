"""Build and write research candidates for WriterAgent."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from agents.topic_agent.tools.article_score_writer import validate_identifier


DEFAULT_RESEARCH_DATABASE = "research_article_data"
DEFAULT_RESEARCH_TABLE = "research_article_candidates"
DEFAULT_PROMPT_VERSION = "research_prompt_v1"
DEFAULT_RECENT_NOTICE_DAYS = 62

UNIMPORTANT_TITLE_RE = re.compile(
    r"(通知|公告|公示|名单|须知|值班|放假|缴费|补录|调剂复试名单|资格审查)",
    re.IGNORECASE,
)


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "是"}:
            return True
        if normalized in {"0", "false", "no", "n", "否"}:
            return False
    return None


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw[:10].replace("/", "-").replace(".", "-")
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _reference_date(value: Optional[date] = None) -> date:
    return value or date.today()


def _publish_date(article: Mapping[str, Any], score: Mapping[str, Any]) -> Optional[date]:
    return _parse_date(
        _first_non_empty(
            article.get("publish_date"),
            article.get("published_at"),
            article.get("article_publish_date"),
            score.get("publish_date"),
        )
    )


def _is_recent_publish_date(
    article: Mapping[str, Any],
    score: Mapping[str, Any],
    reference_date: Optional[date] = None,
    recent_notice_days: int = DEFAULT_RECENT_NOTICE_DAYS,
) -> bool:
    published = _publish_date(article, score)
    if published is None:
        return False
    age_days = (_reference_date(reference_date) - published).days
    return 0 <= age_days <= recent_notice_days


def _article_id(article: Mapping[str, Any], score: Mapping[str, Any]) -> Any:
    return _first_non_empty(
        score.get("article_id"),
        article.get("id"),
        article.get("article_id"),
        article.get("source_article_id"),
    )


def _original_url(article: Mapping[str, Any], score: Mapping[str, Any]) -> Optional[str]:
    value = _first_non_empty(
        article.get("original_url"),
        article.get("url"),
        article.get("source_url"),
        article.get("link"),
        score.get("original_url"),
        score.get("url"),
    )
    return str(value).strip() if value is not None else None


def _score_value(article: Mapping[str, Any], score: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _to_float(score.get(key))
        if value is not None:
            return value
        value = _to_float(article.get(key))
        if value is not None:
            return value
    return None


def extract_writer_prompt(research_result: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Extract prompt fields from a ResearchAgent result."""

    if not isinstance(research_result, Mapping):
        return {"research_brief": None, "writer_prompt": None, "writer_prompt_type": None}

    brief = research_result.get("research_brief")
    if not isinstance(brief, Mapping):
        brief = None

    prompt = research_result.get("writer_prompt")
    if not isinstance(prompt, Mapping) and isinstance(brief, Mapping):
        prompt = brief.get("writer_prompt")

    if not isinstance(prompt, Mapping):
        return {
            "research_brief": dict(brief) if isinstance(brief, Mapping) else None,
            "writer_prompt": None,
            "writer_prompt_type": None,
        }

    return {
        "research_brief": dict(brief) if isinstance(brief, Mapping) else None,
        "writer_prompt": str(prompt.get("prompt_text") or "").strip() or None,
        "writer_prompt_type": str(prompt.get("prompt_type") or "").strip() or None,
    }


def should_keep_research_candidate(
    article: Mapping[str, Any],
    score: Mapping[str, Any],
    min_score: float = 75.0,
    max_score: Optional[float] = None,
    reference_date: Optional[date] = None,
    recent_notice_days: int = DEFAULT_RECENT_NOTICE_DAYS,
) -> Tuple[bool, str]:
    """Decide whether a scored article should enter the research pool."""

    overall = _score_value(article, score, "overall_score", "article_overall_score")
    if overall is None:
        return False, "missing_score"
    if overall <= min_score:
        return False, "score_below_range"
    if max_score is not None and overall > max_score:
        return False, "score_above_range"

    if not _original_url(article, score):
        return False, "missing_original_url"

    is_notice = _to_bool(_first_non_empty(score.get("is_notice"), article.get("article_is_notice")))
    title = str(_first_non_empty(article.get("title"), score.get("title")) or "")
    looks_admin = bool(UNIMPORTANT_TITLE_RE.search(title))
    if is_notice is True or looks_admin:
        if _is_recent_publish_date(
            article,
            score,
            reference_date=reference_date,
            recent_notice_days=recent_notice_days,
        ):
            return True, f"recent_notice_within_{recent_notice_days}_days"
        if is_notice is True:
            return False, "old_notice_article"
        return False, "old_admin_title"

    if max_score is None:
        return True, f"score_above_{int(min_score)}_and_quality_ready"
    return True, f"score_in_{int(min_score)}_{int(max_score)}_and_quality_ready"


def should_enter_research_after_quality(
    article: Mapping[str, Any],
    score: Mapping[str, Any],
    quality: Mapping[str, Any],
    min_score: float = 75.0,
    max_quality_score: float = 70.0,
    reference_date: Optional[date] = None,
    recent_notice_days: int = DEFAULT_RECENT_NOTICE_DAYS,
) -> Tuple[bool, str]:
    """Route only high-value but low-quality articles into ResearchAgent."""

    keep, reason = should_keep_research_candidate(
        article,
        score,
        min_score=min_score,
        max_score=None,
        reference_date=reference_date,
        recent_notice_days=recent_notice_days,
    )
    if not keep:
        return False, reason
    quality_score = _score_value(quality, quality, "quality_score", "article_quality_score")
    if quality_score is None:
        return False, "missing_quality_score"
    if quality_score >= max_quality_score:
        return False, "quality_high_enough_skip_rewrite"
    return True, f"article_score_above_{int(min_score)}_quality_below_{int(max_quality_score)}"


def build_research_candidate_payload(
    article: Mapping[str, Any],
    score: Optional[Mapping[str, Any]] = None,
    research_result: Optional[Mapping[str, Any]] = None,
    source_database: str = "crawler_ai",
    source_table: str = "crawler_news_main",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    min_score: float = 75.0,
    max_score: Optional[float] = None,
    reference_date: Optional[date] = None,
    recent_notice_days: int = DEFAULT_RECENT_NOTICE_DAYS,
) -> Dict[str, Any]:
    """Build one insert payload for research_article_candidates."""

    article = article if isinstance(article, Mapping) else {}
    score = score if isinstance(score, Mapping) else {}
    keep, reason = should_keep_research_candidate(
        article,
        score,
        min_score=min_score,
        max_score=max_score,
        reference_date=reference_date,
        recent_notice_days=recent_notice_days,
    )
    if not keep:
        raise ValueError(reason)

    prompt_info = extract_writer_prompt(research_result)
    research_status = "generated" if prompt_info["writer_prompt"] else "pending"

    return {
        "source_database": source_database,
        "source_table": source_table,
        "source_article_id": _article_id(article, score),
        "original_url": _original_url(article, score),
        "title": _first_non_empty(article.get("title"), score.get("title")),
        "college_name": article.get("college_name"),
        "specialty_name": article.get("specialty_name"),
        "category": None if article.get("category") is None else str(article.get("category")),
        "publish_date": article.get("publish_date"),
        "word_count": _score_value(article, score, "word_count", "article_word_count"),
        "article_score": _score_value(article, score, "overall_score", "article_overall_score"),
        "title_style_score": _score_value(article, score, "title_style_score", "article_title_style_score"),
        "content_importance_score": _score_value(
            article,
            score,
            "content_importance_score",
            "article_content_importance_score",
        ),
        "raw_content_importance_score": _score_value(
            article,
            score,
            "raw_content_importance_score",
            "article_raw_content_importance_score",
        ),
        "freshness_score": _score_value(article, score, "freshness_score", "article_freshness_score"),
        "is_notice": _to_bool(_first_non_empty(score.get("is_notice"), article.get("article_is_notice"))),
        "keep_reason": reason,
        "score_payload": dict(score),
        "research_status": research_status,
        "research_brief": prompt_info["research_brief"],
        "writer_prompt": prompt_info["writer_prompt"],
        "writer_prompt_type": prompt_info["writer_prompt_type"],
        "prompt_version": prompt_version,
    }


def build_research_candidate_payloads(
    articles: Iterable[Mapping[str, Any]],
    scores_by_article_id: Optional[Mapping[Any, Mapping[str, Any]]] = None,
    research_results_by_article_id: Optional[Mapping[Any, Mapping[str, Any]]] = None,
    min_score: float = 75.0,
    max_score: Optional[float] = None,
    reference_date: Optional[date] = None,
    recent_notice_days: int = DEFAULT_RECENT_NOTICE_DAYS,
) -> Dict[str, Any]:
    """Filter many articles and return candidate payloads plus skipped reasons."""

    candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    scores_by_article_id = scores_by_article_id or {}
    research_results_by_article_id = research_results_by_article_id or {}

    for article in articles:
        article = article if isinstance(article, Mapping) else {}
        article_id = _first_non_empty(article.get("id"), article.get("article_id"), article.get("source_article_id"))
        score = scores_by_article_id.get(article_id) or article
        keep, reason = should_keep_research_candidate(
            article,
            score,
            min_score=min_score,
            max_score=max_score,
            reference_date=reference_date,
            recent_notice_days=recent_notice_days,
        )
        if not keep:
            skipped.append({"article_id": article_id, "title": article.get("title"), "reason": reason})
            continue

        research_result = research_results_by_article_id.get(article_id)
        candidates.append(
            build_research_candidate_payload(
                article,
                score,
                research_result=research_result,
                min_score=min_score,
                max_score=max_score,
                reference_date=reference_date,
                recent_notice_days=recent_notice_days,
            )
        )

    return {"candidates": candidates, "skipped": skipped}


class ResearchCandidateDBWriter:
    """MySQL writer for the research candidate pool."""

    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config or {})
        self.host = self.config.get("host", "localhost")
        self.port = int(self.config.get("port", 3306))
        self.database = validate_identifier(self.config.get("database", DEFAULT_RESEARCH_DATABASE))
        self.user = self.config.get("user", "")
        self.password = self.config.get("password", "")
        self.table = validate_identifier(self.config.get("table", DEFAULT_RESEARCH_TABLE))
        self._conn = None

    async def _get_conn(self):
        if self._conn is None:
            import aiomysql

            self._conn = await aiomysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db=self.database,
                charset="utf8mb4",
                autocommit=False,
            )
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def write_candidates(self, candidates: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        rows = list(candidates)
        if not rows:
            return {"success": True, "inserted_or_updated": 0}

        conn = await self._get_conn()
        query = f"""
            INSERT INTO `{self.table}` (
                source_database,
                source_table,
                source_article_id,
                original_url,
                title,
                college_name,
                specialty_name,
                category,
                publish_date,
                word_count,
                article_score,
                title_style_score,
                content_importance_score,
                raw_content_importance_score,
                freshness_score,
                is_notice,
                keep_reason,
                score_payload,
                research_status,
                research_brief,
                writer_prompt,
                writer_prompt_type,
                prompt_version,
                prompt_generated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON),
                %s, CAST(%s AS JSON), %s, %s, %s,
                CASE WHEN %s IS NULL THEN NULL ELSE NOW() END
            )
            ON DUPLICATE KEY UPDATE
                source_database = VALUES(source_database),
                source_table = VALUES(source_table),
                source_article_id = VALUES(source_article_id),
                title = VALUES(title),
                college_name = VALUES(college_name),
                specialty_name = VALUES(specialty_name),
                category = VALUES(category),
                publish_date = VALUES(publish_date),
                word_count = VALUES(word_count),
                article_score = VALUES(article_score),
                title_style_score = VALUES(title_style_score),
                content_importance_score = VALUES(content_importance_score),
                raw_content_importance_score = VALUES(raw_content_importance_score),
                freshness_score = VALUES(freshness_score),
                is_notice = VALUES(is_notice),
                keep_reason = VALUES(keep_reason),
                score_payload = VALUES(score_payload),
                research_status = VALUES(research_status),
                research_brief = VALUES(research_brief),
                writer_prompt = VALUES(writer_prompt),
                writer_prompt_type = VALUES(writer_prompt_type),
                prompt_version = VALUES(prompt_version),
                prompt_generated_at = VALUES(prompt_generated_at)
        """

        params = [
            (
                row.get("source_database"),
                row.get("source_table"),
                row.get("source_article_id"),
                row.get("original_url"),
                row.get("title"),
                row.get("college_name"),
                row.get("specialty_name"),
                row.get("category"),
                row.get("publish_date"),
                row.get("word_count"),
                row.get("article_score"),
                row.get("title_style_score"),
                row.get("content_importance_score"),
                row.get("raw_content_importance_score"),
                row.get("freshness_score"),
                None if row.get("is_notice") is None else 1 if row.get("is_notice") else 0,
                row.get("keep_reason"),
                _json_dumps(row.get("score_payload")),
                row.get("research_status"),
                _json_dumps(row.get("research_brief")),
                row.get("writer_prompt"),
                row.get("writer_prompt_type"),
                row.get("prompt_version"),
                row.get("writer_prompt"),
            )
            for row in rows
        ]

        async with conn.cursor() as cursor:
            await cursor.executemany(query, params)
            await conn.commit()
            return {"success": True, "inserted_or_updated": cursor.rowcount}


async def write_research_candidates_to_db(
    config: Dict[str, Any],
    candidates: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Convenience function for writing research candidates to MySQL."""

    writer = ResearchCandidateDBWriter(config)
    try:
        return await writer.write_candidates(candidates)
    finally:
        await writer.close()
