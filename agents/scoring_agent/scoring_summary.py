"""Crawler 文章评分。

这个模块刻意不绑定具体数据库：
- crawler_news_main / crawler_news_0..9 可以先读成 dict 再传入
- 输出的是文章评分列表，不做 topic 排名

给刚开始读代码的人：
    run_langgraph_batch.py 负责“从 MySQL 取文章、写数据库、推进 graph”。
    本文件只负责“给一批文章算分”。也就是说，这里尽量不关心队列、
    MySQL、CMS，只接收 List[dict]，返回一个包含 article_scores 的 dict。

评分大概分四步：
    1. TopicExtractor 从标题/正文里抽几个辅助主题
    2. AIArticleScoringClient 调 LLM，让模型判断标题风格、内容重要性、是否通知
    3. ArticleScorer 把 AI 分数叠加时效惩罚、通知惩罚，算 overall_score
    4. TopicSummarizer 把 dataclass 结果转成普通 dict，交给 runner 使用
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
import logging

logger = logging.getLogger(__name__)
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_SCORE_WEIGHT_PROFILE = {
    # 这是旧/通用 WeightSystem 的默认配置，下面 ARTICLE_SCORE_WEIGHTS 才是
    # 当前 ArticleScorer 实际使用的文章综合分权重。保留这个 profile 是为了
    # 兼容以前直接使用 WeightSystem 的代码。
    "title_style_score": {
        "weight": 0.25,
        "description": "标题风格分，越新颖、越不同质化分越高",
    },
    "notice_score": {
        "weight": 0.05,
        "description": "是否为通知的加分项，非通知/新闻类内容分数更高",
    },
    "content_importance_score": {
        "weight": 0.60,
        "description": "阅读全文后的内容重要性分，短但关键信息明确的文章可以拿高分，并受时效惩罚影响",
    },
    "freshness_score": {
        "weight": 0.10,
        "description": "时效性分，发布时间越近分数越高；两个月内不计入综合分",
    },
}


ARTICLE_SCORE_WEIGHTS = {
    # 这是当前实际综合分权重。注意 content_importance_score 权重最高，
    # 因为我们最关心“这篇文章值不值得被用户阅读/运营转载或改写”。
    "title_style_score": 0.25,
    "notice_score": 0.05,
    "content_importance_score": 0.70,
    "freshness_score": 0.10,
}


def _article_id(article: Dict[str, Any]) -> Any:
    # 不同数据来源的 id 字段名字不完全一样；统一在这里取文章唯一标识。
    return article.get("id") or article.get("news_id") or article.get("original_url")


def _clamp_score(value: Any) -> float:
    # 所有维度分最后都收敛到 0-100。传入 0.8 这种小数时，按 80 分理解。
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    if score <= 1:
        score *= 100
    return max(0.0, min(score, 100.0))


def _env_float(name: str, default: float) -> float:
    """Read a float env setting, falling back when the value is invalid."""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean env setting from common .env true/false spellings."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_factor(name: str, default: float) -> float:
    """Read a 0-1 freshness factor from env."""
    return max(0.0, min(_env_float(name, default), 1.0))


@dataclass
class WeightedScore:
    """权重系统输出。

    total_score:
        加权后的总分。
    dimension_scores:
        每个原始维度被清洗到 0-100 后的分数。
    weighted_breakdown:
        每个维度乘以权重后的贡献值。
    weights:
        实际使用的权重，方便调试。
    """

    total_score: float
    dimension_scores: Dict[str, float]
    weighted_breakdown: Dict[str, float]
    weights: Dict[str, float]


@dataclass
class AIArticleReview:
    """AI 对文章语义价值的评分结果。

    注意：AI 不直接决定最终 overall_score。AI 只给几个语义判断：
    标题好不好、内容重要不重要、是不是通知。最终综合分由本地代码
    在 ArticleScorer 里统一计算。
    """

    title_style_score: Optional[float] = None
    content_importance_score: Optional[float] = None
    is_notice: Optional[bool] = None
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArticleScore:
    """单篇文章评分明细。

    LangGraph batch runner 最终拿到的就是这个结构转成的 dict。
    如果 overall_score 是 None，说明关键 AI 字段缺失，这篇文章不会继续
    往下游 quality/rewrite/publish 走。
    """

    article_id: Any
    title: str
    overall_score: Optional[float]
    title_style_score: Optional[float]
    is_notice: Optional[bool]
    notice_score: Optional[float]
    content_importance_score: Optional[float]
    raw_content_importance_score: Optional[float]
    freshness_score: float
    freshness_factor: float
    freshness_weight_active: bool
    score_breakdown: Dict[str, Optional[float]]
    topic_count: int
    word_count: int
    topics: List[str]
    reasons: List[str] = field(default_factory=list)
    ai_used: bool = False
    ai_reason: Optional[str] = None


class WeightSystem:
    """通用权重系统：接收维度分，输出加权总分。"""

    def __init__(self, profile: Optional[Dict[str, Dict[str, Any]]] = None):
        self.profile = self.normalize_profile(profile or DEFAULT_SCORE_WEIGHT_PROFILE)

    def score(self, dimension_scores: Dict[str, Any]) -> WeightedScore:
        # 这个类是通用加权器。当前 LangGraph scoring 主流程主要使用下面的
        # ArticleScorer._build_breakdown()，但保留这里给测试和工具调用使用。
        clean_scores = {
            name: self._clamp_score(dimension_scores.get(name, 0))
            for name in self.profile
        }
        weighted_breakdown = {
            name: round(clean_scores[name] * cfg["weight"], 4)
            for name, cfg in self.profile.items()
        }
        total = round(sum(weighted_breakdown.values()), 2)
        return WeightedScore(
            total_score=total,
            dimension_scores={k: round(v, 2) for k, v in clean_scores.items()},
            weighted_breakdown=weighted_breakdown,
            weights={name: cfg["weight"] for name, cfg in self.profile.items()},
        )

    @staticmethod
    def normalize_profile(profile: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        # 用户传进来的权重不一定刚好加起来等于 1。这里做归一化，避免
        # 某个配置文件权重总和写错导致总分比例失真。
        total_weight = sum(float(cfg.get("weight", 0)) for cfg in profile.values())
        if total_weight <= 0:
            profile = DEFAULT_SCORE_WEIGHT_PROFILE
            total_weight = sum(float(cfg.get("weight", 0)) for cfg in profile.values())
        return {
            name: {
                **cfg,
                "weight": round(float(cfg.get("weight", 0)) / total_weight, 6),
            }
            for name, cfg in profile.items()
        }

    @staticmethod
    def _clamp_score(value: Any) -> float:
        return _clamp_score(value)


class AIArticleScoringClient:
    """OpenAI-compatible 文章评分客户端。

    默认读取环境变量：
    - ARTICLE_SCORING_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY
    - ARTICLE_SCORING_MODEL，可选，默认 gpt-4o-mini
    - ARTICLE_SCORING_BASE_URL / OPENAI_BASE_URL，可选，默认 https://api.openai.com/v1
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
    ):
        self.api_key = (
            api_key
            or os.getenv("ARTICLE_SCORING_API_KEY", "")
            or os.getenv("DEEPSEEK_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        )
        self.model = model or os.getenv("ARTICLE_SCORING_MODEL", "gpt-4o-mini")
        self.base_url = (
            base_url
            or os.getenv("ARTICLE_SCORING_BASE_URL", "")
            or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def review_article(
        self,
        article: Dict[str, Any],
        candidate_topics: List[str],
    ) -> Optional[AIArticleReview]:
        # This is the external-model part of ScoringAgent. It does not decide
        # final routing by itself. It only asks the model for semantic signals;
        # ArticleScorer later combines those signals with local freshness/notice
        # rules into overall_score.
        # 没有 API key 时直接返回 None。上层会把这篇文章视为 AI 未评分，
        # 不会硬编一个分数。
        if not self.enabled:
            return None

        # 使用 OpenAI-compatible /chat/completions 接口。DeepSeek、OpenAI、
        # 以及其他兼容服务都可以通过 base_url + api_key 接入。
        payload = {
            "model": self.model,
            "temperature": 0.1,
            # JSON response is important because downstream parsing is strict:
            # missing fields make overall_score None instead of silently routing.
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是内容运营团队的文章评分助手。"
                        "只返回 JSON，不要输出 Markdown。"
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_prompt(article, candidate_topics),
                },
            ],
        }
        request = urllib.request.Request(
            # urllib is used here so this scorer stays lightweight and sync.
            # The LangGraph batch runner calls it in asyncio.to_thread().
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            # 网络/超时/JSON 错误不在这里抛出，避免一篇文章拖死整批评分。
            return None

        content = (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "{}")
        )
        # 模型被要求输出 JSON。解析失败时返回 None，后续 ArticleScorer 会
        # 让 overall_score 保持 None，而不是猜一个分数。
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return None
        return self._parse_review(data)

    def _build_prompt(self, article: Dict[str, Any], candidate_topics: List[str]) -> str:
        # ScoringAgent 必须看原文。优先 source_content，其次 content/description。
        # 截断到 6000 字符是为了控制 token 成本和请求体大小。
        content = str(article.get("source_content") or article.get("content") or article.get("description") or "")[:6000]
        # The prompt is JSON text, not free-form markdown. That makes the task,
        # article fields, scoring scale, and return schema visually separate for
        # the model, and easier to inspect in prompt logs.
        return json.dumps(
            {
                "task": "你是一个严格的内容筛选器。请按百分位法打分：想象你把所有文章按质量排序，这篇文章排在什么位置？\n\n评分锚点（连续尺度，每 5 分一个台阶）：\n• 95-100：前 5%。诺贝尔奖级别、国家级重大突破\n• 90-94：前 10%。学科级突破、重大排名跃升\n• 85-89：前 20%。深度报道、重要人事任命、高传播价值故事\n• 80-84：前 35%。有信息量的政策解读、创新合作\n• 75-79：前 50%。普通新闻、活动报道\n• 70-74：前 65%。简短会议报道、合作签约\n• 60-69：前 80%。通告、低信息量动态\n• 40-59：前 93%。空洞转载\n• 0-39：垃圾\n\n关键要求：分数必须覆盖全量程，不能只在几个区间内打转。如果你发现大部分文章都在 75-84 或 95+，说明锚点使用不正确——普通新闻用 70-79，深度内容用 80-89，突破性内容才用 90+。",
                "scoring_scale": "连续百分位尺度 0-100，不是分档。请拉开分数差距。",
                "content_importance_guidelines": [
                    "使用百分位思维：排名前 5% 的文章才给 95+；普通校内新闻（会议、签约、活动）应该落在 60-74 区间；只有真正具有传播价值的深度内容才给 80+。",
                    "政策变化、行业规则、项目进展、产品发布、技术突破、重要机构动态等实用信息，可以给高重要性分。",
                    "故事类内容也可以给高重要性分：例如人物/团队的心路历程、科研突破背后的故事、成长经历、团队奋斗过程、项目发展故事。只要具备人物性、叙事性、传播性或情绪价值，用户可能愿意阅读，就不应因为不是通知或政策而低估。",
                    "泛泛会议、普通活动回顾、缺少明确看点的机构动态，应给较低重要性分。",
                ],
                "candidate_topics": candidate_topics,
                "article": {
                    "title": article.get("title") or "",
                    "keywords": article.get("keywords") or "",
                    "description": article.get("description") or "",
                    "content_full_or_excerpt": content,
                    "category": article.get("category"),
                    "publish_date": article.get("publish_date") or article.get("published_at"),
                },
                "return_json_schema": {
                    "title_style_score": "标题是否清晰、具体、有信息量，0-100",
                    "content_importance_score": "百分位法，每5分一个台阶：95-100顶级突破，90-94重大成果，85-89深度报道，80-84政策解读，75-79普通新闻，70-74会议签约，60-69通告，<60低质。请覆盖全量程，不要跳跃区间。",
                    "is_notice": (
                        "boolean。通知/公告/公示/须知/提示/名单/办法/细则等流程性内容为 true；"
                        "新闻报道、政策解读、行业动态、趋势分析等更适合内容运营的文章为 false"
                    ),
                    "reason": "一句话说明",
                },
            },
            ensure_ascii=False,
        )

    def _parse_review(self, data: Dict[str, Any]) -> AIArticleReview:
        # 只抽取后续计算需要的字段。raw 保留完整模型返回，方便 JSONL 调试。
        return AIArticleReview(
            title_style_score=self._optional_score(data.get("title_style_score")),
            content_importance_score=self._optional_score(data.get("content_importance_score")),
            is_notice=self._optional_bool(data.get("is_notice")),
            reason=str(data.get("reason") or ""),
            raw=data,
        )

    def _optional_score(self, value: Any) -> Optional[float]:
        # Optional means: 模型没给就返回 None。None 会阻止最终综合分生成，
        # 这样比默默按 0 分处理更容易发现模型输出异常。
        if value is None:
            return None
        return _clamp_score(value)

    def _optional_bool(self, value: Any) -> Optional[bool]:
        # LLM 可能返回 true/false，也可能返回“是/否/通知/新闻”等中文。
        # 这里统一转成 Python bool。
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "y", "是", "通知"}:
                return True
            if text in {"false", "0", "no", "n", "否", "新闻"}:
                return False
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        return None


