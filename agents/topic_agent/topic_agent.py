import os
import sys
import yaml
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))

from agents.topic_agent.tools.keyword_research import KeywordResearchTool, KeywordData
from agents.topic_agent.tools.serp_analysis import SERPAnalysisTool
from agents.topic_agent.tools.trend_detection import TrendDetectionTool


def _deep_env_resolve(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            key = value[2:-1]
            return os.environ.get(key, "")
        return value
    if isinstance(value, dict):
        return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_env_resolve(v) for v in value]
    return value


class TopicAgent:
    def __init__(self, config_path: str = "agents/topic_agent/config.yaml", mode: Optional[str] = None):
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass
        self.config_path = config_path
        self.config = self._load_config()
        exec_cfg = (self.config or {}).get("execution") if isinstance(self.config, dict) else {}
        cfg_mode = (exec_cfg.get("mode") or "").strip().lower() if isinstance(exec_cfg, dict) else ""
        self.mode = (mode or os.environ.get("TOPIC_AGENT_MODE") or cfg_mode or "mock").strip().lower()

    def _load_config(self) -> Dict[str, Any]:
        p = (self.config_path or "").strip()
        if p and os.path.exists(p):
            cfg_path = p
        else:
            cfg_path = str(Path(__file__).resolve().parent / "config.yaml")
            if not os.path.exists(cfg_path):
                return {}

        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _get_limits(self, min_search_volume: Optional[int], max_kd: Optional[float]) -> Dict[str, Any]:
        topic_cfg = (self.config or {}).get("topic") if isinstance(self.config, dict) else {}
        sv_cfg = (topic_cfg.get("search_volume") or {}) if isinstance(topic_cfg, dict) else {}
        kd_cfg = (topic_cfg.get("keyword_difficulty") or {}) if isinstance(topic_cfg, dict) else {}
        min_vol = int(min_search_volume if min_search_volume is not None else (sv_cfg.get("min", 100) or 100))
        max_kd_val = float(max_kd if max_kd is not None else (kd_cfg.get("max", 35) or 35))
        out_cfg = (topic_cfg.get("output") or {}) if isinstance(topic_cfg, dict) else {}
        min_out = int(out_cfg.get("min", 5) or 5)
        max_out = int(out_cfg.get("max", 10) or 10)
        return {"min_volume": min_vol, "max_kd": max_kd_val, "min_out": min_out, "max_out": max_out}

    def _priority_weights(self) -> Dict[str, float]:
        out_cfg = (self.config or {}).get("output") if isinstance(self.config, dict) else {}
        weights = (out_cfg.get("priority_weights") or {}) if isinstance(out_cfg, dict) else {}
        default_weights = {
            "search_volume": 0.3,
            "keyword_difficulty": 0.25,
            "competition_gap": 0.2,
            "trending_score": 0.15,
            "strategic_value": 0.1,
        }
        merged: Dict[str, float] = dict(default_weights)
        for k, v in weights.items() if isinstance(weights, dict) else []:
            try:
                merged[str(k)] = float(v)
            except Exception:
                continue
        return merged

    def _prefer_terms(self) -> List[str]:
        kw_cfg = (self.config or {}).get("keyword_research") if isinstance(self.config, dict) else {}
        filters_cfg = (kw_cfg.get("filters") or {}) if isinstance(kw_cfg, dict) else {}
        prefer = filters_cfg.get("prefer") if isinstance(filters_cfg, dict) else None
        if isinstance(prefer, list):
            return [str(x) for x in prefer if str(x).strip()]
        return []

    def _normalize_search_volume_score(self, search_volume: int) -> float:
        topic_cfg = (self.config or {}).get("topic") if isinstance(self.config, dict) else {}
        sv_cfg = (topic_cfg.get("search_volume") or {}) if isinstance(topic_cfg, dict) else {}
        preferred = float(sv_cfg.get("preferred") or 500)
        denom = max(1.0, preferred * 2.0)
        return max(0.0, min(100.0, float(search_volume) / denom * 100.0))

    def _normalize_kd_score(self, kd: float) -> float:
        topic_cfg = (self.config or {}).get("topic") if isinstance(self.config, dict) else {}
        kd_cfg = (topic_cfg.get("keyword_difficulty") or {}) if isinstance(topic_cfg, dict) else {}
        max_kd = float(kd_cfg.get("max") or 35)
        if max_kd <= 0:
            return 0.0
        return max(0.0, min(100.0, (max_kd - float(kd)) / max_kd * 100.0))

    def _normalize_competition_gap_score(
        self,
        *,
        content_gaps: Optional[List[str]],
        opportunities: Optional[List[str]],
        competition_score: float,
    ) -> float:
        gaps = content_gaps or []
        opps = opportunities or []
        base = len(gaps) * 18.0 + len(opps) * 12.0
        adj = max(0.0, min(25.0, (70.0 - float(competition_score)) / 70.0 * 25.0))
        return max(0.0, min(100.0, base + adj))

    def _normalize_strategic_value_score(self, keyword: str) -> float:
        kw = (keyword or "").strip().lower()
        prefer_terms = self._prefer_terms()
        matches = 0
        for t in prefer_terms:
            if t and t.strip().lower() in kw:
                matches += 1
        if matches <= 0:
            return 40.0
        return min(100.0, 80.0 + matches * 10.0)

    def _priority_score(
        self,
        *,
        keyword: str,
        search_volume: int,
        kd: float,
        competition_score: float,
        trend_score: float,
        content_gaps: Optional[List[str]],
        opportunities: Optional[List[str]],
    ) -> Dict[str, Any]:
        sv = self._normalize_search_volume_score(search_volume)
        kd_s = self._normalize_kd_score(kd)
        gap = self._normalize_competition_gap_score(
            content_gaps=content_gaps,
            opportunities=opportunities,
            competition_score=competition_score,
        )
        tr = max(0.0, min(100.0, float(trend_score)))
        st = self._normalize_strategic_value_score(keyword)

        weights = self._priority_weights()
        total_w = 0.0
        weighted_sum = 0.0
        parts = {
            "search_volume": sv,
            "keyword_difficulty": kd_s,
            "competition_gap": gap,
            "trending_score": tr,
            "strategic_value": st,
        }
        for k, v in parts.items():
            w = float(weights.get(k) or 0.0)
            if w <= 0:
                continue
            total_w += w
            weighted_sum += w * float(v)
        score = weighted_sum / total_w if total_w > 0 else 0.0
        return {"score": max(0.0, min(100.0, score)), "breakdown": parts, "weights": weights}

    def _rank_and_select_topics(self, topics: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        limit_val = max(1, int(limit or 1))
        sorted_topics = sorted(topics, key=lambda x: float(x.get("priority_score") or 0.0), reverse=True)
        topic_cfg = (self.config or {}).get("topic") if isinstance(self.config, dict) else {}
        out_cfg = (topic_cfg.get("output") or {}) if isinstance(topic_cfg, dict) else {}
        diversity = bool(out_cfg.get("prioritize_diversity")) if isinstance(out_cfg, dict) else False
        if not diversity:
            return sorted_topics[:limit_val]

        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for t in sorted_topics:
            buckets.setdefault(str(t.get("content_type") or ""), []).append(t)

        out: List[Dict[str, Any]] = []
        keys = sorted([k for k in buckets.keys() if k])
        if not keys:
            return sorted_topics[:limit_val]
        while len(out) < limit_val and any(buckets.get(k) for k in keys):
            for k in keys:
                if len(out) >= limit_val:
                    break
                if buckets.get(k):
                    out.append(buckets[k].pop(0))
        return out

    async def execute(
        self,
        keywords: List[str],
        min_search_volume: Optional[int] = None,
        max_kd: Optional[float] = None,
        limit: Optional[int] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode_val = (mode or self.mode or "mock").strip().lower()
        limits = self._get_limits(min_search_volume, max_kd)
        min_vol = limits["min_volume"]
        max_kd_val = limits["max_kd"]
        min_out = limits["min_out"]
        max_out = limits["max_out"]
        limit_val: int
        if isinstance(limit, int) and limit > 0:
            limit_val = int(limit)
        elif isinstance(limit, str) and limit.strip().isdigit():
            limit_val = int(limit.strip())
        else:
            limit_val = max_out
        limit_val = max(1, limit_val)
        desired_out = max(min_out, limit_val)

        warnings: List[str] = []
        keyword_tool = KeywordResearchTool(config={"mode": mode_val, "config": self.config})
        serp_tool = SERPAnalysisTool(config={"mode": mode_val, "config": self.config})
        trend_tool = TrendDetectionTool(config={"mode": mode_val, "config": self.config})

        raw_keyword_data: List[Dict[str, Any]] = []
        raw_serp_data: List[Dict[str, Any]] = []

        is_mock = mode_val != "live"
        data_confidence = "low" if is_mock else "high"

        try:
            kw_result = await keyword_tool.research_keywords(
                seed_keywords=keywords,
                min_search_volume=min_vol,
                max_kd=max_kd_val,
                limit=desired_out * 4,
            )
            raw_keyword_data = [self._keyword_to_dict(k) for k in kw_result.primary_keywords + kw_result.long_tail_keywords]
        except Exception as e:
            warnings.append(f"keyword_research_failed:{e}")
            data_confidence = "none"
            kw_result = None

        candidate_keywords: List[KeywordData] = []
        if kw_result:
            candidate_keywords = (kw_result.primary_keywords + kw_result.long_tail_keywords)[: desired_out]

        topics: List[Dict[str, Any]] = []
        try:
            for idx, kw in enumerate(candidate_keywords):
                serp = None
                try:
                    serp = await serp_tool.analyze_serp(kw.keyword)
                    raw_serp_data.append(self._serp_to_dict(serp))
                except Exception as e:
                    warnings.append(f"serp_analysis_failed:{kw.keyword}:{e}")
                    raw_serp_data.append({})
                    if mode_val == "live":
                        continue

                if mode_val == "live" and (not serp or getattr(serp, "total_results", 0) == 0):
                    warnings.append(f"serp_no_results_in_live_mode:{kw.keyword}")
                    continue

                competition_score = float(getattr(serp, "competition_score", 0) or 0)
                competition_level = "low" if competition_score < 30 else ("medium" if competition_score < 60 else "high")

                trend_score = 0
                try:
                    trend_info = await trend_tool.detect_trends([kw.keyword], time_range="30d")
                    trend_score = trend_info[0].trend_score if trend_info else 0
                except Exception as e:
                    warnings.append(f"trend_detection_failed:{kw.keyword}:{e}")

                content_type = self._infer_content_type(kw.keyword)
                search_intent = self._infer_intent(kw.keyword)

                gaps = list(getattr(serp, "content_gaps", None) or []) if serp else []
                opps = list(getattr(serp, "opportunities", None) or []) if serp else []
                pr = self._priority_score(
                    keyword=kw.keyword,
                    search_volume=int(kw.search_volume or 0),
                    kd=float(kw.keyword_difficulty or 0),
                    competition_score=competition_score,
                    trend_score=float(trend_score or 0),
                    content_gaps=gaps,
                    opportunities=opps,
                )
                priority = self._priority_label(score=float(pr["score"]))

                outline_points = []
                if gaps:
                    outline_points.extend(gaps[:2])
                if opps:
                    outline_points.extend(opps[:2])
                outline_points = outline_points[:5]

                reason_parts = []
                reason_parts.append(f"搜索量{kw.search_volume}")
                reason_parts.append(f"难度{kw.keyword_difficulty}")
                reason_parts.append(f"竞争{competition_level}")
                if trend_score:
                    reason_parts.append(f"趋势{int(trend_score)}")

                estimated_difficulty = self._estimated_difficulty(
                    kd=float(kw.keyword_difficulty or 0),
                    competition=competition_score,
                    content_type=content_type,
                )

                topics.append(
                    {
                        "id": f"topic_{idx+1:03d}",
                        "title": self._suggest_title(kw.keyword, content_type),
                        "target_keywords": [kw.keyword],
                        "search_volume": int(kw.search_volume or 0),
                        "keyword_difficulty": float(kw.keyword_difficulty or 0),
                        "competition_level": competition_level,
                        "content_type": content_type,
                        "search_intent": search_intent,
                        "outline_points": outline_points,
                        "priority_score": float(pr["score"]),
                        "priority": priority,
                        "reason": "，".join(reason_parts),
                        "estimated_difficulty": estimated_difficulty,
                        "data_sources": [x for x in {kw.source, "serp", "trends"} if x],
                    }
                )
        finally:
            await trend_tool.close()

        topics = self._rank_and_select_topics(topics, limit_val)

        if mode_val == "live":
            if not os.environ.get("SERPAPI_API_KEY", "").strip():
                warnings.append("missing_serpapi_api_key")
            if data_confidence == "none":
                warnings.append("live_mode_no_data")
        else:
            warnings.append("is_mock:true")

        return {
            "topics": topics,
            "raw_keyword_data": raw_keyword_data,
            "raw_serp_data": raw_serp_data,
            "warnings": warnings,
            "is_mock": is_mock,
            "data_confidence": data_confidence,
            "generated_at": datetime.now().isoformat(),
        }

    def _keyword_to_dict(self, kw: KeywordData) -> Dict[str, Any]:
        return {
            "keyword": kw.keyword,
            "search_volume": int(kw.search_volume or 0),
            "keyword_difficulty": float(kw.keyword_difficulty or 0),
            "cpc": kw.cpc,
            "competition": kw.competition,
            "source": kw.source,
            "is_mock": bool(getattr(kw, "is_mock", False)),
            "data_confidence": str(getattr(kw, "data_confidence", "unknown")),
            "fetched_at": getattr(kw, "fetched_at", None),
        }

    def _serp_to_dict(self, serp: Any) -> Dict[str, Any]:
        if not serp:
            return {}
        return {
            "keyword": serp.keyword,
            "total_results": serp.total_results,
            "competition_score": serp.competition_score,
            "top_domains": serp.top_domains or [],
            "avg_word_count": serp.avg_word_count,
            "content_gaps": serp.content_gaps or [],
            "opportunities": serp.opportunities or [],
        }

    def _infer_intent(self, keyword: str) -> str:
        kw = (keyword or "").strip().lower()
        if any(x in kw for x in ["官网", "official", "site:"]):
            return "navigational"
        if any(x in kw for x in ["报名", "购买", "价格", "多少钱", "费用", "apply"]):
            return "transactional"
        if any(kw.startswith(x) for x in ["如何", "怎么", "为什么", "what", "how", "why"]):
            return "informational"
        if any(x in kw for x in ["指南", "攻略", "教程", "方法", "流程", "对比", "vs"]):
            return "informational"
        return "informational"

    def _infer_content_type(self, keyword: str) -> str:
        kw = (keyword or "").strip().lower()
        if any(kw.startswith(x) for x in ["如何", "怎么"]):
            return "how_to"
        if any(x in kw for x in ["对比", "比较", "vs", "versus"]):
            return "comparison"
        if any(x in kw for x in ["清单", "top", "10个", "5个", "清单"]):
            return "list"
        if any(x in kw for x in ["案例", "case study", "实例"]):
            return "case_study"
        if any(x in kw for x in ["指南", "攻略", "教程"]):
            return "guide"
        return "guide"

    def _estimated_difficulty(self, *, kd: float, competition: float, content_type: str) -> str:
        base = 25.0
        if content_type in {"how_to", "guide"}:
            base = 35.0
        elif content_type in {"comparison", "case_study"}:
            base = 55.0
        score = base + kd * 0.4 + competition * 0.35
        if score < 45:
            return "easy"
        if score < 70:
            return "medium"
        return "hard"

    def _suggest_title(self, keyword: str, content_type: str) -> str:
        kw = (keyword or "").strip()
        if not kw:
            return ""
        if content_type == "comparison":
            return f"{kw}对比：怎么选更合适"
        if content_type == "how_to":
            return f"{kw}操作指南：步骤、要点与避坑"
        if content_type == "list":
            return f"{kw}清单：5个关键点快速掌握"
        if content_type == "case_study":
            return f"{kw}案例解析：方法与可复用经验"
        return f"{kw}完整指南：核心要点与实用建议"

    def _priority_label(self, *, score: float) -> str:
        if score >= 60:
            return "high"
        if score >= 35:
            return "medium"
        return "low"
