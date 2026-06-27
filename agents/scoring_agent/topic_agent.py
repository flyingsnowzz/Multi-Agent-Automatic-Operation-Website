from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path
import re

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))


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

        if yaml is None:
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

    def _business_semantics(self) -> Dict[str, Any]:
        bs = (self.config or {}).get("business_semantics") if isinstance(self.config, dict) else {}
        return bs if isinstance(bs, dict) else {}

    def _quality_gates(self) -> Dict[str, Any]:
        gates = (self.config or {}).get("quality_gates") if isinstance(self.config, dict) else {}
        return gates if isinstance(gates, dict) else {}

    def _forbidden_patterns(self) -> List[str]:
        bs = self._business_semantics()
        out: List[str] = []
        if isinstance(bs.get("forbidden_patterns"), list):
            out.extend([str(x) for x in bs.get("forbidden_patterns") if str(x).strip()])
        kw_cfg = (self.config or {}).get("keyword_research") if isinstance(self.config, dict) else {}
        filters_cfg = (kw_cfg.get("filters") or {}) if isinstance(kw_cfg, dict) else {}
        if isinstance(filters_cfg.get("exclude"), list):
            out.extend([str(x) for x in filters_cfg.get("exclude") if str(x).strip()])
        seen: set[str] = set()
        uniq: List[str] = []
        for x in out:
            key = re.sub(r"\s+", "", x)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(x)
        return uniq

    def _semantic_min_score(self) -> float:
        gates = self._quality_gates()
        try:
            return float(gates.get("semantic_min_score") if gates.get("semantic_min_score") is not None else 70)
        except Exception:
            return 70.0

    def _min_title_len(self) -> int:
        gates = self._quality_gates()
        try:
            return int(gates.get("title_min_len") if gates.get("title_min_len") is not None else 12)
        except Exception:
            return 12

    def _min_keyword_len(self) -> int:
        gates = self._quality_gates()
        try:
            return int(gates.get("keyword_min_len") if gates.get("keyword_min_len") is not None else 4)
        except Exception:
            return 4

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip().lower())

    def _contains_forbidden(self, text: str) -> Optional[str]:
        t = self._normalize_text(text)
        for p in self._forbidden_patterns():
            pn = self._normalize_text(p)
            if pn and pn in t:
                return str(p)
        return None

    def _topic_entity(self, keyword: str) -> str:
        kw = str(keyword or "")
        if "EMBA" in kw.upper():
            return "EMBA"
        if "商学院" in kw:
            return "商学院"
        return kw.strip()

    def _infer_topic_angle(self, keyword: str) -> str:
        kw = str(keyword or "")
        if any(x in kw for x in ["报考条件", "报考", "条件"]):
            return "conditions"
        if any(x in kw for x in ["申请流程", "申请", "流程", "材料"]):
            return "process"
        if any(x in kw for x in ["院校", "怎么选", "选择", "选校"]):
            return "school_selection"
        if any(x in kw for x in ["区别", "对比", "比较", "vs", "VS", "versus"]):
            return "comparison"
        if any(x in kw for x in ["学费", "费用", "成本", "回报", "ROI", "值不值"]):
            return "roi"
        if any(x in kw for x in ["课程", "价值", "收获", "提升"]):
            return "value"
        if any(x in kw for x in ["适合", "值得", "有没有用", "有什么用", "吗"]):
            return "fit"
        return "general"

    def _suggest_title(self, keyword: str, content_type: str) -> str:
        entity = self._topic_entity(keyword)
        angle = self._infer_topic_angle(keyword)
        year = datetime.now().year
        if angle == "conditions":
            return f"{year}年{entity}报考条件详解：适合人群、申请流程与准备建议"
        if angle == "process":
            return f"{entity}申请流程全解析：材料清单、时间规划与准备建议"
        if angle == "school_selection":
            return f"{entity}院校怎么选：从课程方向、师资资源到校友网络的判断依据"
        if angle == "comparison":
            return f"{entity}和MBA有什么区别：企业高管如何选择更合适"
        if angle == "roi":
            return f"企业管理者读{entity}是否值得：投入成本、课程价值与职业回报分析"
        if angle == "value":
            return f"{entity}课程价值有哪些：对管理能力与职业发展的真实帮助"
        if angle == "fit":
            return f"企业高管适合读{entity}吗：人群画像、学习节奏与决策建议"
        if content_type == "case_study":
            return f"{entity}案例解析：选择逻辑、关键指标与可复用经验"
        return f"{entity}入门详解：核心问题、常见误区与下一步行动建议"

    def _semantic_quality_check(self, *, keyword: str, title: str) -> Dict[str, Any]:
        warnings: List[str] = []
        score = 100.0

        forbidden = self._contains_forbidden(keyword) or self._contains_forbidden(title)
        if forbidden:
            return {"score": 0.0, "warnings": [f"forbidden_pattern:{forbidden}"]}

        kw = str(keyword or "").strip()
        ttl = str(title or "").strip()

        if len(kw) < self._min_keyword_len():
            warnings.append("keyword_too_short")
            score -= 35.0
        if len(ttl) < self._min_title_len():
            warnings.append("title_too_short")
            score -= 25.0

        if re.match(r"^(怎么|如何)\s*[A-Za-z\u4e00-\u9fff]{2,12}$", kw) and ("怎么选" not in kw and "如何选" not in kw):
            warnings.append("keyword_incomplete_question")
            score -= 60.0

        bad_phrases = ["完整指南", "核心要点", "实用建议", "避坑", "技巧", "方法", "工具"]
        if any(x in kw for x in bad_phrases) or any(x in ttl for x in bad_phrases):
            warnings.append("generic_or_mechanical_phrase")
            score -= 35.0

        if not any(x in (kw + ttl) for x in ["EMBA", "MBA", "商学院"]):
            warnings.append("out_of_business_domain")
            score -= 70.0

        angle_terms = ["报考", "条件", "申请", "流程", "院校", "怎么选", "区别", "对比", "学费", "回报", "课程", "价值", "适合", "值得"]
        if not any(x in ttl for x in angle_terms):
            warnings.append("missing_clear_angle_in_title")
            score -= 20.0

        audience_terms = ["企业高管", "企业管理者", "管理者", "创业者", "申请者"]
        if any(x in ttl for x in audience_terms):
            score += 5.0

        score = max(0.0, min(100.0, score))
        return {"score": score, "warnings": warnings}

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
        sorted_topics = sorted(
            topics,
            key=lambda x: float(x.get("_rank_score") or x.get("priority_score") or 0.0),
            reverse=True,
        )
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
        from agents.scoring_agent.tools.keyword_research import KeywordResearchTool
        from agents.scoring_agent.tools.serp_analysis import SERPAnalysisTool
        from agents.scoring_agent.tools.trend_detection import TrendDetectionTool

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
                        "title": "",
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

        semantic_min = self._semantic_min_score()
        filtered_topics: List[Dict[str, Any]] = []
        dropped = 0
        for t in topics:
            title = self._suggest_title((t.get("target_keywords") or [""])[0], str(t.get("content_type") or "guide"))
            t["title"] = title
            qc = self._semantic_quality_check(keyword=(t.get("target_keywords") or [""])[0], title=title)
            t["semantic_quality_score"] = float(qc["score"])
            t["quality_warnings"] = list(qc["warnings"])
            t["_rank_score"] = float(t.get("priority_score") or 0.0) * 0.35 + float(t.get("semantic_quality_score") or 0.0) * 0.65
            if float(t["semantic_quality_score"]) < semantic_min:
                dropped += 1
                continue
            filtered_topics.append(t)

        if dropped:
            warnings.append(f"semantic_filtered:{dropped}")

        topics = self._rank_and_select_topics(filtered_topics, limit_val)

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

    def _priority_label(self, *, score: float) -> str:
        if score >= 60:
            return "high"
        if score >= 35:
            return "medium"
        return "low"

    async def execute_on_candidates(
        self,
        candidates: List[Dict[str, Any]],
        mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        根据初筛通过的候选素材(pass_to_topic)，加工成选题。
        对每个候选素材的主题线索进行意图判断、内容类型推导，并生成文章标题、大纲建议和优先级推荐。
        """
        from agents.scoring_agent.tools.keyword_research import KeywordResearchTool
        from agents.scoring_agent.tools.serp_analysis import SERPAnalysisTool
        from agents.scoring_agent.tools.trend_detection import TrendDetectionTool

        mode_val = (mode or self.mode or "mock").strip().lower()
        keyword_tool = KeywordResearchTool(config={"mode": mode_val, "config": self.config})
        serp_tool = SERPAnalysisTool(config={"mode": mode_val, "config": self.config})
        trend_tool = TrendDetectionTool(config={"mode": mode_val, "config": self.config})

        # 读取候选初筛筛选配置
        cfg_screening = (self.config or {}).get("candidate_screening") or {}
        min_priority = float(cfg_screening.get("min_priority_score") if cfg_screening.get("min_priority_score") is not None else 35.0)
        require_intent = bool(cfg_screening.get("require_search_intent", True))
        require_relevance = bool(cfg_screening.get("require_business_relevance", True))
        
        cfg_routes = (self.config or {}).get("workflow_routes") or {}
        rewrite_route = cfg_routes.get("rewrite_candidate") or "full_rewrite_flow"
        publish_route = cfg_routes.get("publish_candidate") or "light_publish_flow"

        warnings: List[str] = []
        topics: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        try:
            for idx, cand in enumerate(candidates):
                material_score = float(cand.get("material_score") or 0.0)
                route_tier = cand.get("route_tier")
                rewrite_required = bool(cand.get("rewrite_required", False))
                publish_candidate = bool(cand.get("publish_candidate", False))

                topic_hint = cand.get("topic_hint")
                if topic_hint is None:
                    topic_hint = cand.get("source_title")
                topic_hint = str(topic_hint or "").strip()
                
                # 1. 检查 topic_hint 是否为空
                if not topic_hint:
                    rejected.append({
                        "id": f"topic_cand_{idx+1:03d}",
                        "title": "",
                        "target_keywords": [],
                        "search_volume": 0,
                        "keyword_difficulty": 0.0,
                        "competition_level": "low",
                        "content_type": "guide",
                        "search_intent": "",
                        "outline_points": [],
                        "priority_score": 0.0,
                        "priority": "low",
                        "reason": "主题线索为空",
                        "estimated_difficulty": "easy",
                        "data_sources": ["crawler"],
                        "semantic_quality_score": 0.0,
                        "quality_warnings": ["missing_topic_hint"],
                        # 候选素材原属性持久化
                        "candidate_id": cand.get("id"),
                        "route_tier": route_tier,
                        "rewrite_required": rewrite_required,
                        "publish_candidate": publish_candidate,
                        "source_title": cand.get("source_title"),
                        "source_summary": cand.get("source_summary"),
                        "source_url": cand.get("source_url"),
                        "source_content": cand.get("source_content") or cand.get("source_summary") or "",
                        "material_score": material_score,
                        "primary_keyword": "",
                        "secondary_keywords": [],
                        "content_angle": "",
                        "evaluation": cand.get("evaluation") or {},
                        "dedup": cand.get("dedup") or {},
                        "is_accepted": False,
                        "reject_reason": "主题线索 (topic_hint) 为空，无法提炼选题。",
                        "routing_payload": cand.get("routing_payload") or {},
                    })
                    continue

                # 获取或者研究关键词指标
                search_volume = int(cand.get("search_volume") if cand.get("search_volume") is not None else 200)
                kd = float(cand.get("keyword_difficulty") if cand.get("keyword_difficulty") is not None else (cand.get("kd") if cand.get("kd") is not None else 20.0))
                competition_score = float(cand.get("competition_score") if cand.get("competition_score") is not None else 30.0)

                if mode_val == "live":
                    try:
                        # 尝试通过工具研究候选词
                        kw_res = await keyword_tool.research_keywords([topic_hint], limit=1)
                        if kw_res and (kw_res.primary_keywords or kw_res.long_tail_keywords):
                            k_data = (kw_res.primary_keywords + kw_res.long_tail_keywords)[0]
                            search_volume = int(k_data.search_volume or 200)
                            kd = float(k_data.keyword_difficulty or 20.0)
                    except Exception as e:
                        warnings.append(f"live_keyword_research_failed:{topic_hint}:{e}")

                    try:
                        serp = await serp_tool.analyze_serp(topic_hint)
                        if serp:
                            competition_score = float(getattr(serp, "competition_score", 30.0) or 30.0)
                    except Exception as e:
                        warnings.append(f"live_serp_analysis_failed:{topic_hint}:{e}")

                content_type = self._infer_content_type(topic_hint)
                search_intent = self._infer_intent(topic_hint)
                content_angle = self._infer_topic_angle(topic_hint)
                title = self._suggest_title(topic_hint, content_type)
                target_keywords = [topic_hint]
                primary_keyword = topic_hint
                secondary_keywords: List[str] = []

                # 检查语义质量
                qc = self._semantic_quality_check(keyword=topic_hint, title=title)
                semantic_quality_score = float(qc["score"])
                quality_warnings = list(qc["warnings"])

                pr = self._priority_score(
                    keyword=topic_hint,
                    search_volume=search_volume,
                    kd=kd,
                    competition_score=competition_score,
                    trend_score=0.0,
                    content_gaps=[],
                    opportunities=[],
                )
                priority = self._priority_label(score=float(pr["score"]))
                priority_score = float(pr["score"])

                outline_points = [
                    f"分析 {topic_hint} 的背景与现状",
                    f"核心论点 1：解析 {cand.get('source_title', '') or topic_hint} 包含的重点内容",
                    f"核心论点 2：如何针对本选题做深度落地与实践",
                    "总结与下一步行动建议"
                ]

                reason = f"来自爬虫素材初筛（分级: {route_tier or '无'}，素材评分: {material_score:.1f}）"

                # 检查筛选条件是否满足选题要求（对所有候选素材执行筛选规则）
                is_accepted = True
                reject_reasons = []

                # 1. 基础关键词检查 (长度 & 违禁词)
                if len(topic_hint) < self._min_keyword_len():
                    is_accepted = False
                    reject_reasons.append(f"关键词长度 {len(topic_hint)} 低于限制 {self._min_keyword_len()}")
                
                forbidden = self._contains_forbidden(topic_hint)
                if forbidden:
                    is_accepted = False
                    reject_reasons.append(f"命中违禁模式: {forbidden}")

                # 2. 行业相关性检查
                if require_relevance and "out_of_business_domain" in quality_warnings:
                    is_accepted = False
                    reject_reasons.append("与高管教育/商学/EMBA核心领域相关性不足")

                # 3. 搜索意图检查
                if require_intent and not search_intent:
                    is_accepted = False
                    reject_reasons.append("搜索意图缺失")

                # 4. 推荐分数检查
                if priority_score < min_priority:
                    is_accepted = False
                    reject_reasons.append(f"选题推荐分 {priority_score:.1f} 低于最低限制 {min_priority:.1f}")

                # 5. 语义质量分检查
                semantic_min = self._semantic_min_score()
                if semantic_quality_score < semantic_min:
                    is_accepted = False
                    reject_reasons.append(f"语义质量分 {semantic_quality_score:.1f} 低于设定阈值 {semantic_min:.1f}。警告: {', '.join(quality_warnings)}")

                reject_reason = "; ".join(reject_reasons) if reject_reasons else None
                workflow_route = None
                if is_accepted:
                    if route_tier == "rewrite_candidate":
                        workflow_route = rewrite_route
                    elif route_tier == "publish_candidate":
                        workflow_route = publish_route

                topic_item = {
                    "id": f"topic_cand_{idx+1:03d}",
                    "title": title,
                    "primary_keyword": primary_keyword,
                    "secondary_keywords": secondary_keywords,
                    "target_keywords": target_keywords,
                    "search_volume": int(search_volume),
                    "keyword_difficulty": float(kd),
                    "competition_level": "low" if competition_score < 30 else ("medium" if competition_score < 60 else "high"),
                    "content_type": content_type,
                    "search_intent": search_intent,
                    "content_angle": content_angle,
                    "outline_points": outline_points,
                    "priority_score": priority_score,
                    "priority": priority,
                    "reason": reason,
                    "estimated_difficulty": self._estimated_difficulty(kd=kd, competition=competition_score, content_type=content_type),
                    "data_sources": ["crawler"],
                    "semantic_quality_score": semantic_quality_score,
                    "quality_warnings": quality_warnings,
                    # 候选素材原属性持久化至 topic 对象，方便后续流程读取
                    "candidate_id": cand.get("id"),
                    "route_tier": route_tier,
                    "rewrite_required": rewrite_required,
                    "publish_candidate": publish_candidate,
                    "source_title": cand.get("source_title"),
                    "source_summary": cand.get("source_summary"),
                    "source_url": cand.get("source_url"),
                    "source_content": cand.get("source_content") or cand.get("source_summary") or "",
                    "material_score": material_score,
                    "evaluation": cand.get("evaluation") or {},
                    "dedup": cand.get("dedup") or {},
                    "is_accepted": is_accepted,
                    "reject_reason": reject_reason,
                    "workflow_route": workflow_route,
                    "routing_payload": cand.get("routing_payload") or {},
                }

                if is_accepted:
                    topics.append(topic_item)
                else:
                    rejected.append(topic_item)
        finally:
            await trend_tool.close()

        return {
            "topics": topics,
            "rejected": rejected,
            "accepted_count": len(topics),
            "rejected_count": len(rejected),
            "warnings": warnings,
            "is_mock": mode_val != "live",
            "data_confidence": "low" if mode_val != "live" else "high",
            "generated_at": datetime.now().isoformat(),
        }

    def summarize_from_articles(
        self,
        articles: List[Dict[str, Any]],
        manual_article_scores: Optional[Dict[Any, Dict[str, Any]]] = None,
        output_count: int = 20,
        use_ai: bool = True,
        ai_client: Optional[Any] = None,
        ai_config: Optional[Dict[str, Any]] = None,
        fetch_from_url: bool = False,
        db_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Score crawler articles without producing topic rankings.

        Args:
            fetch_from_url: 从 original_url 抓取原文（替代 description）
            db_config: 数据库配置，用于标记抓取失败的文章
        """
        from agents.scoring_agent.scoring_summary import summarize_crawler_topics

        return summarize_crawler_topics(
            articles=articles,
            manual_article_scores=manual_article_scores,
            output_count=output_count,
            use_ai=use_ai,
            ai_client=ai_client,
            ai_config=ai_config,
            fetch_from_url=fetch_from_url,
            db_config=db_config,
        )
