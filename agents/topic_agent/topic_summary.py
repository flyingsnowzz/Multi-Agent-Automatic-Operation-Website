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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_SCORE_WEIGHT_PROFILE = {
    "title_style_score": {
        "weight": 0.25,
        "description": "标题风格分，越新颖、越不同质化分越高",
    },
    "length_score": {
        "weight": 0.20,
        "description": "文章长度分，过短或过长都会降分",
    },
    "content_importance_score": {
        "weight": 0.35,
        "description": "内容重要性分，短但关键信息明确的文章可以拿高分",
    },
    "freshness_score": {
        "weight": 0.20,
        "description": "时效性分，发布时间越近分数越高",
    },
}


ARTICLE_SCORE_WEIGHTS = {
    "title_style_score": 0.25,
    "length_score": 0.20,
    "content_importance_score": 0.35,
    "freshness_score": 0.20,
}


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
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArticleScore:
    """单篇文章评分明细。"""

    article_id: Any
    title: str
    overall_score: float
    title_style_score: float
    length_score: float
    content_importance_score: float
    freshness_score: float
    score_breakdown: Dict[str, float]
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
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 0.0
        if score <= 1:
            score *= 100
        return max(0.0, min(score, 100.0))


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
        content = str(article.get("content") or article.get("description") or "")[:1800]
        return json.dumps(
            {
                "task": "请给这篇 crawler 文章做语义评分，用于判断是否值得转发、重写或丢弃。",
                "scoring_scale": "所有分数为 0-100。",
                "candidate_topics": candidate_topics,
                "article": {
                    "title": article.get("title") or "",
                    "keywords": article.get("keywords") or "",
                    "description": article.get("description") or "",
                    "content_excerpt": content,
                    "category": article.get("category"),
                    "publish_date": article.get("publish_date") or article.get("published_at"),
                },
                "return_json_schema": {
                    "title_style_score": "标题是否清晰、具体、有信息量，0-100",
                    "content_importance_score": "内容对招生/考试/调剂/录取/政策变化是否重要，0-100",
                    "reason": "一句话说明",
                },
            },
            ensure_ascii=False,
        )

    def _parse_review(self, data: Dict[str, Any]) -> AIArticleReview:
        return AIArticleReview(
            title_style_score=self._optional_score(data.get("title_style_score")),
            content_importance_score=self._optional_score(data.get("content_importance_score")),
            reason=str(data.get("reason") or ""),
            raw=data,
        )

    def _optional_score(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        return WeightSystem._clamp_score(value)


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
        parts = [
            article.get("title"),
            article.get("keywords"),
            article.get("description"),
            article.get("content"),
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

    HIGH_IMPORTANCE_TERMS = [
        "停止招收", "停止招生", "暂停招生", "恢复招生", "取消", "撤销",
        "重大调整", "调整", "变更", "新增", "删除", "不再招收",
        "报名时间", "截止时间", "报名截止", "考试科目", "参考书目",
        "复试名单", "拟录取", "录取名单", "分数线", "调剂", "调剂名额",
        "招生计划", "招生专业目录", "学费", "奖学金", "录取通知书",
        "初试成绩", "成绩查询", "资格审查", "破格复试",
    ]
    LOW_VALUE_TERMS = ["转载", "风采", "活动回顾", "会议", "讲座预告"]

    def __init__(
        self,
        extractor: TopicExtractor,
        ideal_min_words: int = 500,
        ideal_max_words: int = 1800,
        ai_client: Optional[Any] = None,
    ):
        self.extractor = extractor
        self.ideal_min_words = ideal_min_words
        self.ideal_max_words = ideal_max_words
        self.ai_client = ai_client

    def score_articles(
        self,
        articles: List[Dict[str, Any]],
        extracted_by_id: Dict[Any, List[Tuple[str, float, List[str]]]],
        manual_article_scores: Optional[Dict[Any, Dict[str, Any]]] = None,
    ) -> Dict[Any, ArticleScore]:
        manual_article_scores = manual_article_scores or {}
        title_tokens_by_id = {
            self._article_id(article): self._title_tokens(str(article.get("title") or ""))
            for article in articles
        }

        scores = {}
        for article in articles:
            article_id = self._article_id(article)
            extracted_topics = extracted_by_id.get(article_id, [])
            if article_id in manual_article_scores:
                scores[article_id] = self._manual_score(
                    article=article,
                    extracted_topics=extracted_topics,
                    scores=manual_article_scores[article_id],
                )
                continue

            title_style = self._score_title_style(article, title_tokens_by_id)
            word_count = self._count_words(article)
            length = self._score_length(word_count)
            content_importance = self._score_content_importance(article, word_count)
            freshness = self._score_freshness(article)
            ai_review = self._review_with_ai(article, extracted_topics)
            if ai_review:
                title_style = self._merge_ai_score(title_style, ai_review.title_style_score)
                content_importance = self._merge_ai_score(content_importance, ai_review.content_importance_score)

            breakdown = {
                "title_style_score": round(title_style * ARTICLE_SCORE_WEIGHTS["title_style_score"], 4),
                "length_score": round(length * ARTICLE_SCORE_WEIGHTS["length_score"], 4),
                "content_importance_score": round(content_importance * ARTICLE_SCORE_WEIGHTS["content_importance_score"], 4),
                "freshness_score": round(freshness * ARTICLE_SCORE_WEIGHTS["freshness_score"], 4),
            }
            overall = round(min(100.0, sum(breakdown.values())), 2)

            scores[article_id] = ArticleScore(
                article_id=article_id,
                title=str(article.get("title") or ""),
                overall_score=overall,
                title_style_score=round(title_style, 2),
                length_score=round(length, 2),
                content_importance_score=round(content_importance, 2),
                freshness_score=round(freshness, 2),
                score_breakdown=breakdown,
                ai_used=bool(ai_review),
                ai_reason=ai_review.reason if ai_review else None,
                topic_count=len(extracted_topics),
                word_count=word_count,
                topics=[topic for topic, _, _ in extracted_topics],
                reasons=self._score_reasons(
                    title_style,
                    length,
                    content_importance,
                    freshness,
                ),
            )
        return scores

    def _manual_score(
        self,
        article: Dict[str, Any],
        extracted_topics: List[Tuple[str, float, List[str]]],
        scores: Dict[str, Any],
    ) -> ArticleScore:
        title_style = WeightSystem._clamp_score(scores.get("title_style_score", 0))
        length = WeightSystem._clamp_score(scores.get("length_score", 0))
        content_importance = WeightSystem._clamp_score(scores.get("content_importance_score", 0))
        freshness = WeightSystem._clamp_score(scores.get("freshness_score", self._score_freshness(article)))
        overall = scores.get("overall_score")
        if overall is None:
            breakdown = {
                "title_style_score": round(title_style * ARTICLE_SCORE_WEIGHTS["title_style_score"], 4),
                "length_score": round(length * ARTICLE_SCORE_WEIGHTS["length_score"], 4),
                "content_importance_score": round(content_importance * ARTICLE_SCORE_WEIGHTS["content_importance_score"], 4),
                "freshness_score": round(freshness * ARTICLE_SCORE_WEIGHTS["freshness_score"], 4),
            }
            overall_score = round(min(100.0, sum(breakdown.values())), 2)
        else:
            overall_score = WeightSystem._clamp_score(overall)
            breakdown = {
                "title_style_score": round(title_style * ARTICLE_SCORE_WEIGHTS["title_style_score"], 4),
                "length_score": round(length * ARTICLE_SCORE_WEIGHTS["length_score"], 4),
                "content_importance_score": round(content_importance * ARTICLE_SCORE_WEIGHTS["content_importance_score"], 4),
                "freshness_score": round(freshness * ARTICLE_SCORE_WEIGHTS["freshness_score"], 4),
            }
        return ArticleScore(
            article_id=self._article_id(article),
            title=str(article.get("title") or ""),
            overall_score=round(overall_score, 2),
            title_style_score=round(title_style, 2),
            length_score=round(length, 2),
            content_importance_score=round(content_importance, 2),
            freshness_score=round(freshness, 2),
            score_breakdown=breakdown,
            ai_used=False,
            ai_reason=None,
            topic_count=len(extracted_topics),
            word_count=self._count_words(article),
            topics=[topic for topic, _, _ in extracted_topics],
            reasons=["使用手动文章评分"],
        )

    def _score_title_style(
        self,
        article: Dict[str, Any],
        title_tokens_by_id: Dict[Any, set],
    ) -> float:
        article_id = self._article_id(article)
        title = str(article.get("title") or "")
        tokens = title_tokens_by_id.get(article_id, set())
        if not title.strip():
            return 20.0

        similarities = []
        for other_id, other_tokens in title_tokens_by_id.items():
            if other_id == article_id or not tokens or not other_tokens:
                continue
            union = tokens | other_tokens
            if union:
                similarities.append(len(tokens & other_tokens) / len(union))
        max_similarity = max(similarities) if similarities else 0.0
        novelty_score = 100.0 - max_similarity * 70.0

        generic_penalty = 0.0
        generic_terms = ["通知", "公告", "公示", "新闻", "招生简章", "报名通知"]
        if any(term in title for term in generic_terms):
            generic_penalty += 10.0
        if any(term in title for term in self.LOW_VALUE_TERMS):
            generic_penalty += 25.0
        if len(tokens) <= 2:
            generic_penalty += 8.0
        if re.search(r"\d{4}", title):
            novelty_score += 5.0

        return max(0.0, min(100.0, novelty_score - generic_penalty))

    def _score_length(self, word_count: int) -> float:
        if word_count <= 0:
            return 20.0
        if self.ideal_min_words <= word_count <= self.ideal_max_words:
            return 100.0
        if word_count < self.ideal_min_words:
            return max(20.0, word_count / self.ideal_min_words * 100)

        over_ratio = (word_count - self.ideal_max_words) / self.ideal_max_words
        return max(35.0, 100.0 - over_ratio * 80.0)

    def _score_content_importance(self, article: Dict[str, Any], word_count: int) -> float:
        text = self._article_text(article)
        matched_high = [term for term in self.HIGH_IMPORTANCE_TERMS if term in text]
        matched_low = [term for term in self.LOW_VALUE_TERMS if term in text]

        score = 45.0
        if matched_high:
            score += min(45.0, len(set(matched_high)) * 12.0)
        if re.search(r"202[0-9]年|报名|复试|录取|调剂|招生|考试", text):
            score += 8.0
        if word_count <= 250 and matched_high:
            score += 12.0
        if "停止" in text or "不再招收" in text or "暂停" in text:
            score += 20.0
        if matched_low and not matched_high:
            score -= 15.0

        return max(0.0, min(100.0, score))

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

    def _merge_ai_score(self, rule_score: float, ai_score: Optional[float]) -> float:
        if ai_score is None:
            return rule_score
        if ai_score >= 100:
            return 100.0
        return round(rule_score * 0.35 + ai_score * 0.65, 2)

    def _title_tokens(self, title: str) -> set:
        english = re.findall(r"[A-Za-z0-9]+", title.lower())
        chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", title)
        chinese_tokens = []
        for chunk in chinese_chunks:
            chinese_tokens.extend(chunk[i:i + 2] for i in range(max(1, len(chunk) - 1)))
        return set(english + chinese_tokens)

    def _count_words(self, article: Dict[str, Any]) -> int:
        content = str(article.get("content") or article.get("description") or "")
        if not content:
            content = str(article.get("title") or "")
        chinese = len(re.findall(r"[\u4e00-\u9fff]", content))
        english = len(re.findall(r"\b[A-Za-z]+\b", content))
        return chinese + english

    def _score_freshness(self, article: Dict[str, Any]) -> float:
        raw_date = article.get("publish_date") or article.get("published_at") or article.get("ctime")
        parsed = self._parse_date(raw_date)
        if not parsed:
            return 50.0
        days = max(0, (datetime.now() - parsed).days)
        if days <= 7:
            return 100.0
        if days <= 30:
            return 90.0
        if days <= 90:
            return 80.0
        if days <= 180:
            return 65.0
        if days <= 365:
            return 50.0
        if days <= 730:
            return 35.0
        return 20.0

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
        title_style: float,
        length: float,
        content_importance: float,
        freshness: float,
    ) -> List[str]:
        reasons = []
        reasons.append("标题较新颖" if title_style >= 75 else "标题同质化偏高")
        reasons.append("文章长度合适" if length >= 80 else "文章长度偏离理想区间")
        reasons.append("内容信息重要" if content_importance >= 80 else "内容重要性一般")
        reasons.append("发布时间较近" if freshness >= 80 else "发布时间较早或缺失")
        return reasons

    def _article_text(self, article: Dict[str, Any]) -> str:
        parts = [
            article.get("title"),
            article.get("keywords"),
            article.get("description"),
            article.get("content"),
            article.get("college_name"),
            article.get("specialty_name"),
        ]
        return " ".join(str(part) for part in parts if part)

    def _article_id(self, article: Dict[str, Any]) -> Any:
        return article.get("id") or article.get("news_id") or article.get("original_url")


class TopicSummarizer:
    """遍历文章、抽取辅助主题，并输出文章评分。"""

    def __init__(
        self,
        weight_profile: Optional[Dict[str, Dict[str, Any]]] = None,
        topic_rules: Optional[Dict[str, List[str]]] = None,
        use_ai: bool = False,
        ai_client: Optional[Any] = None,
        ai_config: Optional[Dict[str, Any]] = None,
    ):
        self.extractor = TopicExtractor(topic_rules)
        if ai_client is None and use_ai:
            ai_config = ai_config or {}
            ai_client = AIArticleScoringClient(
                api_key=ai_config.get("api_key"),
                model=ai_config.get("model"),
                base_url=ai_config.get("base_url"),
                timeout=int(ai_config.get("timeout", 30)),
            )
        self.article_scorer = ArticleScorer(self.extractor, ai_client=ai_client)

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
            article_id = article.get("id") or article.get("news_id") or article.get("original_url")
            extracted_by_id[article_id] = self.extractor.extract(article)

        article_scores = self.article_scorer.score_articles(
            articles=article_list,
            extracted_by_id=extracted_by_id,
            manual_article_scores=manual_article_scores,
        )

        scored_articles = [
            asdict(article_scores[article.get("id") or article.get("news_id") or article.get("original_url")])
            for article in article_list
        ]

        return {
            "article_scores": scored_articles,
            "summary": {
                "article_count": len(article_list),
                "scored_count": len(scored_articles),
                "ai_used_count": sum(1 for item in scored_articles if item.get("ai_used")),
            },
        }


def summarize_crawler_topics(
    articles: Iterable[Dict[str, Any]],
    weight_profile: Optional[Dict[str, Dict[str, Any]]] = None,
    topic_rules: Optional[Dict[str, List[str]]] = None,
    manual_article_scores: Optional[Dict[Any, Dict[str, Any]]] = None,
    output_count: int = 20,
    use_ai: bool = False,
    ai_client: Optional[Any] = None,
    ai_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """便捷函数：从 crawler 文章列表生成文章评分。"""

    article_list = list(articles)
    summarizer = TopicSummarizer(
        weight_profile=weight_profile,
        topic_rules=topic_rules,
        use_ai=use_ai,
        ai_client=ai_client,
        ai_config=ai_config,
    )
    return summarizer.summarize(
        articles=article_list,
        manual_article_scores=manual_article_scores,
        output_count=output_count,
    )
