"""
选题Agent核心实现。

职责：
- 基于种子关键词生成候选选题
- 按可配置评分标准逐个 topic 打分
- 输出排序后的选题列表，供调研/写作 Agent 继续处理
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

from agents.topic_agent.tools.keyword_research import KeywordData, KeywordResearchTool
from agents.topic_agent.topic_summary import summarize_crawler_topics


DEFAULT_SCORING_CRITERIA = {
    "search_value": {
        "weight": 0.30,
        "description": "搜索量和长期搜索需求",
    },
    "competition_feasibility": {
        "weight": 0.25,
        "description": "关键词难度和竞争可应对性",
    },
    "intent_match": {
        "weight": 0.20,
        "description": "选题与用户搜索意图的匹配程度",
    },
    "content_uniqueness": {
        "weight": 0.15,
        "description": "差异化视角和内容缺口机会",
    },
    "strategic_value": {
        "weight": 0.10,
        "description": "与品牌、转化或业务目标的契合度",
    },
}


@dataclass
class TopicScore:
    """单个选题的评分结果。"""

    total_score: float
    dimension_scores: Dict[str, float]
    priority: str
    recommendation: str
    reasons: List[str] = field(default_factory=list)


@dataclass
class TopicCandidate:
    """候选选题结构。"""

    id: str
    title: str
    target_keywords: List[str]
    search_volume: int
    keyword_difficulty: float
    competition_level: str
    content_type: str
    search_intent: str
    outline_points: List[str]
    score: TopicScore
    estimated_difficulty: str
    data_sources: List[str]


class TopicAgent:
    """选题Agent：生成、评分、排序候选选题。"""

    def __init__(
        self,
        config_path: str = "agents/topic_agent/config.yaml",
        keyword_tool: Optional[KeywordResearchTool] = None,
    ):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.keyword_tool = keyword_tool or KeywordResearchTool(
            self.config.get("keyword_research", {})
        )
        self.scoring_criteria = self._load_scoring_criteria()

    async def execute(
        self,
        keywords: List[str],
        industry: str = "",
        target_audience: str = "",
        content_goals: Optional[List[str]] = None,
        scoring_criteria: Optional[Dict[str, Dict[str, Any]]] = None,
        output_count: Optional[int] = None,
        min_search_volume: Optional[int] = None,
        max_kd: Optional[float] = None,
    ) -> Dict[str, Any]:
        """生成选题列表并给每个 topic 打分。"""

        topic_config = self.config.get("topic", {})
        volume_config = topic_config.get("search_volume", {})
        kd_config = topic_config.get("keyword_difficulty", {})
        output_config = topic_config.get("output", {})

        min_search_volume = min_search_volume or volume_config.get("min", 100)
        max_kd = max_kd if max_kd is not None else kd_config.get("max", 35)
        output_count = output_count or output_config.get("max", 10)
        criteria = self._normalize_scoring_criteria(scoring_criteria or self.scoring_criteria)

        keyword_result = await self.keyword_tool.research_keywords(
            seed_keywords=keywords,
            min_search_volume=min_search_volume,
            max_kd=max_kd,
            limit=max(output_count * 3, 15),
        )

        keyword_pool = (
            keyword_result.primary_keywords
            + keyword_result.long_tail_keywords
            + keyword_result.questions
        )
        if not keyword_pool:
            keyword_pool = self._fallback_keywords(keywords, min_search_volume, max_kd)
        elif len(keyword_pool) < output_count:
            existing = {item.keyword for item in keyword_pool}
            supplements = [
                item
                for item in self._fallback_keywords(keywords, min_search_volume, max_kd)
                if item.keyword not in existing
            ]
            keyword_pool.extend(supplements)

        candidates = [
            self._build_candidate(
                keyword_data=kw,
                industry=industry,
                target_audience=target_audience,
                content_goals=content_goals or [],
                scoring_criteria=criteria,
            )
            for kw in keyword_pool
        ]

        candidates = self._dedupe_topics(candidates)
        candidates.sort(key=lambda item: item.score.total_score, reverse=True)

        selected = candidates[:output_count]
        return {
            "agent": self.config.get("agent", {}).get("name", "TopicAgent"),
            "industry": industry,
            "target_audience": target_audience,
            "seed_keywords": keywords,
            "scoring_criteria": criteria,
            "topics": [asdict(topic) for topic in selected],
            "summary": {
                "total_candidates": len(candidates),
                "selected_count": len(selected),
                "high_priority_count": sum(
                    1 for topic in selected if topic.score.priority == "high"
                ),
            },
        }

    def score_topic(
        self,
        topic: Dict[str, Any],
        scoring_criteria: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> TopicScore:
        """对外暴露的单 topic 评分方法。"""

        criteria = self._normalize_scoring_criteria(scoring_criteria or self.scoring_criteria)
        keyword_data = KeywordData(
            keyword=topic.get("primary_keyword") or topic.get("keyword") or topic.get("title", ""),
            search_volume=int(topic.get("search_volume", 0)),
            keyword_difficulty=float(topic.get("keyword_difficulty", 50)),
            competition=topic.get("competition_level"),
            source=topic.get("source", "manual"),
        )
        return self._score_keyword(keyword_data, criteria)

    def summarize_from_articles(
        self,
        articles: List[Dict[str, Any]],
        weight_profile: Optional[Dict[str, Dict[str, Any]]] = None,
        topic_rules: Optional[Dict[str, List[str]]] = None,
        manual_article_scores: Optional[Dict[Any, Dict[str, Any]]] = None,
        output_count: int = 20,
        use_ai: bool = False,
        ai_client: Optional[Any] = None,
        ai_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """从 crawler 文章列表中归纳主题，并按权重生成主题分。"""

        configured_profile = (
            weight_profile
            or self.config.get("crawler_topic_summary", {}).get("weight_profile")
        )
        configured_rules = (
            topic_rules
            or self.config.get("crawler_topic_summary", {}).get("topic_rules")
        )
        return summarize_crawler_topics(
            articles=articles,
            weight_profile=configured_profile,
            topic_rules=configured_rules,
            manual_article_scores=manual_article_scores,
            output_count=output_count,
            use_ai=use_ai,
            ai_client=ai_client,
            ai_config=ai_config or self.config.get("crawler_topic_summary", {}).get("ai_scoring"),
        )

    def _build_candidate(
        self,
        keyword_data: KeywordData,
        industry: str,
        target_audience: str,
        content_goals: List[str],
        scoring_criteria: Dict[str, Dict[str, Any]],
    ) -> TopicCandidate:
        score = self._score_keyword(keyword_data, scoring_criteria)
        content_type = self._infer_content_type(keyword_data.keyword)
        title = self._make_title(keyword_data.keyword, content_type, industry)

        return TopicCandidate(
            id=self._make_topic_id(title),
            title=title,
            target_keywords=self._target_keywords(keyword_data),
            search_volume=keyword_data.search_volume,
            keyword_difficulty=keyword_data.keyword_difficulty,
            competition_level=self._competition_level(keyword_data.keyword_difficulty),
            content_type=content_type,
            search_intent=self._infer_search_intent(keyword_data.keyword),
            outline_points=self._make_outline(keyword_data.keyword, content_type, target_audience),
            score=score,
            estimated_difficulty=self._estimated_difficulty(keyword_data.keyword_difficulty),
            data_sources=[keyword_data.source],
        )

    def _score_keyword(
        self,
        keyword_data: KeywordData,
        scoring_criteria: Dict[str, Dict[str, Any]],
    ) -> TopicScore:
        volume = keyword_data.search_volume
        kd = keyword_data.keyword_difficulty

        dimension_scores = {
            "search_value": self._score_search_value(volume),
            "competition_feasibility": max(0.0, 100.0 - kd),
            "intent_match": self._score_intent_match(keyword_data.keyword),
            "content_uniqueness": self._score_uniqueness(keyword_data.keyword, kd),
            "strategic_value": self._score_strategic_value(keyword_data.keyword),
        }

        total = 0.0
        for name, score in dimension_scores.items():
            total += score * scoring_criteria.get(name, {}).get("weight", 0)
        total = round(total, 2)

        priority = self._priority(total)
        return TopicScore(
            total_score=total,
            dimension_scores={k: round(v, 2) for k, v in dimension_scores.items()},
            priority=priority,
            recommendation=self._recommendation(total),
            reasons=self._score_reasons(keyword_data, dimension_scores),
        )

    def _load_config(self, path: str) -> Dict[str, Any]:
        default_config = {
            "agent": {"name": "TopicAgent"},
            "topic": {
                "search_volume": {"min": 100, "preferred": 500},
                "keyword_difficulty": {"max": 35},
                "output": {"max": 10},
            },
            "keyword_research": {
                "filters": {
                    "prefer": ["指南", "攻略", "教程", "技巧", "方法", "流程"],
                }
            },
            "scoring": {"criteria": DEFAULT_SCORING_CRITERIA},
        }
        if not os.path.exists(path):
            return default_config
        if yaml is None:
            return default_config
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or default_config

    def _load_scoring_criteria(self) -> Dict[str, Dict[str, Any]]:
        configured = self.config.get("scoring", {}).get("criteria")
        return self._normalize_scoring_criteria(configured or DEFAULT_SCORING_CRITERIA)

    def _normalize_scoring_criteria(
        self,
        criteria: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        merged = {k: v.copy() for k, v in DEFAULT_SCORING_CRITERIA.items()}
        for name, value in (criteria or {}).items():
            merged[name] = {**merged.get(name, {}), **(value or {})}

        total_weight = sum(float(item.get("weight", 0)) for item in merged.values())
        if total_weight <= 0:
            return DEFAULT_SCORING_CRITERIA

        return {
            name: {
                **item,
                "weight": round(float(item.get("weight", 0)) / total_weight, 4),
            }
            for name, item in merged.items()
        }

    def _fallback_keywords(
        self,
        keywords: List[str],
        min_search_volume: int,
        max_kd: float,
    ) -> List[KeywordData]:
        templates = ["指南", "怎么选", "流程", "对比", "案例"]
        fallback = []
        for seed in keywords:
            fallback.append(
                KeywordData(
                    keyword=seed,
                    search_volume=max(min_search_volume, 500),
                    keyword_difficulty=min(max_kd, 30),
                    source="fallback",
                )
            )
            for suffix in templates:
                fallback.append(
                    KeywordData(
                        keyword=f"{seed}{suffix}",
                        search_volume=max(min_search_volume, 260),
                        keyword_difficulty=min(max_kd, 28),
                        source="fallback",
                    )
                )
        return fallback

    def _dedupe_topics(self, topics: List[TopicCandidate]) -> List[TopicCandidate]:
        seen = set()
        unique = []
        for topic in topics:
            key = topic.title.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(topic)
        return unique

    def _score_search_value(self, volume: int) -> float:
        preferred = self.config.get("topic", {}).get("search_volume", {}).get("preferred", 500)
        if preferred <= 0:
            return 50.0
        return min(100.0, max(0.0, volume / preferred * 80))

    def _score_intent_match(self, keyword: str) -> float:
        high_intent_terms = ["如何", "怎么", "指南", "攻略", "教程", "方法", "流程", "选择", "对比"]
        if any(term in keyword for term in high_intent_terms):
            return 90.0
        return 72.0

    def _score_uniqueness(self, keyword: str, kd: float) -> float:
        modifier_terms = ["案例", "清单", "避坑", "趋势", "实战", "对比", "2026"]
        base = 78.0 if any(term in keyword for term in modifier_terms) else 62.0
        return max(30.0, base - max(0.0, kd - 35) * 0.5)

    def _score_strategic_value(self, keyword: str) -> float:
        prefer_terms = self.config.get("keyword_research", {}).get("filters", {}).get("prefer", [])
        if any(term in keyword for term in prefer_terms):
            return 86.0
        return 70.0

    def _score_reasons(
        self,
        keyword_data: KeywordData,
        dimension_scores: Dict[str, float],
    ) -> List[str]:
        reasons = []
        if dimension_scores["search_value"] >= 75:
            reasons.append("搜索需求较明确")
        if dimension_scores["competition_feasibility"] >= 65:
            reasons.append("关键词难度在可竞争范围内")
        if dimension_scores["intent_match"] >= 85:
            reasons.append("搜索意图清晰，适合做指南或解决方案内容")
        if dimension_scores["content_uniqueness"] >= 70:
            reasons.append("具备差异化切入空间")
        if not reasons:
            reasons.append("可作为备选选题继续观察")
        return reasons

    def _make_title(self, keyword: str, content_type: str, industry: str) -> str:
        if content_type == "comparison":
            if any(term in keyword for term in ["对比", "比较", "vs", "VS"]):
                return f"{keyword}：关键差异、适用场景和选择建议"
            return f"{keyword}对比：关键差异、适用场景和选择建议"
        if content_type == "case_study":
            if "案例" in keyword:
                return f"{keyword}拆解：可复用的方法和经验"
            return f"{keyword}案例拆解：可复用的方法和经验"
        if content_type == "how_to":
            if any(term in keyword for term in ["如何", "怎么"]):
                return f"{keyword}：从准备到落地的完整流程"
            return f"{keyword}怎么做：从准备到落地的完整流程"
        if any(term in keyword for term in ["指南", "攻略", "教程"]):
            if industry:
                return f"{keyword}：{industry}场景下的关键判断"
            return f"{keyword}：关键步骤、常见问题与实用建议"
        if industry:
            return f"{keyword}指南：{industry}场景下的关键判断"
        return f"{keyword}指南：关键步骤、常见问题与实用建议"

    def _target_keywords(self, keyword_data: KeywordData) -> List[str]:
        related = keyword_data.related_keywords or []
        return [keyword_data.keyword, *related[:2]]

    def _make_outline(self, keyword: str, content_type: str, target_audience: str) -> List[str]:
        audience = target_audience or "目标读者"
        common = [
            f"{audience}为什么关注{keyword}",
            "核心概念和判断标准",
            "常见误区与风险提醒",
            "可执行的下一步建议",
        ]
        if content_type == "comparison":
            common.insert(2, "不同方案的优劣对比")
        elif content_type == "case_study":
            common.insert(2, "典型案例拆解")
        else:
            common.insert(2, "具体操作流程")
        return common[:5]

    def _infer_content_type(self, keyword: str) -> str:
        if any(term in keyword for term in ["对比", "比较", "vs", "VS"]):
            return "comparison"
        if "案例" in keyword:
            return "case_study"
        if any(term in keyword for term in ["如何", "怎么", "方法", "流程"]):
            return "how_to"
        return "guide"

    def _infer_search_intent(self, keyword: str) -> str:
        if any(term in keyword for term in ["报名", "购买", "申请"]):
            return "transactional"
        if any(term in keyword for term in ["官网", "地址", "电话"]):
            return "navigational"
        return "informational"

    def _competition_level(self, keyword_difficulty: float) -> str:
        if keyword_difficulty < 25:
            return "low"
        if keyword_difficulty < 55:
            return "medium"
        return "high"

    def _estimated_difficulty(self, keyword_difficulty: float) -> str:
        if keyword_difficulty < 30:
            return "easy"
        if keyword_difficulty < 55:
            return "medium"
        return "hard"

    def _priority(self, score: float) -> str:
        if score >= 80:
            return "high"
        if score >= 65:
            return "medium"
        return "low"

    def _recommendation(self, score: float) -> str:
        if score >= 80:
            return "采纳"
        if score >= 65:
            return "修改后采纳"
        return "暂缓"

    def _make_topic_id(self, title: str) -> str:
        digest = hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
        return f"topic_{digest}"


async def generate_topic_list(
    keywords: List[str],
    industry: str = "",
    target_audience: str = "",
    output_count: int = 10,
    scoring_criteria: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """便捷函数：生成选题列表。"""

    agent = TopicAgent()
    return await agent.execute(
        keywords=keywords,
        industry=industry,
        target_audience=target_audience,
        output_count=output_count,
        scoring_criteria=scoring_criteria,
    )


def get_topic_generator_tool():
    """返回CrewAI可用的选题生成工具。"""

    from crewai.tools import tool

    @tool("topic_generator")
    def topic_generator_tool(
        keywords: str,
        industry: str = "",
        target_audience: str = "",
        output_count: int = 10,
        scoring_criteria_json: str = "",
    ) -> str:
        """
        基于种子关键词生成候选选题列表，并按评分标准排序。

        Args:
            keywords: 逗号分隔的种子关键词
            industry: 行业/领域
            target_audience: 目标受众
            output_count: 输出选题数量
            scoring_criteria_json: 可选评分标准JSON，格式为 {维度: {weight, description}}
        """

        parsed_criteria = None
        if scoring_criteria_json:
            parsed_criteria = json.loads(scoring_criteria_json)

        result = asyncio.run(
            generate_topic_list(
                keywords=[item.strip() for item in keywords.split(",") if item.strip()],
                industry=industry,
                target_audience=target_audience,
                output_count=output_count,
                scoring_criteria=parsed_criteria,
            )
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    return topic_generator_tool