class TopicExtractor:
    """Extract lightweight scoring labels from article fields.

    Current behavior:
        The LangGraph pipeline is article-generic, so labels are derived only
        from the article's own keywords/category/title fields.
    """

    def __init__(self, topic_rules: Optional[Dict[str, List[str]]] = None):
        self.topic_rules = topic_rules or {}

    def extract(self, article: Dict[str, Any], max_topics: int = 5) -> List[Tuple[str, float, List[str]]]:
        # These labels are not SEO keywords and do not decide routing. They are
        # only scoring hints passed to the AI review prompt.
        candidates: List[Tuple[str, float, List[str]]] = []

        category_label = self._category_label(article)
        if category_label:
            candidates.append((category_label, 65.0, ["category"]))

        for keyword in self._keyword_labels(article):
            candidates.append((keyword, 75.0, ["keywords"]))

        if self.topic_rules:
            text = self._article_text(article)
        for topic, terms in self.topic_rules.items():
            matched = [term for term in terms if term and self._term_matches(text, term)]
            if matched:
                confidence = min(95.0, 60.0 + len(matched) * 12.0)
                candidates.append((topic, confidence, matched))

        fallback = self._fallback_topic(article)
        if fallback:
            candidates.append((fallback, 50.0, ["fallback"]))

        merged: Dict[str, Tuple[float, List[str]]] = {}
        for topic, confidence, matched_terms in candidates:
            current_conf, current_terms = merged.get(topic, (0.0, []))
            merged[topic] = (
                max(current_conf, confidence),
                sorted(set(current_terms + matched_terms)),
            )

        ranked = [
            (topic, confidence, terms)
            for topic, (confidence, terms) in merged.items()
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:max_topics]

    def _category_label(self, article: Dict[str, Any]) -> str:
        for key in ("category_name", "category_title", "channel_name", "section", "source_name"):
            value = str(article.get(key) or "").strip()
            if value:
                return value[:24]
        category = article.get("category")
        if isinstance(category, str) and not category.isdigit() and category.strip():
            return category.strip()[:24]
        return ""

    def _keyword_labels(self, article: Dict[str, Any]) -> List[str]:
        raw = article.get("keywords") or article.get("tags") or article.get("keyword") or ""
        if isinstance(raw, list):
            values = raw
        else:
            values = re.split(r"[,，;；|、\s]+", str(raw))
        labels: List[str] = []
        for item in values:
            label = str(item or "").strip()
            if len(label) < 2 or label in labels:
                continue
            labels.append(label[:24])
            if len(labels) >= 4:
                break
        return labels

    def _article_text(self, article: Dict[str, Any]) -> str:
        # 优先使用从 original_url 抓取的 source_content
        source = article.get("source_content") or article.get("description") or article.get("content") or ""
        parts = [
            article.get("title"),
            article.get("keywords"),
            source,
        ]
        return " ".join(str(part) for part in parts if part)

    def _fallback_topic(self, article: Dict[str, Any]) -> str:
        # If category/keywords are unavailable, use the first readable title
        # phrase so the scoring prompt still has minimal context.
        # 这不是精确 SEO，只是为了让评分 prompt 不至于完全没有主题上下文。
        title = str(article.get("title") or "").strip()
        if not title:
            return "未分类主题"

        cleaned = re.sub(r"[【】\[\]（）()《》:：,，.!！?？\-_\s]+", " ", title)
        words = [item for item in cleaned.split(" ") if len(item) >= 2]
        return words[0][:16] if words else title[:16]

    def _term_matches(self, text: str, term: str) -> bool:
        # 英文/数字词用单词边界，避免短词命中到别的长字符串内部。
        # 中文词没有空格边界，直接做包含判断。
        if re.fullmatch(r"[A-Za-z0-9]+", term):
            pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        return term.lower() in text.lower()


