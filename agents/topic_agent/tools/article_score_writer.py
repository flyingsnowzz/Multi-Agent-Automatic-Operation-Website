"""Write article scoring results back to crawler_news_main."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_SCORING_VERSION = "article_scoring_v1"


def validate_identifier(value: str) -> str:
    """Allow only simple MySQL identifiers used for table names."""

    if not IDENTIFIER_RE.fullmatch(str(value or "")):
        raise ValueError("invalid_identifier")
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def build_article_score_update_payload(
    score: Dict[str, Any],
    model: Optional[str] = None,
    scoring_version: str = DEFAULT_SCORING_VERSION,
) -> Dict[str, Any]:
    """Convert one ArticleScore dict to DB update payload."""

    return {
        "article_overall_score": score.get("overall_score"),
        "article_title_style_score": score.get("title_style_score"),
        "article_is_notice": (
            None if score.get("is_notice") is None else 1 if score.get("is_notice") else 0
        ),
        "article_notice_score": score.get("notice_score"),
        "article_length_score": score.get("length_score"),
        "article_content_importance_score": score.get("content_importance_score"),
        "article_raw_content_importance_score": score.get("raw_content_importance_score"),
        "article_freshness_score": score.get("freshness_score"),
        "article_freshness_factor": score.get("freshness_factor"),
        "article_freshness_weight_active": 1 if score.get("freshness_weight_active") else 0,
        "article_score_breakdown": _json_dumps(score.get("score_breakdown") or {}),
        "article_word_count": score.get("word_count"),
        "article_topic_count": score.get("topic_count"),
        "article_topics": _json_dumps(score.get("topics") or []),
        "article_score_reasons": _json_dumps(score.get("reasons") or []),
        "article_ai_used": 1 if score.get("ai_used") else 0,
        "article_ai_reason": score.get("ai_reason"),
        "article_scoring_model": model,
        "article_scoring_version": scoring_version,
        "article_id": score.get("article_id"),
    }


class ArticleScoreDBWriter:
    """MySQL writer for article scoring results."""

    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config or {})
        self.host = self.config.get("host", "localhost")
        self.port = int(self.config.get("port", 3306))
        self.database = self.config.get("database", "")
        self.user = self.config.get("user", "")
        self.password = self.config.get("password", "")
        self.table = validate_identifier(self.config.get("table", "crawler_news_main"))
        self.model = self.config.get("model")
        self.scoring_version = self.config.get("scoring_version", DEFAULT_SCORING_VERSION)
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

    async def write_scores(self, article_scores: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """Write many article scores to crawler_news_main."""

        scores = list(article_scores)
        if not scores:
            return {"success": True, "updated": 0}

        conn = await self._get_conn()
        query = f"""
            UPDATE `{self.table}`
            SET
                article_overall_score = %s,
                article_title_style_score = %s,
                article_is_notice = %s,
                article_notice_score = %s,
                article_length_score = %s,
                article_content_importance_score = %s,
                article_raw_content_importance_score = %s,
                article_freshness_score = %s,
                article_freshness_factor = %s,
                article_freshness_weight_active = %s,
                article_score_breakdown = CAST(%s AS JSON),
                article_word_count = %s,
                article_topic_count = %s,
                article_topics = CAST(%s AS JSON),
                article_score_reasons = CAST(%s AS JSON),
                article_ai_used = %s,
                article_ai_reason = %s,
                article_scoring_model = %s,
                article_scoring_version = %s,
                article_scored_at = NOW()
            WHERE id = %s
        """

        params = []
        for score in scores:
            payload = build_article_score_update_payload(
                score,
                model=self.model,
                scoring_version=self.scoring_version,
            )
            if payload["article_id"] is None:
                continue
            params.append(
                (
                    payload["article_overall_score"],
                    payload["article_title_style_score"],
                    payload["article_is_notice"],
                    payload["article_notice_score"],
                    payload["article_length_score"],
                    payload["article_content_importance_score"],
                    payload["article_raw_content_importance_score"],
                    payload["article_freshness_score"],
                    payload["article_freshness_factor"],
                    payload["article_freshness_weight_active"],
                    payload["article_score_breakdown"],
                    payload["article_word_count"],
                    payload["article_topic_count"],
                    payload["article_topics"],
                    payload["article_score_reasons"],
                    payload["article_ai_used"],
                    payload["article_ai_reason"],
                    payload["article_scoring_model"],
                    payload["article_scoring_version"],
                    payload["article_id"],
                )
            )

        if not params:
            return {"success": True, "updated": 0}

        async with conn.cursor() as cursor:
            await cursor.executemany(query, params)
            await conn.commit()
            return {"success": True, "updated": cursor.rowcount}


async def write_article_scores_to_db(
    config: Dict[str, Any],
    article_scores: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Convenience function for writing article scores to MySQL."""

    writer = ArticleScoreDBWriter(config)
    try:
        return await writer.write_scores(article_scores)
    finally:
        await writer.close()
