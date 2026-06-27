"""Crawler 文章评分。

这个模块刻意不绑定具体数据库：
- crawler_news_main / crawler_news_0..9 可以先读成 dict 再传入
- 输出的是文章评分列表，不做 topic 排名
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
    "title_style_score": 0.25,
    "notice_score": 0.05,
    "content_importance_score": 0.70,
    "freshness_score": 0.10,
}


def _article_id(article: Dict[str, Any]) -> Any:
    return article.get("id") or article.get("news_id") or article.get("original_url")


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    if score <= 1:
        score *= 100
    return max(0.0, min(score, 100.0))


DEFAULT_TOPIC_RULES = {
    "招生简章": ["招生简章", "招生章程", "招生专业目录", "招生目录"],
    "报考条件": ["报考条件", "报名条件", "申请条件", "报考资格"],
    "报名流程": ["报名", "网上报名", "报名流程", "报名时间", "入口"],
    "复试录取": ["复试", "录取", "拟录取", "复试名单", "录取名单"],
    "调剂信息": ["调剂", "调剂公告", "调剂系统", "调剂名额"],
    "考试大纲": ["考试大纲", "考试科目", "参考书目", "初试", "笔试"],
    "学费学制": ["学费", "学制", "奖学金", "培养费用"],
    "项目介绍": ["项目介绍", "专业介绍", "培养方案", "课程设置"],
    "院校动态": ["通知", "公告", "新闻", "动态", "讲座"],
    "中外合作": ["中外合作", "国际项目", "合作办学", "留学"],
    "MBA": ["MBA", "工商管理硕士"],
    "EMBA": ["EMBA", "高级管理人员工商管理硕士"],
    "MEM": ["MEM", "工程管理硕士"],
    "MPA": ["MPA", "公共管理硕士"],
    "MPAcc": ["MPAcc", "会计硕士"],
}


CATEGORY_TOPIC_MAP = {
    1: "招生简章",
    "1": "招生简章",
    2: "院校动态",
    "2": "院校动态",
    3: "院校公告",
    "3": "院校公告",
    5: "调剂信息",
    "5": "调剂信息",
}


@dataclass
class WeightedScore:
    """权重系统输出。"""

    total_score: float
    dimension_scores: Dict[str, float]
    weighted_breakdown: Dict[str, float]
    weights: Dict[str, float]


@dataclass
class AIArticleReview:
    """AI 对文章语义价值的评分结果。"""

    title_style_score: Optional[float] = None
    content_importance_score: Optional[float] = None
    is_notice: Optional[bool] = None
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArticleScore:
    """单篇文章评分明细。"""

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
        if not self.enabled:
            return None

        payload = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是招生内容运营的文章评分助手。"
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
            return None

        content = (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "{}")
        )
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return None
        return self._parse_review(data)

    def _build_prompt(self, article: Dict[str, Any], candidate_topics: List[str]) -> str:
        content = str(article.get("source_content") or article.get("content") or article.get("description") or "")[:6000]
        return json.dumps(
            {
                "task": "请给这篇 crawler 文章做语义评分，用于判断是否值得转发、重写或丢弃。重要程度必须结合可见全文内容判断，而不是只看标题。",
                "scoring_scale": "所有分数为 0-100。",
                "content_importance_guidelines": [
                    "招生政策、考试安排、调剂录取、项目变化、重要院校动态等实用信息，可以给高重要性分。",
                    "故事类内容也可以给高重要性分：例如教授/学生/校友的心路历程、科研突破背后的故事、成长经历、团队奋斗过程、项目发展故事。只要具备人物性、叙事性、传播性或情绪价值，用户可能愿意阅读，就不应因为不是通知或政策而低估。",
                    "泛泛会议、普通活动回顾、缺少明确看点的校内动态，应给较低重要性分。",
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
                    "content_importance_score": "阅读全文后判断内容是否值得用户阅读和内容运营使用，0-100。政策/招生/考试信息可高分；人物故事、教授心路历程、科研故事、校友成长等有传播性的故事类内容也可高分。",
                    "is_notice": (
                        "boolean。通知/公告/公示/须知/提示/名单/办法/细则等流程性内容为 true；"
                        "新闻报道、政策解读、招生动态、趋势分析等更适合内容运营的文章为 false"
                    ),
                    "reason": "一句话说明",
                },
            },
            ensure_ascii=False,
        )

    def _parse_review(self, data: Dict[str, Any]) -> AIArticleReview:
        return AIArticleReview(
            title_style_score=self._optional_score(data.get("title_style_score")),
            content_importance_score=self._optional_score(data.get("content_importance_score")),
            is_notice=self._optional_bool(data.get("is_notice")),
            reason=str(data.get("reason") or ""),
            raw=data,
        )

    def _optional_score(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        return _clamp_score(value)

    def _optional_bool(self, value: Any) -> Optional[bool]:
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
    """基于规则的主题抽取器，后续可替换为 LLM/Embedding 聚类。"""

    def __init__(self, topic_rules: Optional[Dict[str, List[str]]] = None):
        self.topic_rules = topic_rules or DEFAULT_TOPIC_RULES

    def extract(self, article: Dict[str, Any], max_topics: int = 5) -> List[Tuple[str, float, List[str]]]:
        text = self._article_text(article)
        candidates: List[Tuple[str, float, List[str]]] = []

        category_topic = CATEGORY_TOPIC_MAP.get(article.get("category"))
        if category_topic:
            candidates.append((category_topic, 72.0, ["category"]))

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

    def _article_text(self, article: Dict[str, Any]) -> str:
        # 优先使用从 original_url 抓取的 source_content
        source = article.get("source_content") or article.get("description") or article.get("content") or ""
        parts = [
            article.get("title"),
            article.get("keywords"),
            source,
            article.get("college_name"),
            article.get("specialty_name"),
        ]
        return " ".join(str(part) for part in parts if part)

    def _fallback_topic(self, article: Dict[str, Any]) -> str:
        specialty = str(article.get("specialty_name") or "").strip()
        if specialty:
            return specialty

        title = str(article.get("title") or "").strip()
        if not title:
            return "未分类主题"

        cleaned = re.sub(r"[【】\[\]（）()《》:：,，.!！?？\-_\s]+", " ", title)
        words = [item for item in cleaned.split(" ") if len(item) >= 2]
        return words[0][:16] if words else title[:16]

    def _term_matches(self, text: str, term: str) -> bool:
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
        manual_article_scores = manual_article_scores or {}
        ai_reviews_by_id = self._review_articles_with_ai(
            articles=articles,
            extracted_by_id=extracted_by_id,
            manual_article_scores=manual_article_scores,
        )
        scores = {}
        for article in articles:
            article_id = _article_id(article)
            extracted_topics = extracted_by_id.get(article_id, [])
            if article_id in manual_article_scores:
                scores[article_id] = self._manual_score(
                    article=article,
                    extracted_topics=extracted_topics,
                    scores=manual_article_scores[article_id],
                )
                continue

            word_count = self._count_words(article)
            freshness, freshness_factor, freshness_weight_active = self._freshness_policy(article)
            ai_review = ai_reviews_by_id.get(article_id)
            title_style = ai_review.title_style_score if ai_review else None
            raw_content_importance = ai_review.content_importance_score if ai_review else None
            content_importance = (
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
                round(min(100.0, sum(value for value in breakdown.values() if value is not None)), 2)
                if title_style is not None
                and content_importance is not None
                and notice_score is not None
                else None
            )

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

        pending = [
            article
            for article in articles
            if _article_id(article) not in manual_article_scores
        ]
        if self.ai_concurrency <= 1:
            return {
                _article_id(article): self._review_with_ai(
                    article,
                    extracted_by_id.get(_article_id(article), []),
                )
                for article in pending
            }

        reviews: Dict[Any, Optional[AIArticleReview]] = {}
        with ThreadPoolExecutor(max_workers=self.ai_concurrency) as executor:
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
        if score is None:
            return None
        return round(score * (weights or ARTICLE_SCORE_WEIGHTS)[name], 4)

    def _score_notice(self, is_notice: Optional[bool]) -> Optional[float]:
        if is_notice is None:
            return None
        return 0.0 if is_notice else 100.0

    def _review_with_ai(
        self,
        article: Dict[str, Any],
        extracted_topics: List[Tuple[str, float, List[str]]],
    ) -> Optional[AIArticleReview]:
        if not self.ai_client:
            return None
        candidate_topics = [topic for topic, _, _ in extracted_topics]
        try:
            return self.ai_client.review_article(article, candidate_topics)
        except Exception:
            return None

    def _count_words(self, article: Dict[str, Any]) -> int:
        content = str(article.get("content") or article.get("description") or "")
        if not content:
            content = str(article.get("title") or "")
        chinese = len(re.findall(r"[\u4e00-\u9fff]", content))
        english = len(re.findall(r"\b[A-Za-z]+\b", content))
        return chinese + english

    def _freshness_policy(self, article: Dict[str, Any]) -> Tuple[float, float, bool]:
        raw_date = article.get("publish_time") or article.get("publish_date") or article.get("published_at") or article.get("ctime")
        parsed = self._parse_date(raw_date)
        if not parsed:
            return 50.0, 0.5, True
        days = max(0, (datetime.now() - parsed).days)
        months = days / 30.4375
        if months <= 2:
            return 100.0, 1.0, False
        if months <= 6:
            freshness = self._interpolate(months, 2, 6, 100, 80)
            return freshness, 0.8, True
        if months <= 12:
            freshness = self._interpolate(months, 6, 12, 80, 60)
            return freshness, 0.5, True
        if months <= 24:
            freshness = self._interpolate(months, 12, 24, 60, 30)
            return freshness, 0.5, True
        if months <= 36:
            freshness = self._interpolate(months, 24, 36, 30, 0)
            return freshness, 0.1, True
        return 0.0, 0.1, True

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
        if end_value <= start_value:
            return end_score
        ratio = (value - start_value) / (end_value - start_value)
        score = start_score + ratio * (end_score - start_score)
        return max(min(start_score, end_score), min(max(start_score, end_score), score))

    def _parse_date(self, value: Any) -> Optional[datetime]:
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
        valid = [a for a in scored_articles if a.get("overall_score") is not None]
        if len(valid) < 3:
            return scored_articles
        cmin = min(a["overall_score"] for a in valid)
        cmax = max(a["overall_score"] for a in valid)
        if cmax <= cmin:
            return scored_articles
        for a in scored_articles:
            if a.get("overall_score") is not None:
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