class ArticleScorer:
    """按标题风格、文章长度、内容重要性、时效性给每篇 crawler 文章打分。"""

    def __init__(
        self,
        extractor: TopicExtractor,
        ideal_min_words: int = 200,
        ideal_max_words: int = 1800,
        ai_client: Optional[Any] = None,
        ai_concurrency: int = 1,
    ):
        # ai_concurrency 控制一次 batch 内并发多少个 LLM 评分请求。
        # 它和 LangGraph 文章批大小不是一回事。
        self.extractor = extractor
        self.ideal_min_words = ideal_min_words
        self.ideal_max_words = ideal_max_words
        self.ai_client = ai_client
        self.ai_concurrency = max(1, int(ai_concurrency or 1))

    def score_articles(
        self,
        articles: List[Dict[str, Any]],
        extracted_by_id: Dict[Any, List[Tuple[str, float, List[str]]]],
        manual_article_scores: Optional[Dict[Any, Dict[str, Any]]] = None,
    ) -> Dict[Any, ArticleScore]:
        # Main scoring entry for a batch. run_langgraph_batch.py calls this
        # through summarize_crawler_topics(), then routes by overall_score.
        manual_article_scores = manual_article_scores or {}
        # 先批量跑 AI 评审，再做本地加权计算。这样能用线程池并发调用 LLM。
        ai_reviews_by_id = self._review_articles_with_ai(
            articles=articles,
            extracted_by_id=extracted_by_id,
            manual_article_scores=manual_article_scores,
        )
        scores = {}
        for article in articles:
            # Everything inside this loop is per-article scoring. One bad or
            # incomplete article should not prevent other batch items scoring.
            article_id = _article_id(article)
            extracted_topics = extracted_by_id.get(article_id, [])
            if article_id in manual_article_scores:
                # 手动分数主要用于测试、回放、或者人为修正某些样本。
                # 命中后不再调用 AI。
                scores[article_id] = self._manual_score(
                    article=article,
                    extracted_topics=extracted_topics,
                    scores=manual_article_scores[article_id],
                )
                continue

            word_count = self._count_words(article)
            freshness, freshness_factor, freshness_weight_active = self._freshness_policy(article)
            ai_review = ai_reviews_by_id.get(article_id)
            # AI 只直接判断几个语义维度；综合分由本地规则统一计算，
            # 这样阈值、权重、时效惩罚都更稳定可控。
            title_style = ai_review.title_style_score if ai_review else None
            raw_content_importance = ai_review.content_importance_score if ai_review else None
            content_importance = (
                # 老文章的重要性会被 freshness_factor 打折。比如一年多以前的
                # 普通动态，即使模型觉得内容不错，也不应该占用生产流水线。
                raw_content_importance * freshness_factor
                if raw_content_importance is not None
                else None
            )
            is_notice = ai_review.is_notice if ai_review else None
            notice_score = self._score_notice(is_notice)

            breakdown = self._build_breakdown(
                title_style=title_style,
                notice_score=notice_score,
                content_importance=content_importance,
                freshness=freshness,
                freshness_weight_active=freshness_weight_active,
            )
            overall = (
                # overall_score 是各个加权项求和。只要 AI 没返回关键字段，
                # overall_score 就保持 None，让 worker 不继续往下游推。
                round(min(100.0, sum(value for value in breakdown.values() if value is not None)), 2)
                if title_style is not None
                and content_importance is not None
                and notice_score is not None
                else None
            )

            # ArticleScore 同时保留原始 AI 分、折算后分、分项贡献和原因。
            # worker 只用 overall_score 路由，但日志/人工复盘会用这些字段。
            scores[article_id] = ArticleScore(
                article_id=article_id,
                title=str(article.get("title") or ""),
                overall_score=overall,
                title_style_score=round(title_style, 2) if title_style is not None else None,
                is_notice=is_notice,
                notice_score=round(notice_score, 2) if notice_score is not None else None,
                content_importance_score=(
                    round(content_importance, 2) if content_importance is not None else None
                ),
                raw_content_importance_score=(
                    round(raw_content_importance, 2)
                    if raw_content_importance is not None
                    else None
                ),
                freshness_score=round(freshness, 2),
                freshness_factor=round(freshness_factor, 2),
                freshness_weight_active=freshness_weight_active,
                score_breakdown=breakdown,
                ai_used=bool(
                    title_style is not None
                    and raw_content_importance is not None
                    and is_notice is not None
                ),
                ai_reason=ai_review.reason if ai_review else None,
                topic_count=len(extracted_topics),
                word_count=word_count,
                topics=[topic for topic, _, _ in extracted_topics],
                reasons=self._score_reasons(
                    title_style,
                    is_notice,
                    content_importance,
                    raw_content_importance,
                    freshness,
                    freshness_factor,
                    freshness_weight_active,
                ),
            )
        return scores

    def _review_articles_with_ai(
        self,
        articles: List[Dict[str, Any]],
        extracted_by_id: Dict[Any, List[Tuple[str, float, List[str]]]],
        manual_article_scores: Dict[Any, Dict[str, Any]],
    ) -> Dict[Any, Optional[AIArticleReview]]:
        if not self.ai_client:
            return {}

        # 手动分数的文章不再调用 AI，方便测试和修正个别样本。
        pending = [
            article
            for article in articles
            if _article_id(article) not in manual_article_scores
        ]
        if self.ai_concurrency <= 1:
            # 单线程路径更容易调试，也避免小批量时线程池开销。
            return {
                _article_id(article): self._review_with_ai(
                    article,
                    extracted_by_id.get(_article_id(article), []),
                )
                for article in pending
            }

        reviews: Dict[Any, Optional[AIArticleReview]] = {}
        with ThreadPoolExecutor(max_workers=self.ai_concurrency) as executor:
            # 并发调用 LLM，但每篇文章失败只影响自己，不影响整批。
            future_map = {
                executor.submit(
                    self._review_with_ai,
                    article,
                    extracted_by_id.get(_article_id(article), []),
                ): _article_id(article)
                for article in pending
            }
            for future in as_completed(future_map):
                article_id = future_map[future]
                try:
                    reviews[article_id] = future.result()
                except Exception:
                    reviews[article_id] = None
        return reviews

    def _manual_score(
        self,
        article: Dict[str, Any],
        extracted_topics: List[Tuple[str, float, List[str]]],
        scores: Dict[str, Any],
    ) -> ArticleScore:
        # Manual score follows the same local weighting rules as AI score. That
        # keeps tests and production scoring comparable.
        title_style = _clamp_score(scores.get("title_style_score", 0))
        raw_content_importance = _clamp_score(
            scores.get("raw_content_importance_score", scores.get("content_importance_score", 0))
        )
        freshness, freshness_factor, freshness_weight_active = self._freshness_policy(article)
        if "freshness_score" in scores:
            freshness = _clamp_score(scores.get("freshness_score"))
        if "freshness_factor" in scores:
            freshness_factor = max(0.0, min(float(scores.get("freshness_factor") or 0), 1.0))
        content_importance = raw_content_importance * freshness_factor
        is_notice = scores.get("is_notice")
        if is_notice is None:
            is_notice = False
        is_notice = bool(is_notice)
        notice_score = self._score_notice(is_notice)
        overall = scores.get("overall_score")
        breakdown = self._build_breakdown(
            title_style=title_style,
            notice_score=notice_score,
            content_importance=content_importance,
            freshness=freshness,
            freshness_weight_active=freshness_weight_active,
        )
        if overall is None:
            overall_score = round(
                min(100.0, sum(value for value in breakdown.values() if value is not None)),
                2,
            )
        else:
            overall_score = _clamp_score(overall)
        return ArticleScore(
            article_id=_article_id(article),
            title=str(article.get("title") or ""),
            overall_score=round(overall_score, 2),
            title_style_score=round(title_style, 2),
            is_notice=is_notice,
            notice_score=round(notice_score, 2) if notice_score is not None else None,
            content_importance_score=round(content_importance, 2),
            raw_content_importance_score=round(raw_content_importance, 2),
            freshness_score=round(freshness, 2),
            freshness_factor=round(freshness_factor, 2),
            freshness_weight_active=freshness_weight_active,
            score_breakdown=breakdown,
            ai_used=False,
            ai_reason=None,
            topic_count=len(extracted_topics),
            word_count=self._count_words(article),
            topics=[topic for topic, _, _ in extracted_topics],
            reasons=["使用手动文章评分"],
        )

    def _build_breakdown(
        self,
        title_style: Optional[float],
        notice_score: Optional[float],
        content_importance: Optional[float],
        freshness: float,
        freshness_weight_active: bool,
    ) -> Dict[str, Optional[float]]:
        active_weights = dict(ARTICLE_SCORE_WEIGHTS)
        if not freshness_weight_active:
            # 两个月内的文章不让 freshness 参与综合分，否则新文章会被重复奖励。
            # 去掉 freshness 权重后，把剩余权重重新归一化到 100%。
            active_weights.pop("freshness_score", None)
            total = sum(active_weights.values())
            active_weights = {name: weight / total for name, weight in active_weights.items()}
        return {
            "title_style_score": self._weighted("title_style_score", title_style, active_weights),
            "notice_score": self._weighted("notice_score", notice_score, active_weights),
            "content_importance_score": self._weighted(
                "content_importance_score",
                content_importance,
                active_weights,
            ),
            "freshness_score": (
                self._weighted("freshness_score", freshness, active_weights)
                if freshness_weight_active
                else None
            ),
        }

    def _weighted(
        self,
        name: str,
        score: Optional[float],
        weights: Optional[Dict[str, float]] = None,
    ) -> Optional[float]:
        # None means this dimension is unavailable and should not silently become
        # a normal low score. Missing AI fields should be visible upstream.
        if score is None:
            return None
        return round(score * (weights or ARTICLE_SCORE_WEIGHTS)[name], 4)

    def _score_notice(self, is_notice: Optional[bool]) -> Optional[float]:
        # 通知/公告类内容不一定没价值，但运营改写价值通常更低，所以这里给
        # notice_score=0；新闻/动态类给 100，再乘以很小的 0.05 权重。
        if is_notice is None:
            return None
        return 0.0 if is_notice else 100.0

    def _review_with_ai(
        self,
        article: Dict[str, Any],
        extracted_topics: List[Tuple[str, float, List[str]]],
    ) -> Optional[AIArticleReview]:
        # Build candidate topic strings from TopicExtractor output, then pass
        # both article and topics to the LLM scoring client.
        if not self.ai_client:
            return None
        candidate_topics = [topic for topic, _, _ in extracted_topics]
        try:
            return self.ai_client.review_article(article, candidate_topics)
        except Exception:
            return None

    def _count_words(self, article: Dict[str, Any]) -> int:
        # This count is rough but stable: Chinese characters + English words.
        # It is used for diagnostics, not as a major scoring dimension here.
        content = str(article.get("content") or article.get("description") or "")
        if not content:
            content = str(article.get("title") or "")
        chinese = len(re.findall(r"[\u4e00-\u9fff]", content))
        english = len(re.findall(r"\b[A-Za-z]+\b", content))
        return chinese + english

    def _freshness_policy(self, article: Dict[str, Any]) -> Tuple[float, float, bool]:
        # 返回三个值：
        # - freshness: 时效性本身 0-100
        # - freshness_factor: 对内容重要性的折扣
        # - freshness_weight_active: freshness 是否参与综合分
        #
        # Operators can tune the policy in .env without editing code:
        #   ARTICLE_SCORING_FRESHNESS_IMPORTANCE_WEIGHT_ENABLED=false disables
        #   the content_importance discount while keeping freshness_score itself.
        #   ARTICLE_SCORING_FRESHNESS_FACTOR_* adjusts the discount bands.
        importance_weight_enabled = _env_bool(
            "ARTICLE_SCORING_FRESHNESS_IMPORTANCE_WEIGHT_ENABLED",
            True,
        )

        def factor(name: str, default: float) -> float:
            if not importance_weight_enabled:
                return 1.0
            return _env_factor(name, default)

        recent_months = _env_float("ARTICLE_SCORING_FRESHNESS_RECENT_MONTHS", 2.0)
        mid_months = _env_float("ARTICLE_SCORING_FRESHNESS_MID_MONTHS", 6.0)
        old_months = _env_float("ARTICLE_SCORING_FRESHNESS_OLD_MONTHS", 12.0)
        very_old_months = _env_float("ARTICLE_SCORING_FRESHNESS_VERY_OLD_MONTHS", 24.0)
        stale_months = _env_float("ARTICLE_SCORING_FRESHNESS_STALE_MONTHS", 36.0)
        if not (0 < recent_months < mid_months < old_months < very_old_months < stale_months):
            recent_months, mid_months, old_months, very_old_months, stale_months = 2.0, 6.0, 12.0, 24.0, 36.0
        # Crawler rows may contain several date-ish fields. Do not stop at the
        # first non-empty value, because some sources store placeholders such as
        # 0000-00-00 in publish_time while publish_date is valid.
        parsed = None
        for date_key in ("publish_time", "publish_date", "published_at", "ctime", "created_at"):
            parsed = self._parse_date(article.get(date_key))
            if parsed:
                break
        if not parsed:
            # Unknown publish date is treated as mediocre freshness and applies
            # a 0.5 content importance discount.
            return 50.0, factor("ARTICLE_SCORING_FRESHNESS_FACTOR_UNKNOWN", 0.5), True
        days = max(0, (datetime.now() - parsed).days)
        months = days / 30.4375
        if months <= recent_months:
            # Very recent articles are already timely, so freshness is not added
            # as an extra weighted dimension. This avoids double-counting recency.
            return 100.0, factor("ARTICLE_SCORING_FRESHNESS_FACTOR_RECENT", 1.0), False
        if months <= mid_months:
            freshness = self._interpolate(months, recent_months, mid_months, 100, 80)
            return freshness, factor("ARTICLE_SCORING_FRESHNESS_FACTOR_MID", 0.8), True
        if months <= old_months:
            freshness = self._interpolate(months, mid_months, old_months, 80, 60)
            return freshness, factor("ARTICLE_SCORING_FRESHNESS_FACTOR_OLD", 0.5), True
        if months <= very_old_months:
            freshness = self._interpolate(months, old_months, very_old_months, 60, 30)
            return freshness, factor("ARTICLE_SCORING_FRESHNESS_FACTOR_VERY_OLD", 0.5), True
        if months <= stale_months:
            freshness = self._interpolate(months, very_old_months, stale_months, 30, 0)
            return freshness, factor("ARTICLE_SCORING_FRESHNESS_FACTOR_STALE", 0.1), True
        return 0.0, factor("ARTICLE_SCORING_FRESHNESS_FACTOR_ANCIENT", 0.1), True

    def _score_freshness(self, article: Dict[str, Any]) -> float:
        return self._freshness_policy(article)[0]

    def _interpolate(
        self,
        value: float,
        start_value: float,
        end_value: float,
        start_score: float,
        end_score: float,
    ) -> float:
        # Linear interpolation helper for freshness decay. Example:
        # between 2 and 6 months, freshness fades from 100 to 80.
        if end_value <= start_value:
            return end_score
        ratio = (value - start_value) / (end_value - start_value)
        score = start_score + ratio * (end_score - start_score)
        return max(min(start_score, end_score), min(max(start_score, end_score), score))

    def _parse_date(self, value: Any) -> Optional[datetime]:
        # Accept common crawler date formats. If parsing fails, caller applies
        # the unknown-date freshness policy.
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value))
            except (OSError, ValueError):
                return None
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        return None

    def _score_reasons(
        self,
        title_style: Optional[float],
        is_notice: Optional[bool],
        content_importance: Optional[float],
        raw_content_importance: Optional[float],
        freshness: float,
        freshness_factor: float,
        freshness_weight_active: bool,
    ) -> List[str]:
        # Human-readable local reasons. These are useful in JSONL audit/debug
        # output, but long reason text should not be stored in MySQL.
        reasons = []
        if title_style is None:
            reasons.append("AI未返回标题风格分")
        else:
            reasons.append("标题较新颖" if title_style >= 75 else "标题同质化偏高")
        if is_notice is None:
            reasons.append("AI未返回是否通知判断")
        elif is_notice:
            reasons.append("属于通知/公告类内容，通知分不加分")
        else:
            reasons.append("属于新闻/动态类内容，获得非通知加分")
        if content_importance is None or raw_content_importance is None:
            reasons.append("AI未返回内容重要性分")
        else:
            reasons.append(
                f"原始重要性{raw_content_importance:.0f}分，按时效系数{freshness_factor:.1f}折算"
            )
        if freshness_weight_active:
            reasons.append("发布时间较近" if freshness >= 80 else "发布时间较早，触发时效惩罚")
        else:
            reasons.append("发布时间在两个月内，时效分不参与综合分")
        return reasons


