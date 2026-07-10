"""Score article writing quality and persist QualityAgent results.

Beginner mental model:
    ScoringAgent asks "is this topic/article worth doing?". QualityAgent asks
    "is this written article good enough?". This file contains the real scoring
    logic behind the small QualityAgent facade in quality_agent.py.

Two use cases:
    1. Original crawler article quality:
       if_ai_generated is false, so AI-feel has zero weight.
    2. WriterAgent rewritten draft quality:
       if_ai_generated is true, so AI-feel becomes important.

Important storage rule:
    Workers should store only compact numeric fields in MySQL. Long reasons,
    suggestions, dimension details, and raw payloads should go to JSONL logs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from agents.crawler_processor_agent.tools.url_content_fetcher import URLContentFetcher


DEFAULT_QUALITY_MODEL = "deepseek-chat"
DEFAULT_QUALITY_BASE_URL = "https://api.deepseek.com"
DEFAULT_QUALITY_VERSION = "quality_agent_v1"


def _env_float(name: str, default: float) -> float:
    # Quality thresholds live in .env. Bad values fall back so one typo does not
    # crash imports; tests and workers can still start and reveal the issue.
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


ORIGINAL_QUALITY_WEIGHTS = {
    # Original crawler articles are not punished for "AI feel" because they came
    # from source sites. We care more about whether the original can be forwarded
    # or should be rewritten.
    "word_count_score": 0.20,
    "fluency_score": 0.25,
    "structure_score": 0.20,
    "attractiveness_score": 0.35,
    "ai_feel_score": 0.0,   # 原文不检测AI味
}

GENERATED_QUALITY_WEIGHTS = {
    # Rewritten/generated drafts must be checked for AI smell. A draft can be
    # fluent but still fail if it feels too templated.
    "word_count_score": 0.10,
    "fluency_score": 0.20,
    "structure_score": 0.20,
    "attractiveness_score": 0.20,
    "ai_feel_score": 0.30,
}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _clamp_score(value: Any) -> float:
    # Normalize model scores into 0-100. Some models/tools may return 0.83
    # instead of 83; values <= 1 are interpreted as ratios.
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    if score <= 1:
        score *= 100
    return max(0.0, min(score, 100.0))


def _extract_json(text: Any) -> Dict[str, Any]:
    # LLMs sometimes wrap JSON in ```json fences. Strip the fence and parse the
    # object so callers receive a normal dict.
    raw = str(text or "").strip()
    if "```" in raw:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Remove control characters that sometimes appear in model output and
        # retry once. If it still fails, the caller sees the JSON error.
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
        return json.loads(cleaned)


def _text_length(text: Any) -> int:
    return len(str(text or ""))


def _word_count(text: Any) -> int:
    # Mixed Chinese/English approximation: Chinese characters count one by one,
    # English words count by word boundary. Good enough for scoring bands.
    value = str(text or "")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    english = len(re.findall(r"\b[A-Za-z]+\b", value))
    return chinese + english


def _article_id(article: Mapping[str, Any]) -> Any:
    # Different stages use slightly different id names. Collapse them so logs,
    # tests, and payload builders can all reference one id.
    return article.get("article_id") or article.get("id") or article.get("candidate_id")


def _score_word_count(word_count: int) -> float:
    # Target article length is roughly 900-1200 Chinese/English words/chars.
    # Too short usually means not enough substance; too long can be bloated.
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
    # The same QualityAgent is reused before and after rewrite. This flag tells
    # it which weight profile to use.
    return GENERATED_QUALITY_WEIGHTS if bool(article.get("if_ai_generated")) else ORIGINAL_QUALITY_WEIGHTS


def _normalize_quality_payload(payload: Mapping[str, Any], article: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    # Normalize the model response into one stable shape. The worker code should
    # not care whether the model returned fields at top level or inside
    # "dimensions".
    payload = payload if isinstance(payload, Mapping) else {}
    article = article if isinstance(article, Mapping) else {}
    dimensions = payload.get("dimensions") if isinstance(payload.get("dimensions"), Mapping) else payload
    text = article.get("content") or article.get("description") or ""
    word_count = _word_count(text)
    word_count_score = round(_score_word_count(word_count), 2)
    ai_probability = round(_clamp_score(payload.get("ai_generated_probability")), 2)
    ai_feel_score = round(100.0 - ai_probability, 2)
    normalized_dimensions = {
        # word_count_score is calculated by code, not trusted from the model.
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
        # If the model did not provide an overall score, compute it from the
        # normalized dimensions and the active weight profile.
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
    ready_threshold = _env_float(
        "QUALITY_READY_THRESHOLD",
        _env_float("REWRITE_QUALITY_THRESHOLD", _env_float("QUALITY_PASS_THRESHOLD", 70)),
    )
    pass_threshold = _env_float("QUALITY_PASS_THRESHOLD", 70)
    if score >= ready_threshold:
        return "ready"
    if score > pass_threshold:
        return "review"
    return "rewrite"


def route_by_quality(score: Any) -> str:
    """Return route decision after quality scoring."""

    # Keep this descriptive route aligned with the Redis worker threshold.
    # The worker is still the source of truth for actual stream routing, but the
    # returned route label should not contradict .env during debugging.
    pass_threshold = _env_float("QUALITY_PASS_THRESHOLD", 70)
    ready_threshold = _env_float(
        "QUALITY_READY_THRESHOLD",
        _env_float("REWRITE_QUALITY_THRESHOLD", pass_threshold),
    )
    value = _clamp_score(score)
    if value <= pass_threshold:
        return "needs_research_writer"
    if value < ready_threshold:
        return "manual_review"
    return "ready_to_store"


def should_enter_quality(article_score: Any, min_article_score: Optional[float] = None) -> bool:
    if min_article_score is None:
        min_article_score = _env_float("AI_SCORE_THRESHOLD", 75)
    value = _clamp_score(article_score)
    return value >= min_article_score


def should_enter_research_writer(article_score: Any, quality_score: Any) -> bool:
    pass_threshold = _env_float("QUALITY_PASS_THRESHOLD", 70)
    return should_enter_quality(article_score) and _clamp_score(quality_score) <= pass_threshold


def should_retry_writer_quality(quality_score: Any, target_score: Optional[float] = None) -> bool:
    if target_score is None:
        target_score = _env_float("REWRITE_QUALITY_THRESHOLD", 70)
    return _clamp_score(quality_score) < target_score


def should_discard_after_writer_retry(quality_score: Any, target_score: Optional[float] = None) -> bool:
    """链路二规则：WriterAgent 输出经 QualityAgent 二次评分后，低于 env 阈值则放弃。"""
    if target_score is None:
        target_score = _env_float("REWRITE_QUALITY_THRESHOLD", 70)
    return _clamp_score(quality_score) < target_score


def build_rewrite_feedback_prompt(dimensions: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """Build feedback for ResearchAgent/WriterAgent when quality is low."""

    # This text is not published. It is an internal instruction that explains
    # what the next rewrite attempt should fix.
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
        weak_threshold = _env_float("QUALITY_DIMENSION_WEAK_THRESHOLD", 75)
        if score < weak_threshold:
            detail = dim_reasons.get(name, "")
            if detail:
                weak.append(f"- {label}：{score:.0f}分 — {detail}")
            else:
                weak.append(f"- {label}：{score:.0f}分")
    if ai_feel_reason and _clamp_score(dimensions.get("ai_feel_score")) < _env_float("QUALITY_DIMENSION_WEAK_THRESHOLD", 75):
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

    # QualityAgent must see article body. Prefer content, then source_content,
    # then description. Truncate to keep token cost predictable.
    title = str(article.get("title") or "")
    content = str(article.get("content") or article.get("source_content") or article.get("description") or "")[:8000]
    source_title = str(article.get("source_title") or "")
    article_score = article.get("article_score")
    if_ai_generated = bool(article.get("if_ai_generated"))
    # Original crawler articles skip AI-detection weighting. Rewritten articles
    # enable it, which is why rewrite scores can be lower than original scores.
    skip_ai_detection = not if_ai_generated
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
            "score_scale": "请用百分位法给各维度打分：90-100=前10%顶级出版级，85-89=前20%很优秀，80-84=前35%流畅清晰，75-79=前50%合格，70-74=前65%有小缺陷，65-69=前80%多处不足，55-64=前90%差，<55=前95%很差。要求：分数必须覆盖全量程，不同文章之间要有区分度，不要所有文章给相近的分数。各维度的标准差至少要有10分以上。",
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
        # This is the only live model call used by QualityAgent. The worker has
        # already decided whether this is an original article or rewritten draft
        # by setting article["if_ai_generated"].
        if not self.config.is_configured:
            raise RuntimeError("quality_agent_api_key_missing")

        from openai import AsyncOpenAI

        # AsyncOpenAI works with DeepSeek/OpenAI-compatible APIs as long as
        # base_url and api_key are configured in .env.
        client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )
        # response_format=json_object asks the model provider to return JSON.
        # _extract_json still exists because providers/models sometimes include
        # extra text or malformed wrappers.
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
        # Normalize combines model dimensions with code-owned values such as
        # word_count_score and final weighted quality_score.
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
    # This builder is kept for tests/compatibility and for any future export
    # path that needs a compact quality result. The Redis production path stores
    # only quality_score in MySQL and writes verbose reasons to JSONL logs.
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


"""DB batch helpers were removed. Redis workers call QualityAgent directly."""
