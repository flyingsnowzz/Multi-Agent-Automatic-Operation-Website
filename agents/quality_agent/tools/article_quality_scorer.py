"""Score article writing quality and persist QualityAgent results."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional

from agents.topic_agent.tools.article_score_writer import validate_identifier


DEFAULT_QUALITY_DATABASE = "research_article_data"
DEFAULT_QUALITY_TABLE = "article_quality_scores"
DEFAULT_QUALITY_MODEL = "deepseek-chat"
DEFAULT_QUALITY_BASE_URL = "https://api.deepseek.com"
DEFAULT_QUALITY_VERSION = "quality_agent_v1"


ORIGINAL_QUALITY_WEIGHTS = {
    "word_count_score": 0.20,
    "fluency_score": 0.25,
    "structure_score": 0.20,
    "attractiveness_score": 0.25,
    "ai_feel_score": 0.10,
}

GENERATED_QUALITY_WEIGHTS = {
    "word_count_score": 0.10,
    "fluency_score": 0.20,
    "structure_score": 0.20,
    "attractiveness_score": 0.20,
    "ai_feel_score": 0.30,
}


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _clean_db_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    if score <= 1:
        score *= 100
    return max(0.0, min(score, 100.0))


def _extract_json(text: Any) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if "```" in raw:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
        return json.loads(cleaned)


def _text_length(text: Any) -> int:
    return len(str(text or ""))


def _word_count(text: Any) -> int:
    value = str(text or "")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    english = len(re.findall(r"\b[A-Za-z]+\b", value))
    return chinese + english


def _article_id(article: Mapping[str, Any]) -> Any:
    return article.get("article_id") or article.get("id") or article.get("candidate_id")


def _score_word_count(word_count: int) -> float:
    if word_count <= 0:
        return 30.0
    if 900 <= word_count <= 1200:
        return 100.0
    if word_count < 900:
        if word_count >= 700:
            return 80.0 + (word_count - 700) / 200 * 20
        if word_count >= 500:
            return 60.0 + (word_count - 500) / 200 * 20
        if word_count >= 300:
            return 40.0 + (word_count - 300) / 200 * 20
        return max(20.0, word_count / 300 * 40)
    if word_count <= 1500:
        return 100.0 - (word_count - 1200) / 300 * 20
    if word_count <= 2200:
        return 80.0 - (word_count - 1500) / 700 * 30
    return max(30.0, 50.0 - (word_count - 2200) / 1000 * 20)


def _weights_for_article(article: Mapping[str, Any]) -> Dict[str, float]:
    return GENERATED_QUALITY_WEIGHTS if bool(article.get("if_ai_generated")) else ORIGINAL_QUALITY_WEIGHTS


def _normalize_quality_payload(payload: Mapping[str, Any], article: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    article = article if isinstance(article, Mapping) else {}
    dimensions = payload.get("dimensions") if isinstance(payload.get("dimensions"), Mapping) else payload
    text = article.get("content") or article.get("description") or ""
    word_count = _word_count(text)
    word_count_score = round(_score_word_count(word_count), 2)
    ai_probability = round(_clamp_score(payload.get("ai_generated_probability")), 2)
    ai_feel_score = round(100.0 - ai_probability, 2)
    normalized_dimensions = {
        "word_count_score": word_count_score,
        "fluency_score": round(_clamp_score(dimensions.get("fluency_score")), 2),
        "structure_score": round(_clamp_score(dimensions.get("structure_score")), 2),
        "attractiveness_score": round(
            _clamp_score(
                dimensions.get("attractiveness_score")
                if dimensions.get("attractiveness_score") is not None
                else dimensions.get("title_quality_score")
            ),
            2,
        ),
        "ai_feel_score": ai_feel_score,
    }
    weights = _weights_for_article(article)
    overall = payload.get("quality_score")
    if overall is None:
        overall = sum(normalized_dimensions[name] * weight for name, weight in weights.items())
    quality_score = round(_clamp_score(overall), 2)
    return {
        "quality_score": quality_score,
        "dimensions": normalized_dimensions,
        "word_count": word_count,
        "if_ai_generated": bool(article.get("if_ai_generated")),
        "ai_generated_probability": ai_probability,

        "dimension_weights": weights,
        "grade": str(payload.get("grade") or _grade_quality(quality_score)),
        "route": route_by_quality(quality_score),
        "reasons": payload.get("reasons") if isinstance(payload.get("reasons"), list) else [],
        "suggestions": payload.get("suggestions") if isinstance(payload.get("suggestions"), list) else [],
        "rewrite_feedback_prompt": build_rewrite_feedback_prompt(normalized_dimensions, payload),
        "dimension_reasons": (
            payload.get("dimension_reasons")
            if isinstance(payload.get("dimension_reasons"), dict)
            else {}
        ),
        "ai_feel_reason": str(payload.get("ai_feel_reason") or ""),
        "raw": dict(payload),
    }


def _grade_quality(score: float) -> str:
    if score >= 85:
        return "ready"
    if score >= 70:
        return "review"
    return "rewrite"


def route_by_quality(score: Any) -> str:
    """Return route decision after quality scoring."""

    value = _clamp_score(score)
    if value < 70:
        return "needs_research_writer"
    if value < 85:
        return "manual_review"
    return "ready_to_store"


def should_enter_quality(article_score: Any, min_article_score: float = 75.0) -> bool:
    value = _clamp_score(article_score)
    return value > min_article_score


def should_enter_research_writer(article_score: Any, quality_score: Any) -> bool:
    return should_enter_quality(article_score) and _clamp_score(quality_score) < 70.0


def should_retry_writer_quality(quality_score: Any, target_score: float = 85.0) -> bool:
    return _clamp_score(quality_score) < target_score


def build_rewrite_feedback_prompt(dimensions: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """Build feedback for ResearchAgent/WriterAgent when quality is low."""

    suggestions = payload.get("suggestions") if isinstance(payload.get("suggestions"), list) else []
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    dim_reasons = payload.get("dimension_reasons") if isinstance(payload.get("dimension_reasons"), dict) else {}
    ai_feel_reason = str(payload.get("ai_feel_reason") or "")
    weak = []
    labels = {
        "word_count_score": "字数不符合目标",
        "fluency_score": "文字流畅度不足",
        "structure_score": "结构组织不够清楚",
        "attractiveness_score": "标题/开头/内容吸引力不足",
        "ai_feel_score": "AI味较明显",
    }
    for name, label in labels.items():
        score = _clamp_score(dimensions.get(name))
        if score < 75:
            detail = dim_reasons.get(name, "")
            if detail:
                weak.append(f"- {label}：{score:.0f}分 — {detail}")
            else:
                weak.append(f"- {label}：{score:.0f}分")
    if ai_feel_reason and _clamp_score(dimensions.get("ai_feel_score")) < 75:
        weak.append(f"- AI味较明显原因：{ai_feel_reason}")
    if not weak and not suggestions:
        return "质量评分未发现明显短板；如需重写，请保持事实准确并提升自然表达。"
    parts = [
        "请把以下 QualityAgent 扣分点作为下一轮 ResearchAgent/WriterAgent 的硬约束：",
        "\n".join(weak) if weak else "- 没有低于75分的单项，但整体仍需优化。",
    ]
    if reasons:
        parts.append("主要原因：" + "；".join(str(x) for x in reasons[:4]))
    if suggestions:
        parts.append("修改建议：" + "；".join(str(x) for x in suggestions[:5]))
    parts.append(
        "重写时优先解决最低分项；不要新增原文没有的事实；如果 AI 味扣分，请减少模板化路标句、规律短段和空泛升华。"
    )
    return "\n".join(parts)


def build_quality_prompt(article: Mapping[str, Any]) -> str:
    """Build the LLM prompt for article writing quality scoring."""

    title = str(article.get("title") or "")
    content = str(article.get("content") or article.get("description") or "")[:8000]
    source_title = str(article.get("source_title") or "")
    article_score = article.get("article_score")
    if_ai_generated = bool(article.get("if_ai_generated"))
    weights = _weights_for_article(article)
    return json.dumps(
        {
            "task": "请评价这篇文章的成稿质量，而不是评价事件本身重要不重要。",
            "important_distinction": [
                "选题重要性高不代表文章质量高。重大新闻短通稿也可能质量分低。",
                "请重点判断文章是否写得顺、结构是否清楚、标题和开头是否吸引人、是否像真人编辑写的、AI味是否明显。",
                "不要因为学校/机构/事件重要就自动给高质量分。",
            ],
            "if_ai_generated": if_ai_generated,
            "if_ai_generated_explanation": (
                "这篇文章来自 WriterAgent 或重写链路。请重点估计普通人在不仔细阅读时发现它像AI写作的概率。"
                if if_ai_generated
                else "这篇文章来自原始 crawler/首次考核数据。请正常评价其写作质量，AI味只作为较小权重参考。"
            ),
            "score_scale": "所有分数为 0-100。",
            "routing_rule": {
                "0-69": "质量较低，需要进入 ResearchAgent + WriterAgent 重写。",
                "70-84": "质量中等，需要人工审核或轻改。",
                "85-100": "质量较好，可以存入发布候选库。",
            },
            "dimension_weights": weights,
            "dimensions": {
                "word_count_score": "由代码按正文实际字数计算，模型不要返回这个维度。",
                "fluency_score": "语言是否自然流畅，是否有明显翻译腔、硬拼接或读起来费劲。",
                "structure_score": "开头、展开、背景、重点、结尾是否组织清楚，段落是否有自然推进。",
                "attractiveness_score": "标题、开头和正文是否有阅读吸引力，是否能让普通用户愿意继续看。",
                "ai_generated_probability": "普通人在不仔细阅读时发现它像AI写作的概率，0-100；越高越糟糕。",
            },
            "article_context": {
                "article_id": article.get("article_id") or article.get("id") or article.get("candidate_id"),
                "article_score_from_scoring_agent": article_score,
                "source_title": source_title,
                "title": title,
                "content_length": _text_length(content),
                "word_count": _word_count(content),
            },
            "article": {
                "title": title,
                "content": content,
            },
            "return_json_schema": {
                "dimensions": {
                    "fluency_score": "0-100",
                    "structure_score": "0-100",
                    "attractiveness_score": "0-100"
                },
                "dimension_reasons": {
                    "fluency_score": "如果流畅度低于75分，必须说明具体哪里不自然（如翻译腔、句式重复、读起来费劲等）",
                    "structure_score": "如果结构低于75分，必须说明具体哪里组织不清（如开头拖沓、段落推进机械、结尾空泛等）",
                    "attractiveness_score": "如果吸引力低于75分，必须说明具体哪里不够吸引人（如标题平淡、开头无钩子、内容缺乏看点等）"
                },
                "ai_generated_probability": "0-100，普通人粗看发现AI味的概率，越高越糟糕",
                "ai_feel_reason": "如果 ai_generated_probability > 30，必须说明具体哪里像 AI 写的（如模板化路标句、规律短段、空泛升华等）",
                "grade": "ready/review/rewrite",
                "reasons": ["2-5条主要判断依据"],
                "suggestions": ["如果需要改，给2-5条具体建议"],
            },
        },
        ensure_ascii=False,
        default=_json_default,
    )


@dataclass
class QualityLLMConfig:
    api_key: str
    model: str = DEFAULT_QUALITY_MODEL
    base_url: str = DEFAULT_QUALITY_BASE_URL
    temperature: float = 0.1
    timeout: int = 120

    @classmethod
    def from_env(cls) -> "QualityLLMConfig":
        api_key = (
            os.getenv("QUALITY_AGENT_API_KEY")
            or os.getenv("ARTICLE_QUALITY_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        model = (os.getenv("QUALITY_AGENT_MODEL") or os.getenv("ARTICLE_QUALITY_MODEL") or DEFAULT_QUALITY_MODEL).strip()
        base_url = (
            os.getenv("QUALITY_AGENT_BASE_URL")
            or os.getenv("ARTICLE_QUALITY_BASE_URL")
            or DEFAULT_QUALITY_BASE_URL
        ).strip()
        try:
            temperature = float(os.getenv("QUALITY_AGENT_TEMPERATURE", "0.1"))
        except ValueError:
            temperature = 0.1
        return cls(api_key=api_key, model=model, base_url=base_url, temperature=temperature)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


class OpenAICompatibleQualityClient:
    """Small OpenAI-compatible chat client for QualityAgent."""

    def __init__(self, config: Optional[QualityLLMConfig] = None):
        self.config = config or QualityLLMConfig.from_env()

    async def score(self, article: Mapping[str, Any]) -> Dict[str, Any]:
        if not self.config.is_configured:
            raise RuntimeError("quality_agent_api_key_missing")

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )
        resp = await client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "你是 QualityAgent。只评价文章写作质量，不评价事件重要性。必须只输出 JSON 对象。",
                },
                {"role": "user", "content": build_quality_prompt(article)},
            ],
        )
        content = resp.choices[0].message.content if resp.choices else "{}"
        return _normalize_quality_payload(_extract_json(content), article)


def build_quality_output_payload(
    article: Mapping[str, Any],
    quality: Optional[Mapping[str, Any]] = None,
    *,
    source_kind: str,
    model: Optional[str] = None,
    error_message: Optional[str] = None,
    original_quality_score: Optional[float] = None,
    version: str = DEFAULT_QUALITY_VERSION,
) -> Dict[str, Any]:
    quality = quality if isinstance(quality, Mapping) else None
    dimensions = quality.get("dimensions") if isinstance(quality, Mapping) else {}
    quality_score = quality.get("quality_score") if isinstance(quality, Mapping) else None
    status = "scored" if quality and not error_message else "failed"
    return {
        "source_kind": source_kind,
        "source_article_id": article.get("source_article_id"),
        "candidate_id": article.get("candidate_id") or article.get("id"),
        "writer_output_id": article.get("writer_output_id"),
        "original_url": article.get("original_url"),
        "article_score": article.get("article_score"),
        "original_quality_score": (
            original_quality_score
            if original_quality_score is not None
            else article.get("original_quality_score")
        ),
        "title": article.get("title"),
        "content_chars": _text_length(article.get("content") or article.get("description") or ""),
        "word_count": quality.get("word_count") if isinstance(quality, Mapping) else _word_count(article.get("content") or article.get("description") or ""),
        "if_ai_generated": quality.get("if_ai_generated") if isinstance(quality, Mapping) else bool(article.get("if_ai_generated")),
        "quality_status": status,
        "quality_score": quality_score,
        "word_count_score": dimensions.get("word_count_score") if isinstance(dimensions, Mapping) else None,
        "fluency_score": dimensions.get("fluency_score") if isinstance(dimensions, Mapping) else None,
        "structure_score": dimensions.get("structure_score") if isinstance(dimensions, Mapping) else None,
        "attractiveness_score": dimensions.get("attractiveness_score") if isinstance(dimensions, Mapping) else None,
        "ai_feel_score": dimensions.get("ai_feel_score") if isinstance(dimensions, Mapping) else None,

        "ai_generated_probability": (
            quality.get("ai_generated_probability") if isinstance(quality, Mapping) else None
        ),
        "route": quality.get("route") if isinstance(quality, Mapping) else None,
        "rewrite_feedback_prompt": quality.get("rewrite_feedback_prompt") if isinstance(quality, Mapping) else None,
        "quality_payload": dict(quality) if quality else None,
        "quality_model": model,
        "quality_version": version,
        "error_message": error_message,
    }


class ArticleQualityDB:
    """Read articles and write QualityAgent results."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = dict(config or {})
        self.host = self.config.get("host", "localhost")
        self.port = int(self.config.get("port", 3306))
        self.database = validate_identifier(self.config.get("database", DEFAULT_QUALITY_DATABASE))
        self.user = self.config.get("user", "root")
        self.password = self.config.get("password", "")
        self.quality_table = validate_identifier(self.config.get("quality_table", DEFAULT_QUALITY_TABLE))
        self.candidate_table = validate_identifier(self.config.get("candidate_table", "research_article_candidates"))
        self.writer_output_table = validate_identifier(self.config.get("writer_output_table", "writer_article_outputs"))
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

    async def fetch_original_candidates(
        self,
        *,
        limit: int = 10,
        min_article_score: float = 75.0,
        only_missing_quality: bool = True,
    ) -> List[Dict[str, Any]]:
        import aiomysql

        conn = await self._get_conn()
        missing_clause = ""
        if only_missing_quality:
            missing_clause = f"""
                AND NOT EXISTS (
                    SELECT 1 FROM `{self.quality_table}` q
                    WHERE q.source_kind = 'original'
                      AND q.candidate_id = c.id
                      AND q.quality_status = 'scored'
                )
            """
        query = f"""
            SELECT
                c.id AS candidate_id,
                c.source_article_id,
                c.original_url,
                c.title,
                c.article_score,
                c.score_payload,
                c.word_count,
                c.publish_date,
                FALSE AS if_ai_generated,
                n.description AS content
            FROM `{self.candidate_table}` c
            LEFT JOIN article_scoring_newdata.crawler_news_main n ON n.id = c.source_article_id
            WHERE c.article_score > %s
              {missing_clause}
            ORDER BY c.article_score DESC, c.id ASC
            LIMIT %s
        """
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, (float(min_article_score), max(1, int(limit))))
            rows = await cursor.fetchall()
        return [{k: _clean_db_value(v) for k, v in row.items()} for row in rows]

    async def fetch_original_quality_scores(self, candidate_ids: List[int]) -> Dict[int, float]:
        """Fetch original quality_score for given candidate_ids."""
        if not candidate_ids:
            return {}
        import aiomysql
        conn = await self._get_conn()
        placeholders = ",".join(["%s"] * len(candidate_ids))
        query = (
            'SELECT candidate_id, quality_score'
            ' FROM ' + self.quality_table
            + " WHERE source_kind = 'original'"
            + ' AND candidate_id IN (' + placeholders + ')'
        )
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, candidate_ids)
            rows = await cursor.fetchall()
        return {int(row["candidate_id"]): float(row["quality_score"]) for row in rows if row.get("candidate_id")}

    async def fetch_writer_outputs(
        self,
        *,
        limit: int = 10,
        only_missing_quality: bool = True,
        quality_source_kind: str = "writer",
        if_ai_generated: bool = True,
    ) -> List[Dict[str, Any]]:
        import aiomysql

        conn = await self._get_conn()
        missing_clause = ""
        if only_missing_quality:
            missing_clause = f"""
                AND NOT EXISTS (
                    SELECT 1 FROM `{self.quality_table}` q
                    WHERE q.source_kind = %s
                      AND q.candidate_id = o.candidate_id
                      AND q.quality_status = 'scored'
                )
            """
        query = f"""
            SELECT
                o.id AS writer_output_id,
                o.candidate_id,
                o.source_article_id,
                o.original_url,
                o.article_score,
                o.generated_title AS title,
                %s AS if_ai_generated,
                o.generated_content_md AS content
            FROM `{self.writer_output_table}` o
            WHERE o.generation_status = 'generated'
              {missing_clause}
            ORDER BY o.article_score DESC, o.candidate_id ASC
            LIMIT %s
        """
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            params: List[Any] = []
            if only_missing_quality:
                params.append(quality_source_kind)
            params.extend([bool(if_ai_generated), max(1, int(limit))])

            await cursor.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [{k: _clean_db_value(v) for k, v in row.items()} for row in rows]

    async def write_quality_scores(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        items = list(rows)
        if not items:
            return {"success": True, "inserted_or_updated": 0}

        conn = await self._get_conn()
        query = f"""
            INSERT INTO `{self.quality_table}` (
                source_kind,
                source_article_id,
                candidate_id,
                writer_output_id,
                original_url,
                article_score,
                original_quality_score,
                title,
                content_chars,
                word_count,
                if_ai_generated,
                quality_status,
                quality_score,
                word_count_score,
                fluency_score,
                structure_score,
                attractiveness_score,
                ai_feel_score,

                ai_generated_probability,
                route,
                rewrite_feedback_prompt,
                quality_payload,
                quality_model,
                quality_version,
                error_message,
                scored_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s,
                CASE WHEN %s = 'scored' THEN NOW() ELSE NULL END
            )
            ON DUPLICATE KEY UPDATE
                source_article_id = VALUES(source_article_id),
                writer_output_id = VALUES(writer_output_id),
                original_url = VALUES(original_url),
                article_score = VALUES(article_score),
                original_quality_score = VALUES(original_quality_score),
                title = VALUES(title),
                content_chars = VALUES(content_chars),
                word_count = VALUES(word_count),
                if_ai_generated = VALUES(if_ai_generated),
                quality_status = VALUES(quality_status),
                quality_score = VALUES(quality_score),
                word_count_score = VALUES(word_count_score),
                fluency_score = VALUES(fluency_score),
                structure_score = VALUES(structure_score),
                attractiveness_score = VALUES(attractiveness_score),
                ai_feel_score = VALUES(ai_feel_score),

                ai_generated_probability = VALUES(ai_generated_probability),
                route = VALUES(route),
                rewrite_feedback_prompt = VALUES(rewrite_feedback_prompt),
                quality_payload = VALUES(quality_payload),
                quality_model = VALUES(quality_model),
                quality_version = VALUES(quality_version),
                error_message = VALUES(error_message),
                scored_at = VALUES(scored_at)
        """
        params = [
            (
                row.get("source_kind"),
                row.get("source_article_id"),
                row.get("candidate_id"),
                row.get("writer_output_id"),
                row.get("original_url"),
                row.get("article_score"),
                row.get("original_quality_score"),
                row.get("title"),
                row.get("content_chars"),
                row.get("word_count"),
                1 if row.get("if_ai_generated") else 0,
                row.get("quality_status"),
                row.get("quality_score"),
                row.get("word_count_score"),
                row.get("fluency_score"),
                row.get("structure_score"),
                row.get("attractiveness_score"),
                row.get("ai_feel_score"),

                row.get("ai_generated_probability"),
                row.get("route"),
                row.get("rewrite_feedback_prompt"),
                _json_dumps(row.get("quality_payload")),
                row.get("quality_model"),
                row.get("quality_version"),
                row.get("error_message"),
                row.get("quality_status"),
            )
            for row in items
        ]
        async with conn.cursor() as cursor:
            await cursor.executemany(query, params)
            await conn.commit()
            return {"success": True, "inserted_or_updated": cursor.rowcount}


async def score_articles_to_quality_db(
    db_config: Optional[Dict[str, Any]] = None,
    llm_config: Optional[QualityLLMConfig] = None,
    *,
    source_kind: str = "original",
    limit: int = 10,
    concurrency: int = 2,
    min_article_score: float = 75.0,
    only_missing_quality: bool = True,
) -> Dict[str, Any]:
    """Read articles, run QualityAgent, and persist results."""

    db = ArticleQualityDB(db_config)
    client = OpenAICompatibleQualityClient(llm_config)
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    model = client.config.model
    try:
        if source_kind in {"writer", "writer_plain"}:
            articles = await db.fetch_writer_outputs(
                limit=limit,
                only_missing_quality=only_missing_quality,
                quality_source_kind=source_kind,
                if_ai_generated=source_kind == "writer",
            )
            original_scores = await db.fetch_original_quality_scores(
                [a.get("candidate_id") for a in articles if a.get("candidate_id")]
            )
        elif source_kind == "original":
            original_scores = {}
            articles = await db.fetch_original_candidates(
                limit=limit,
                min_article_score=min_article_score,
                only_missing_quality=only_missing_quality,
            )
        else:
            raise ValueError("invalid_source_kind")

        async def one(article: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    quality = await client.score(article)
                    return build_quality_output_payload(
                        article,
                        quality,
                        source_kind=source_kind,
                        model=model,
                        original_quality_score=original_scores.get(article.get("candidate_id")),
                    )
                except Exception as exc:
                    return build_quality_output_payload(
                        article,
                        None,
                        source_kind=source_kind,
                        model=model,
                        error_message=str(exc),
                        original_quality_score=original_scores.get(article.get("candidate_id")),
                    )

        outputs = await asyncio.gather(*(one(article) for article in articles))
        write_result = await db.write_quality_scores(outputs)
        scored = sum(1 for row in outputs if row.get("quality_status") == "scored")
        failed = len(outputs) - scored
        route_counts: Dict[str, int] = {}
        for row in outputs:
            route = str(row.get("route") or "failed")
            route_counts[route] = route_counts.get(route, 0) + 1
        return {
            "success": failed == 0,
            "source_kind": source_kind,
            "read": len(articles),
            "scored": scored,
            "failed": failed,
            "route_counts": route_counts,
            "write_result": write_result,
            "failures": [
                {
                    "candidate_id": row.get("candidate_id"),
                    "title": row.get("title"),
                    "error_message": row.get("error_message"),
                }
                for row in outputs
                if row.get("quality_status") == "failed"
            ],
        }
    finally:
        await db.close()