class TopicSummarizer:
    """遍历文章、抽取辅助主题，并输出文章评分。"""

    def __init__(
        self,
        weight_profile: Optional[Dict[str, Dict[str, Any]]] = None,
        topic_rules: Optional[Dict[str, List[str]]] = None,
        use_ai: bool = True,
        ai_client: Optional[Any] = None,
        ai_config: Optional[Dict[str, Any]] = None,
        ai_concurrency: Optional[int] = None,
    ):
        # TopicSummarizer wires together topic extraction + article scoring.
        # It is intentionally small so summarize_crawler_topics() can be the
        # simple public entry point used by workers.
        self.extractor = TopicExtractor(topic_rules)
        ai_config = ai_config or {}
        if ai_client is None and use_ai:
            ai_client = AIArticleScoringClient(
                api_key=ai_config.get("api_key"),
                model=ai_config.get("model"),
                base_url=ai_config.get("base_url"),
                timeout=int(ai_config.get("timeout", 30)),
            )
        concurrency = ai_concurrency
        if concurrency is None:
            concurrency = int(ai_config.get("concurrency", 1))
        self.article_scorer = ArticleScorer(
            self.extractor,
            ai_client=ai_client,
            ai_concurrency=concurrency,
        )

    def summarize(
        self,
        articles: Iterable[Dict[str, Any]],
        manual_article_scores: Optional[Dict[Any, Dict[str, Any]]] = None,
        output_count: int = 20,
    ) -> Dict[str, Any]:
        article_list = list(articles)
        manual_article_scores = manual_article_scores or {}
        extracted_by_id: Dict[Any, List[Tuple[str, float, List[str]]]] = {}

        # 先为每篇文章抽主题，再把主题作为上下文喂给 AI 评分。
        for article in article_list:
            article_id = _article_id(article)
            extracted_by_id[article_id] = self.extractor.extract(article)

        article_scores = self.article_scorer.score_articles(
            articles=article_list,
            extracted_by_id=extracted_by_id,
            manual_article_scores=manual_article_scores,
        )

        scored_articles = [
            asdict(article_scores[_article_id(article)])
            for article in article_list
        ]
        # 这里会把同一批次的分数拉开到 75-100 区间。它会影响阈值通过率，
        # 所以如果以后发现评分整体偏高/偏低，优先检查这个函数。
        scored_articles = self._stretch_scores(scored_articles)

        return {
            "article_scores": scored_articles,
            "summary": {
                "article_count": len(article_list),
                "scored_count": len(scored_articles),
                "ai_used_count": sum(1 for item in scored_articles if item.get("ai_used")),
            },
        }



    def _stretch_scores(self, scored_articles):
        """将当前批次 score 拉伸到 75-100 区间。"""
        if os.environ.get("ARTICLE_SCORING_STRETCH_SCORES", "").strip().lower() not in {"1", "true", "yes", "on"}:
            # Keep production routing on the absolute weighted score by default.
            # Otherwise freshness penalties can be undone when a batch is
            # linearly stretched back into 75-100.
            return scored_articles
        # This is a calibration step. It spreads scores within a batch so good
        # and mediocre articles are easier to separate. Tradeoff: thresholds are
        # affected by who else happens to be in the same batch.
        valid = [a for a in scored_articles if a.get("overall_score") is not None]
        if len(valid) < 3:
            return scored_articles
        cmin = min(a["overall_score"] for a in valid)
        cmax = max(a["overall_score"] for a in valid)
        if cmax <= cmin:
            # If every article has the same score, stretching would divide by
            # zero and add no useful information.
            return scored_articles
        for a in scored_articles:
            if a.get("overall_score") is not None:
                # Lowest valid article becomes 75, highest becomes 100, others
                # are linearly mapped between them.
                s = 75.0 + (a["overall_score"] - cmin) / (cmax - cmin) * 25.0
                a["overall_score"] = round(max(75.0, min(s, 100.0)), 2)
        return scored_articles

def summarize_crawler_topics(
    articles: Iterable[Dict[str, Any]],
    weight_profile: Optional[Dict[str, Dict[str, Any]]] = None,
    topic_rules: Optional[Dict[str, List[str]]] = None,
    manual_article_scores: Optional[Dict[Any, Dict[str, Any]]] = None,
    output_count: int = 20,
    use_ai: bool = True,
    ai_client: Optional[Any] = None,
    ai_config: Optional[Dict[str, Any]] = None,
    ai_concurrency: Optional[int] = None,
    db_config: Optional[Dict[str, Any]] = None,
    fetch_from_url: bool = False,
) -> Dict[str, Any]:
    """便捷函数：从 crawler 文章列表生成文章评分。

    LangGraph batch runner 调用的就是这个函数。它是 scoring agent 对外
    暴露的最小入口：传入文章列表，拿回 article_scores。

    新增参数:
        db_config: 数据库配置，用于标记抓取失败的文章
        fetch_from_url: 是否从 original_url 抓取原文（默认 False，向后兼容）
    """

    article_list = list(articles)

    # 从 original_url 抓取原文
    if fetch_from_url:
        article_list, fetch_stats = _fetch_article_contents(article_list, db_config)
    else:
        fetch_stats = {"total": 0, "fetched": 0, "failed": 0, "deleted": 0}

    summarizer = TopicSummarizer(
        weight_profile=weight_profile,
        topic_rules=topic_rules,
        use_ai=use_ai,
        ai_client=ai_client,
        ai_config=ai_config,
        ai_concurrency=ai_concurrency,
    )
    result = summarizer.summarize(
        articles=article_list,
        manual_article_scores=manual_article_scores,
        output_count=output_count,
    )
    result["fetch_stats"] = fetch_stats
    return result


def _fetch_article_contents(
    articles: List[Dict[str, Any]],
    db_config: Optional[Dict[str, Any]] = None,
) -> tuple:
    """从 original_url 抓取原文内容（同步版本）。"""
    from agents.crawler_processor_agent.tools.url_content_fetcher import URLContentFetcher
    from agents.crawler_processor_agent.tools.article_status_updater import ArticleStatusUpdater

    fetcher = URLContentFetcher()
    updater = ArticleStatusUpdater(db_config) if db_config else None

    stats = {"total": len(articles), "fetched": 0, "failed": 0, "deleted": 0}
    updated: List[Dict[str, Any]] = []

    for a in articles:
        url = a.get("original_url", "")
        article_id = a.get("id") or a.get("news_id")
        if not url:
            stats["failed"] += 1
            a["source_content"] = a.get("description", "")
            updated.append(a)
            continue

        try:
            # 用同步 urllib 方式抓取
            import urllib.request
            import urllib.error
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MultiAgentBot/1.0)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                import re as _re
                text = _re.sub(r'<script[^>]*>.*?</script>', '', html, flags=_re.DOTALL | _re.IGNORECASE)
                text = _re.sub(r'<style[^>]*>.*?</style>', '', text, flags=_re.DOTALL | _re.IGNORECASE)
                text = _re.sub(r'<[^>]+>', ' ', text)
                import html as _html
                text = _html.unescape(text)
                text = _re.sub(r'\s+', ' ', text).strip()

                if text and len(text) > 100:
                    a["source_content"] = text[:10000]
                    a["_fetched"] = True
                    stats["fetched"] += 1
                    updated.append(a)
                else:
                    raise ValueError("content_too_short")
        except Exception as e:
            stats["failed"] += 1
            if updater and article_id:
                updater.mark_deleted(int(article_id), reason=str(e))
                stats["deleted"] += 1
            a["source_content"] = a.get("description", "")
            a["_fetch_error"] = str(e)
            updated.append(a)

    return updated, stats
