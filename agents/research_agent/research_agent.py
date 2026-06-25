import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from agents.research_agent.tools.citation_formatter import CitationFormatter, CitationStyle
from agents.research_agent.tools.data_collector import DataCollector
from agents.crawler_processor_agent.tools.url_content_fetcher import URLContentFetcher


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


def normalize_research_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = dict(src)

    mapping = {
        "background_info": "background",
        "key_statistics": "statistics",
        "case_studies": "cases",
        "expert_quotes": "quotes",
        "detailed_outline": "outline",
    }
    for old, new in mapping.items():
        if new not in out and old in out:
            out[new] = out.get(old)

    background = out.get("background")
    if not isinstance(background, dict):
        background = {}
    outline = out.get("outline")
    if not isinstance(outline, dict):
        outline = {}

    def _as_list(v: Any) -> List[Any]:
        if isinstance(v, list):
            return v
        if v is None:
            return []
        return [v]

    out["background"] = {
        "definition": str(background.get("definition") or ""),
        "industry_context": str(background.get("industry_context") or ""),
        "common_pain_points": _as_list(background.get("common_pain_points")),
    }
    out["statistics"] = _as_list(out.get("statistics"))
    out["cases"] = _as_list(out.get("cases"))
    out["quotes"] = _as_list(out.get("quotes"))
    out["sources"] = _as_list(out.get("sources"))
    citations_raw = _as_list(out.get("citations"))
    citations: List[Dict[str, str]] = []
    for item in citations_raw:
        if not isinstance(item, dict):
            citation_text = str(item or "").strip()
            if not citation_text:
                continue
            citations.append(
                {
                    "title": "",
                    "url": "",
                    "source": "unknown",
                    "authority": "",
                    "citation": citation_text,
                    "note": "",
                }
            )
            continue

        if "citation" not in item and "type" in item and "name" in item:
            continue

        source = str(item.get("source") or "unknown")
        authority = str(item.get("authority") or "")
        note = str(item.get("note") or "")
        if source == "mock_source":
            authority = authority or "low"
            note = note or "mock_source"

        citations.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "source": source,
                "authority": authority,
                "citation": str(item.get("citation") or ""),
                "note": note,
            }
        )
    out["citations"] = citations
    out["outline"] = {
        "sections": _as_list(outline.get("sections")),
    }
    out["warnings"] = _as_list(out.get("warnings"))
    out["is_mock"] = bool(out.get("is_mock", True))
    out["data_confidence"] = str(out.get("data_confidence") or ("low" if out["is_mock"] else "unknown"))
    out["generated_at"] = str(out.get("generated_at") or datetime.now().isoformat())
    return out


def validate_research_result(result: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    r = result if isinstance(result, dict) else {}
    if not isinstance(r.get("background"), dict):
        out.append("background_not_dict")
    if not isinstance(r.get("outline"), dict):
        out.append("outline_not_dict")
    if not isinstance(((r.get("outline") or {}) if isinstance(r.get("outline"), dict) else {}).get("sections"), list):
        out.append("outline_sections_not_list")
    for k in ("statistics", "cases", "quotes", "sources", "citations", "warnings"):
        if not isinstance(r.get(k), list):
            out.append(f"{k}_not_list")
    return out


def _normalize_space(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate_text(text: Any, limit: int) -> str:
    value = _normalize_space(text)
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _extract_json(text: str) -> Dict[str, Any]:
    raw = text if isinstance(text, str) else str(text or "")
    s = raw.strip()
    if "```" in s:
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]
    return json.loads(s)


def _word_count(text: Any) -> int:
    s = str(text or "")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", s))
    english = len(re.findall(r"\b[a-zA-Z]+\b", s))
    return chinese + english


def _score_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 1:
        n *= 100
    return max(0.0, min(n, 100.0))


def _bool_value(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "y", "是"}:
            return True
        if s in {"0", "false", "no", "n", "否"}:
            return False
    return None


def _split_sentences(text: Any) -> List[str]:
    raw = str(text or "")
    parts = re.split(r"[\n\r]+|(?<=[。！？!?；;])", raw)
    out: List[str] = []
    seen = set()
    for part in parts:
        s = _normalize_space(part)
        if len(s) < 6:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _uniq_strings(items: List[Any], limit: Optional[int] = None) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items or []:
        s = _normalize_space(item)
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if isinstance(limit, int) and limit > 0 and len(out) >= limit:
            break
    return out


class ResearchAgent:
    def __init__(self, config_path: str = "agents/research_agent/config.yaml", llm: Any = None):
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass
        self.config_path = config_path
        self.config = self._load_config()
        self.llm = llm
        if self.llm is None:
            self.llm = self._default_llm()

    def _default_llm(self) -> Any:
        cfg = (self.config or {}).get("llm") if isinstance(self.config, dict) else {}
        model = (cfg.get("model") or "gpt-4o") if isinstance(cfg, dict) else "gpt-4o"
        temperature = float((cfg.get("temperature") if isinstance(cfg, dict) else None) or 0.4)
        base_url = str((cfg.get("base_url") if isinstance(cfg, dict) else None) or "").strip() or None
        api_key = str((cfg.get("api_key") if isinstance(cfg, dict) else None) or "").strip() or None
        kwargs: Dict[str, Any] = {"model": model, "temperature": temperature}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        try:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(**kwargs)
        except Exception:
            return None

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

    def _topic_keywords(self, topic: Dict[str, Any]) -> Tuple[str, List[str]]:
        t = topic if isinstance(topic, dict) else {}
        title = str(t.get("title") or "").strip()
        primary = str(t.get("primary_keyword") or "").strip()
        kws: List[str] = []
        if primary:
            kws.append(primary)
        secondary = t.get("secondary_keywords")
        if secondary is None:
            secondary = t.get("target_keywords")
        if isinstance(secondary, list):
            kws.extend([str(x) for x in secondary if str(x).strip()])
        elif isinstance(secondary, str) and secondary.strip():
            kws.append(secondary.strip())
        if isinstance(t.get("target_keywords"), list):
            kws.extend([str(x) for x in t.get("target_keywords") if str(x).strip()])
        uniq: List[str] = []
        seen = set()
        for k in kws:
            kk = k.strip()
            if not kk or kk in seen:
                continue
            seen.add(kk)
            uniq.append(kk)
        return title, uniq

    def _is_emba_topic(self, topic: Dict[str, Any], keywords: List[str]) -> bool:
        title = str((topic or {}).get("title") or "")
        pool = " ".join([title] + (keywords or []))
        return "EMBA" in pool.upper() or "商学院" in pool or "MBA" in pool.upper() or "高管" in pool

    def _extract_mock_subject(self, *, title: str, primary_keyword: str, keywords: List[str]) -> str:
        raw = (primary_keyword or "").strip() or (title or "").strip()
        if not raw and keywords:
            raw = str(keywords[0] or "").strip()
        if not raw:
            return "主题"

        s = raw
        s = re.sub(r"^\d{4}年", "", s).strip()
        s = re.split(r"[：:—\-]", s, maxsplit=1)[0].strip()
        s = re.sub(r"(详解|攻略|指南|解读|大全)$", "", s).strip()
        s = s.replace("的区别", "")
        s = s.replace("怎么选", "选择").replace("如何选", "选择").replace("怎样选", "选择")

        for suffix in ("报考条件", "报名条件", "申请条件", "报考流程", "报名流程", "申请流程"):
            if s.endswith(suffix) and len(s) > len(suffix):
                s = s[: -len(suffix)].strip()
                break

        return s or "主题"

    def _extract_program_name(self, *, title: str, primary_keyword: str, keywords: List[str]) -> str:
        pool = " ".join([str(title or ""), str(primary_keyword or "")] + (keywords or []))
        if "EMBA" in pool.upper():
            return "EMBA"
        if "MBA" in pool.upper():
            return "MBA"
        return ""

    def _mock_sources(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "mock_source",
                "name": "mock_research_pack",
                "authority": "low",
                "note": "mock_source",
            }
        ]

    def _mock_citations(self) -> List[Dict[str, str]]:
        formatter = CitationFormatter(CitationStyle.GB_T7714)
        formatted = formatter.format_batch(
            [
                {
                    "type": "online",
                    "authors": "研究团队（模拟）",
                    "title": "调研素材包（模拟数据）",
                    "url": "",
                    "access_date": datetime.now().strftime("%Y-%m-%d"),
                }
            ]
        )
        return [
            {
                "title": "调研素材包（模拟数据）",
                "url": "",
                "source": "mock_source",
                "authority": "low",
                "citation": x,
                "note": "mock_source",
            }
            for x in formatted
        ]

    def _mock_outline_sections(self, topic: Dict[str, Any], keywords: List[str]) -> List[Dict[str, Any]]:
        t = topic if isinstance(topic, dict) else {}
        title = str(t.get("title") or "").strip()
        primary = str(t.get("primary_keyword") or (keywords[0] if keywords else "")).strip()
        points = t.get("outline_points")
        outline_points: List[str] = []
        if isinstance(points, list):
            outline_points = [str(x) for x in points if str(x).strip()]

        subject = self._extract_mock_subject(title=title, primary_keyword=primary, keywords=keywords)
        program = self._extract_program_name(title=title, primary_keyword=primary, keywords=keywords)
        label = program or subject

        sections: List[Dict[str, Any]] = []
        if self._is_emba_topic(topic, keywords):
            sections = [
                {
                    "title": f"适合人群：哪些人更适合读{label}",
                    "key_points": [
                        "典型画像：管理岗位、业务负责人或创业者",
                        "核心诉求：系统化管理框架、视野拓展与高质量同伴学习",
                        "不太适合的情况：时间/精力不可控或目标不清晰",
                    ],
                    "notes": "mock",
                },
                {
                    "title": "报考门槛：学历、工作年限与管理经验要求",
                    "key_points": [
                        "关注官方简章口径：学历层次与工作年限的组合要求",
                        "管理经验的证明材料：岗位职责、业绩成果与推荐信",
                        "提前排雷：资格边界与材料缺口（社保/劳动关系等）",
                    ],
                    "notes": "mock",
                },
                {
                    "title": "申请流程：材料准备、面试与时间线",
                    "key_points": [
                        "时间线拆解：网申/初审/笔面试/录取/缴费/入学",
                        "材料清单：身份证明、学历学位、履历与业绩证明",
                        "面试准备：个人陈述、管理案例与动机阐述",
                    ],
                    "notes": "mock",
                },
                {
                    "title": "选校思路：项目定位、课程方向与校友资源",
                    "key_points": [
                        "先定目标：行业资源、管理补齐还是战略视野",
                        "对比维度：课程结构、师资、上课安排与校友网络",
                        "验证方式：参加宣讲/旁听/校友访谈，核实学习强度",
                    ],
                    "notes": "mock",
                },
                {
                    "title": "准备建议：如何提高录取成功率与学习体验",
                    "key_points": [
                        "提前准备个人故事线：职业节点、挑战与成长证据",
                        "补齐短板：英语/写作/财务基础（按院校要求）",
                        "学习规划：时间管理、出差安排与家庭支持沟通",
                    ],
                    "notes": "mock",
                },
            ]
        else:
            sections = [
                {
                    "title": f"核心概念：{subject}到底在解决什么问题",
                    "key_points": [
                        "明确适用场景与边界条件，避免概念泛化",
                        "拆分组成要素：目标、对象、约束与衡量指标",
                        "给出一句话定义，便于写作时统一口径",
                    ],
                    "notes": "mock",
                },
                {
                    "title": "关键要点：影响结果的变量与判断标准",
                    "key_points": [
                        "识别关键变量：资源投入、周期、组织协作与外部环境",
                        "建立判断标准：优先级、取舍原则与风险阈值",
                        "常见误区：只看工具或只看案例不看约束",
                    ],
                    "notes": "mock",
                },
                {
                    "title": "实施步骤：从规划到落地的可执行路径",
                    "key_points": [
                        "步骤1：明确目标与指标，设置里程碑",
                        "步骤2：选择方法与工具，设计协作机制",
                        "步骤3：小步试点、复盘迭代，再规模化推广",
                    ],
                    "notes": "mock",
                },
                {
                    "title": "风险与误区：容易踩坑的点与规避方式",
                    "key_points": [
                        "风险识别：资源不足、协同失败或数据不可信",
                        "规避策略：预案、验收标准与责任分工",
                        "持续改进：用复盘闭环替代一次性“大跃进”",
                    ],
                    "notes": "mock",
                },
            ]

        if outline_points:
            merged_points = [p for p in outline_points if p not in " ".join([s["title"] for s in sections])]
            if merged_points:
                sections[0]["key_points"] = list(sections[0]["key_points"] or []) + merged_points[:2]

        return sections[:5] if len(sections) >= 3 else (sections + sections)[:3]

    def _mock_materials(self, topic: Dict[str, Any], keywords: List[str]) -> Dict[str, Any]:
        primary = str((topic or {}).get("primary_keyword") or (keywords[0] if keywords else "")).strip()
        primary = primary or "EMBA"
        is_emba = self._is_emba_topic(topic, keywords)

        if is_emba:
            definition = f"{primary}通常指面向企业管理者的在职高管教育项目，强调管理能力提升、商业视野拓展与高质量社交网络。"
            industry_context = "高管教育需求与企业管理复杂度上升相关，学习者普遍关注时间投入、机会成本、课程含金量与职业回报。"
            pain_points = [
                "报考条件与申请流程信息分散，准备节奏难把控",
                "院校选择缺乏可量化对比维度，容易被单一宣传点影响",
                "学费与回报评估困难，难以判断是否值得投入",
                "工作与学习时间冲突，担心影响主业绩效",
            ]
            statistics = [
                {
                    "metric": "项目学费区间（示例）",
                    "value": "30-80",
                    "unit": "万元",
                    "note": "mock_estimated",
                    "source": "mock_source",
                },
                {
                    "metric": "常见学习周期（示例）",
                    "value": "18-24",
                    "unit": "个月",
                    "note": "mock_estimated",
                    "source": "mock_source",
                },
            ]
            cases = [
                {
                    "title": "案例A（脱敏）：制造业总经理的选校决策",
                    "background": "希望补齐战略与组织管理能力，同时拓展同业资源。",
                    "actions": ["对比课程方向与师资结构", "访谈校友了解学习强度", "评估出差频率与上课安排"],
                    "outcome": "选择更匹配行业与资源网络的项目，并提前规划时间与团队授权。",
                    "source": "mock_case",
                }
            ]
            quotes = [
                {
                    "quote": "高管教育的价值不在于知识点堆叠，而在于系统化的管理框架与高质量同伴学习。",
                    "speaker": "模拟专家",
                    "role": "商学院课程顾问（模拟）",
                    "source": "mock_expert",
                }
            ]
        else:
            definition = f"{primary}是一个需要结合业务场景理解的主题，通常涉及概念、方法与落地路径。"
            industry_context = "不同企业规模与行业阶段会显著影响实践路径，应优先明确目标与约束条件。"
            pain_points = ["概念边界不清", "缺少可执行方法", "难以衡量收益", "容易陷入工具化误区"]
            statistics = [
                {"metric": "关键指标示例", "value": "N/A", "unit": "", "note": "mock_estimated", "source": "mock_source"}
            ]
            cases = [
                {"title": "案例（模拟）", "background": "业务场景描述", "actions": ["步骤1", "步骤2"], "outcome": "结果描述", "source": "mock_case"}
            ]
            quotes = [
                {"quote": "先定义目标，再选择方法。", "speaker": "模拟专家", "role": "顾问（模拟）", "source": "mock_expert"}
            ]

        sources = self._mock_sources()
        citations = self._mock_citations()
        outline_sections = self._mock_outline_sections(topic, keywords)
        return {
            "background": {"definition": definition, "industry_context": industry_context, "common_pain_points": pain_points},
            "statistics": statistics,
            "cases": cases,
            "quotes": quotes,
            "sources": sources,
            "citations": citations,
            "outline": {"sections": outline_sections},
        }

    def _brief_config(self) -> Dict[str, int]:
        cfg = (self.config or {}).get("brief") if isinstance(self.config, dict) else {}

        def _int(name: str, default: int) -> int:
            try:
                return int(cfg.get(name) if isinstance(cfg, dict) and cfg.get(name) is not None else default)
            except Exception:
                return default

        return {
            "max_source_chars": _int("max_source_chars", 3000),
            "max_highlights": _int("max_highlights", 6),
            "max_risk_points": _int("max_risk_points", 5),
            "max_outline_sections": _int("max_outline_sections", 4),
            "max_keywords": _int("max_keywords", 6),
            "max_facts": _int("max_facts", 5),
        }

    def _writing_config(self) -> Dict[str, Any]:
        cfg = (self.config or {}).get("writing_brief") if isinstance(self.config, dict) else {}
        cfg = cfg if isinstance(cfg, dict) else {}

        def _int(name: str, default: int) -> int:
            try:
                return int(cfg.get(name) if cfg.get(name) is not None else default)
            except Exception:
                return default

        return {
            "standard_min_words": _int("standard_min_words", 900),
            "standard_max_words": _int("standard_max_words", 1200),
            "target_word_count": _int("target_word_count", 1100),
            "notice_min_words": _int("notice_min_words", 300),
            "notice_max_words": _int("notice_max_words", 800),
            "notice_target_word_count": _int("notice_target_word_count", 600),
            "high_score_min": _int("high_score_min", 75),
            "high_score_max": _int("high_score_max", 90),
            "title_major_rewrite_threshold": _int("title_major_rewrite_threshold", 70),
            "word_count_score_low_threshold": _int("word_count_score_low_threshold", 70),
        }

    def _is_rewrite_candidate_input(self, topic: Dict[str, Any]) -> bool:
        t = topic if isinstance(topic, dict) else {}
        return (
            str(t.get("workflow_route") or "").strip() == "full_rewrite_flow"
            and str(t.get("route_tier") or "").strip() == "rewrite_candidate"
        )

    def _angle_label(self, angle: str) -> str:
        mapping = {
            "conditions": "条件指南",
            "process": "流程指南",
            "school_selection": "选校对比",
            "comparison": "对比分析",
            "roi": "价值评估",
            "value": "价值指南",
            "fit": "决策建议",
            "general": "通用指南",
        }
        key = str(angle or "").strip()
        return mapping.get(key, key or "通用指南")

    def _normalize_rewrite_topic(self, topic: Dict[str, Any]) -> Dict[str, Any]:
        t = topic if isinstance(topic, dict) else {}
        target_keywords = t.get("target_keywords")
        if not isinstance(target_keywords, list):
            target_keywords = []

        primary_keyword = _normalize_space(t.get("primary_keyword") or (target_keywords[0] if target_keywords else ""))
        secondary = t.get("secondary_keywords")
        if not isinstance(secondary, list):
            secondary = [x for x in target_keywords[1:] if _normalize_space(x)]
        secondary_keywords = _uniq_strings([str(x) for x in secondary], limit=self._brief_config()["max_keywords"])
        target_keywords = _uniq_strings(
            [primary_keyword] + secondary_keywords + [str(x) for x in target_keywords],
            limit=self._brief_config()["max_keywords"],
        )
        source_summary = _normalize_space(t.get("source_summary"))
        source_content = _normalize_space(t.get("source_content") or source_summary)
        content_angle = str(t.get("content_angle") or "general").strip() or "general"
        scoring = t.get("article_score") if isinstance(t.get("article_score"), dict) else {}
        quality = t.get("quality_score") if isinstance(t.get("quality_score"), dict) else {}
        quality_payload = t.get("quality_payload") if isinstance(t.get("quality_payload"), dict) else {}
        overall_score = _score_value(
            t.get("article_overall_score")
            if t.get("article_overall_score") is not None
            else t.get("overall_score")
            if t.get("overall_score") is not None
            else scoring.get("overall_score")
        )
        title_score = _score_value(
            t.get("article_title_style_score")
            if t.get("article_title_style_score") is not None
            else t.get("title_style_score")
            if t.get("title_style_score") is not None
            else scoring.get("title_style_score")
        )
        raw_word_count = (
            t.get("article_word_count")
            if t.get("article_word_count") is not None
            else t.get("word_count")
            if t.get("word_count") is not None
            else scoring.get("word_count")
        )
        is_notice = _bool_value(
            t.get("article_is_notice")
            if t.get("article_is_notice") is not None
            else t.get("is_notice")
            if t.get("is_notice") is not None
            else scoring.get("is_notice")
        )
        try:
            article_word_count = int(raw_word_count) if raw_word_count is not None else _word_count(source_content)
        except Exception:
            article_word_count = _word_count(source_content)

        return {
            "workflow_route": str(t.get("workflow_route") or "").strip(),
            "route_tier": str(t.get("route_tier") or "").strip(),
            "rewrite_required": bool(t.get("rewrite_required", False)),
            "publish_candidate": bool(t.get("publish_candidate", False)),
            "topic_id": str(t.get("topic_id") or t.get("id") or "").strip(),
            "candidate_id": t.get("candidate_id"),
            "title": _normalize_space(t.get("title")),
            "primary_keyword": primary_keyword,
            "secondary_keywords": secondary_keywords,
            "target_keywords": target_keywords,
            "search_intent": _normalize_space(t.get("search_intent") or "informational"),
            "content_type": _normalize_space(t.get("content_type") or "guide"),
            "content_angle": content_angle,
            "content_angle_label": self._angle_label(content_angle),
            "source_title": _normalize_space(t.get("source_title") or t.get("title")),
            "source_summary": source_summary,
            "source_url": _normalize_space(t.get("source_url")),
            "source_content": source_content,
            "material_score": t.get("material_score"),
            "article_overall_score": overall_score,
            "article_title_style_score": title_score,
            "word_count_score": _score_value(
                (quality.get("dimensions") or {}).get("word_count_score")
                if isinstance(quality.get("dimensions"), dict)
                else None
            )
            or _score_value(
                (quality_payload.get("dimensions") or {}).get("word_count_score")
                if isinstance(quality_payload.get("dimensions"), dict)
                else None
            )
            or _score_value(
                t.get("word_count_score")
                if t.get("word_count_score") is not None
                else scoring.get("word_count_score")
            ),
            "article_is_notice": is_notice,
            "article_word_count": article_word_count,
            "quality_score": _score_value(
                t.get("article_quality_score")
                if t.get("article_quality_score") is not None
                else t.get("quality_overall_score")
                if t.get("quality_overall_score") is not None
                else quality.get("quality_score")
                if quality.get("quality_score") is not None
                else quality_payload.get("quality_score")
            ),
            "quality_dimensions": (
                t.get("quality_dimensions")
                if isinstance(t.get("quality_dimensions"), dict)
                else quality.get("dimensions")
                if isinstance(quality.get("dimensions"), dict)
                else quality_payload.get("dimensions")
                if isinstance(quality_payload.get("dimensions"), dict)
                else {}
            ),
            "quality_rewrite_feedback_prompt": _normalize_space(
                t.get("rewrite_feedback_prompt")
                or t.get("quality_rewrite_feedback_prompt")
                or quality.get("rewrite_feedback_prompt")
                or quality_payload.get("rewrite_feedback_prompt")
            ),
            "evaluation": t.get("evaluation") if isinstance(t.get("evaluation"), dict) else {},
            "dedup": t.get("dedup") if isinstance(t.get("dedup"), dict) else {},
            "routing_payload": t.get("routing_payload") if isinstance(t.get("routing_payload"), dict) else {},
        }

    def _pick_source_highlights(self, normalized: Dict[str, Any]) -> List[str]:
        cfg = self._brief_config()
        summary_sents = _split_sentences(normalized.get("source_summary"))
        content_sents = _split_sentences(normalized.get("source_content"))
        title = _normalize_space(normalized.get("source_title"))
        candidates = [title] if title else []
        candidates.extend(summary_sents)
        candidates.extend(content_sents[: cfg["max_highlights"] * 2])
        return _uniq_strings(candidates, limit=cfg["max_highlights"])

    def _extract_key_facts(self, normalized: Dict[str, Any], highlights: List[str]) -> List[Dict[str, Any]]:
        cfg = self._brief_config()
        facts: List[Dict[str, Any]] = []
        fact_candidates = _split_sentences(normalized.get("source_summary")) + _split_sentences(normalized.get("source_content"))
        for sentence in fact_candidates:
            evidence_type = None
            if re.search(r"\d", sentence):
                evidence_type = "numeric"
            elif any(token in sentence for token in ["包括", "需要", "适合", "流程", "步骤", "区别", "建议", "要求"]):
                evidence_type = "statement"
            if not evidence_type:
                continue
            facts.append(
                {
                    "fact": sentence,
                    "evidence_type": evidence_type,
                    "source": "source_content",
                }
            )
            if len(facts) >= cfg["max_facts"]:
                break
        if not facts:
            for sentence in highlights[: cfg["max_facts"]]:
                facts.append({"fact": sentence, "evidence_type": "summary", "source": "source_summary"})
        return facts

    def _extract_risk_points(self, normalized: Dict[str, Any]) -> List[str]:
        cfg = self._brief_config()
        risks: List[str] = []
        if not normalized.get("source_url"):
            risks.append("原素材缺少 source_url，Writer 需避免把来源描述成外部权威结论。")
        if len(str(normalized.get("source_content") or "")) < 80:
            risks.append("原素材正文较短，Writer 应避免扩写为未经证据支持的细节。")
        if not normalized.get("source_summary"):
            risks.append("原素材缺少摘要，优先依据标题和正文提炼结构，不要臆造背景。")

        evaluation = normalized.get("evaluation") or {}
        if isinstance(evaluation, dict):
            if evaluation.get("has_risk"):
                risks.append("Crawler 评估命中风险标记，Writer 应弱化未经证实的绝对化表述。")
            if evaluation.get("source_ok") is False:
                risks.append("Crawler 评估提示来源质量一般，Writer 应保守引用并避免权威背书措辞。")

        dedup = normalized.get("dedup") or {}
        if isinstance(dedup, dict):
            score = dedup.get("similarity_score")
            try:
                if score is not None and float(score) >= 0.7:
                    risks.append("原素材与已发布内容相似度偏高，Writer 需明显重组结构与表达。")
            except Exception:
                pass

        return _uniq_strings(risks, limit=cfg["max_risk_points"])

    def _rewrite_constraints(self, normalized: Dict[str, Any], risk_points: List[str]) -> List[str]:
        constraints = [
            "只基于提供的源素材提炼事实，不补造数据、案例或结论。",
            "保留主关键词与搜索意图，文章目标是改写而非改题。",
            "优先复用 Research 提炼出的关键信息，避免照抄原句。",
            "若证据不足，明确写成建议或判断依据，不写成确定事实。",
        ]
        if risk_points:
            constraints.append("优先处理 risk_points 中列出的证据和相似度风险。")
        if normalized.get("quality_rewrite_feedback_prompt"):
            constraints.append("必须优先处理 QualityAgent 的扣分反馈，尤其是低分维度对应的问题。")
        return constraints

    def _outline_templates(self) -> List[Dict[str, Any]]:
        configured = (self.config or {}).get("outline_templates") if isinstance(self.config, dict) else None
        if isinstance(configured, list) and len(configured) >= 5:
            return [self._template_with_default_variants(x) for x in configured if isinstance(x, dict)]
        return [
            {
                "id": "news_explainer",
                "name": "新闻解读型",
                "sections": ["事件概述", "为什么重要", "关键影响", "后续关注"],
                "notes": "适合政策、院校动态、项目变化等新闻素材。",
                "match": {"content_types": ["news"], "keywords": ["政策", "动态", "变化", "启动", "发布"]},
            },
            {
                "id": "story_profile",
                "name": "人物故事型",
                "sections": ["人物/团队背景", "关键转折", "经历与方法", "启发与价值"],
                "notes": "适合教授心路历程、校友成长、科研故事等叙事素材。",
                "match": {"content_types": ["case_study"], "keywords": ["教授", "学生", "校友", "心路", "成长", "故事", "经历", "坚持"]},
            },
            {
                "id": "research_breakthrough",
                "name": "科研突破型",
                "sections": ["研究背景", "突破点", "应用价值", "未来方向"],
                "notes": "适合论文发表、科研成果、技术突破等素材。",
                "match": {"keywords": ["研究", "突破", "研发", "期刊", "论文", "团队", "技术", "科学"]},
            },
            {
                "id": "award_profile",
                "name": "荣誉奖项型",
                "sections": ["获奖信息", "获奖原因", "代表成果", "人物/团队启发"],
                "notes": "适合教授、学生、团队获奖和荣誉类素材。",
                "match": {"keywords": ["获奖", "荣获", "荣膺", "奖", "奖项", "表彰", "唯一获选"]},
            },
            {
                "id": "practical_guide",
                "name": "实用指南型",
                "sections": ["适用对象", "核心信息", "操作步骤", "注意事项"],
                "notes": "适合招生、申请、流程、条件类素材。",
                "match": {"content_types": ["guide", "how_to"], "angles": ["conditions", "process"], "keywords": ["申请", "报名", "流程", "条件", "材料", "时间线"]},
            },
            {
                "id": "admissions_update",
                "name": "招生信息型",
                "sections": ["招生变化", "适合人群", "关键要求", "申请建议"],
                "notes": "适合招生简章、项目调整、报名条件变化等素材。",
                "match": {"keywords": ["招生", "报考", "录取", "复试", "调剂", "学费", "项目"]},
            },
            {
                "id": "analysis_framework",
                "name": "分析框架型",
                "sections": ["背景问题", "核心变量", "对比分析", "判断建议"],
                "notes": "适合趋势、项目价值、择校比较等分析素材。",
                "match": {"content_types": ["comparison", "opinion"], "angles": ["comparison", "roi", "value", "fit"], "keywords": ["趋势", "价值", "对比", "选择", "影响", "为什么"]},
            },
            {
                "id": "case_breakdown",
                "name": "案例拆解型",
                "sections": ["案例背景", "做法拆解", "结果与变化", "可借鉴点"],
                "notes": "适合项目实践、学校案例、组织行动类素材。",
                "match": {"content_types": ["case_study"], "keywords": ["案例", "实践", "行动", "落地", "项目", "团队", "过程"]},
            },
            {
                "id": "partnership_collaboration",
                "name": "合作项目型",
                "sections": ["合作背景", "参与方与资源", "合作内容", "潜在影响"],
                "notes": "适合校企合作、国际合作、联合实验室、平台建设等素材。",
                "match": {"keywords": ["合作", "签署", "联合", "联盟", "平台", "伙伴", "中心", "共建"]},
            },
            {
                "id": "event_recap",
                "name": "活动复盘型",
                "sections": ["活动背景", "现场亮点", "参与反馈", "延伸价值"],
                "notes": "适合论坛、讲座、开放日、校园活动等素材。",
                "match": {"keywords": ["活动", "论坛", "讲座", "开放日", "现场", "参与者", "课程", "研讨"]},
            },
            {
                "id": "social_impact",
                "name": "社会影响型",
                "sections": ["问题背景", "行动方案", "影响人群", "长期价值"],
                "notes": "适合医疗、公益、可持续发展、社会服务等素材。",
                "match": {"keywords": ["医疗", "公益", "社会", "健康", "可持续", "气候", "服务", "影响"]},
            },
        ]

    def _default_template_variants(self) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "news_explainer": [
                {"id": "what_changed", "name": "变化解读", "keywords": ["变化", "调整", "发布", "启动"], "sections": ["发生了什么", "变化在哪里", "影响谁", "下一步看什么"]},
                {"id": "impact_first", "name": "影响优先", "keywords": ["影响", "重要", "政策"], "sections": ["核心结论", "直接影响", "间接影响", "行动建议"]},
                {"id": "background_context", "name": "背景补充", "keywords": ["背景", "原因", "趋势"], "sections": ["新闻背景", "事件经过", "深层原因", "后续意义"]},
            ],
            "story_profile": [
                {"id": "journey_turning_point", "name": "人物历程", "keywords": ["心路", "经历", "坚持", "转折"], "sections": ["人物起点", "关键转折", "方法与坚持", "故事启发"]},
                {"id": "team_story", "name": "团队同行", "keywords": ["团队", "并肩", "合作", "认可"], "sections": ["团队背景", "共同挑战", "协作方法", "成果意义"]},
                {"id": "quote_led_story", "name": "金句引入", "keywords": ["他说", "表示", "感恩", "谦卑"], "sections": ["一句话切入", "人物选择", "成果背后", "留给读者的启发"]},
            ],
            "research_breakthrough": [
                {"id": "problem_solution", "name": "问题解决", "keywords": ["挑战", "解决", "瓶颈", "突破"], "sections": ["原有难题", "研究方案", "突破价值", "应用前景"]},
                {"id": "paper_result", "name": "论文成果", "keywords": ["论文", "期刊", "发表", "nature", "science"], "sections": ["研究发表", "核心发现", "验证方式", "学术意义"]},
                {"id": "tech_application", "name": "技术应用", "keywords": ["技术", "应用", "系统", "装置"], "sections": ["技术背景", "创新点", "应用场景", "未来方向"]},
            ],
            "award_profile": [
                {"id": "award_reason", "name": "获奖原因", "keywords": ["获奖", "荣获", "表彰"], "sections": ["奖项信息", "为什么获奖", "代表成果", "未来期待"]},
                {"id": "achievement_timeline", "name": "成果时间线", "keywords": ["2010", "三年后", "十年前", "多年"], "sections": ["获奖节点", "关键年份", "成果累积", "长期价值"]},
                {"id": "honor_to_story", "name": "荣誉到人物", "keywords": ["教授", "学者", "团队"], "sections": ["荣誉落点", "人物底色", "研究贡献", "精神启发"]},
            ],
            "practical_guide": [
                {"id": "condition_checklist", "name": "条件清单", "keywords": ["条件", "资格", "适合", "要求"], "sections": ["适合谁", "核心条件", "自查清单", "准备建议"]},
                {"id": "process_timeline", "name": "流程时间线", "keywords": ["流程", "时间", "步骤", "报名", "复试"], "sections": ["流程总览", "关键节点", "材料与动作", "常见误区"]},
                {"id": "materials_preparation", "name": "材料准备", "keywords": ["材料", "申请", "提交", "证明"], "sections": ["需要准备什么", "材料怎么组织", "提交前检查", "风险提醒"]},
            ],
            "admissions_update": [
                {"id": "policy_change", "name": "招生变化", "keywords": ["调整", "变化", "新增", "取消"], "sections": ["变化摘要", "涉及人群", "关键要求", "应对建议"]},
                {"id": "application_window", "name": "报名窗口", "keywords": ["报名", "时间", "入口", "截止"], "sections": ["时间安排", "报名入口", "材料要求", "提醒事项"]},
                {"id": "admission_result", "name": "录取结果", "keywords": ["录取", "名单", "复试", "调剂"], "sections": ["结果信息", "后续动作", "注意事项", "备选方案"]},
            ],
            "analysis_framework": [
                {"id": "compare_options", "name": "选项对比", "keywords": ["对比", "区别", "怎么选"], "sections": ["对比对象", "关键维度", "适用场景", "选择建议"]},
                {"id": "value_roi", "name": "价值判断", "keywords": ["价值", "回报", "投入", "ROI"], "sections": ["投入是什么", "价值在哪里", "适合谁", "决策边界"]},
                {"id": "trend_reading", "name": "趋势判断", "keywords": ["趋势", "未来", "变化", "影响"], "sections": ["趋势信号", "背后原因", "可能影响", "行动建议"]},
            ],
            "case_breakdown": [
                {"id": "action_review", "name": "行动复盘", "keywords": ["行动", "过程", "落地"], "sections": ["案例背景", "关键动作", "结果反馈", "可复制经验"]},
                {"id": "challenge_response", "name": "挑战应对", "keywords": ["挑战", "困难", "问题"], "sections": ["遇到什么问题", "如何应对", "产生什么变化", "经验总结"]},
                {"id": "before_after", "name": "前后对照", "keywords": ["变化", "提升", "改善"], "sections": ["之前状态", "采取措施", "之后变化", "借鉴意义"]},
            ],
            "partnership_collaboration": [
                {"id": "resource_map", "name": "资源地图", "keywords": ["资源", "平台", "中心"], "sections": ["合作背景", "各方资源", "合作机制", "未来可能"]},
                {"id": "project_launch", "name": "项目启动", "keywords": ["启动", "签署", "成立"], "sections": ["项目缘起", "合作内容", "落地路径", "影响展望"]},
                {"id": "ecosystem_building", "name": "生态共建", "keywords": ["生态", "伙伴", "联盟", "共建"], "sections": ["生态目标", "参与角色", "协作方式", "长期价值"]},
            ],
            "event_recap": [
                {"id": "highlight_recap", "name": "亮点复盘", "keywords": ["亮点", "现场", "参与"], "sections": ["活动概览", "现场亮点", "参与反馈", "延伸价值"]},
                {"id": "forum_insight", "name": "观点提炼", "keywords": ["论坛", "研讨", "观点"], "sections": ["论坛主题", "核心观点", "行业启发", "后续关注"]},
                {"id": "campus_activity", "name": "校园活动", "keywords": ["校园", "同乐", "开放日"], "sections": ["活动背景", "体验内容", "参与人群", "校园价值"]},
            ],
            "social_impact": [
                {"id": "problem_action_impact", "name": "问题行动影响", "keywords": ["问题", "行动", "影响"], "sections": ["现实问题", "行动方案", "影响人群", "长期价值"]},
                {"id": "health_public_value", "name": "健康公共价值", "keywords": ["医疗", "健康", "复康"], "sections": ["健康痛点", "技术/服务方案", "受益对象", "推广价值"]},
                {"id": "sustainability_path", "name": "可持续路径", "keywords": ["可持续", "气候", "低碳"], "sections": ["议题背景", "解决路径", "社会影响", "未来挑战"]},
            ],
        }

    def _template_with_default_variants(self, template: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(template)
        if not isinstance(out.get("variants"), list) or not out.get("variants"):
            out["variants"] = self._default_template_variants().get(str(out.get("id") or ""), [])
        return out

    def _template_match_text(self, normalized: Dict[str, Any]) -> str:
        parts = [
            normalized.get("title"),
            normalized.get("source_title"),
            normalized.get("source_summary"),
            normalized.get("source_content"),
            normalized.get("primary_keyword"),
            " ".join(normalized.get("secondary_keywords") or []),
            " ".join(normalized.get("target_keywords") or []),
        ]
        return " ".join(str(x or "") for x in parts)

    def _template_score(self, template: Dict[str, Any], normalized: Dict[str, Any]) -> int:
        match = template.get("match") if isinstance(template.get("match"), dict) else {}
        text = self._template_match_text(normalized).lower()
        content_type = str(normalized.get("content_type") or "").lower()
        angle = str(normalized.get("content_angle") or "").lower()

        score = 0
        for item in match.get("content_types") or []:
            if str(item or "").lower() == content_type:
                score += 5
        for item in match.get("angles") or []:
            if str(item or "").lower() == angle:
                score += 4
        for item in match.get("keywords") or []:
            keyword = str(item or "").strip().lower()
            if keyword and keyword in text:
                score += 2

        # 故事/人物稿更依赖“人 + 经历/转折/情绪价值”的组合，避免被普通科研词抢走。
        if template.get("id") == "story_profile":
            has_person = any(x in text for x in ["教授", "学生", "校友", "团队", "人物"])
            has_story = any(x in text for x in ["心路", "经历", "成长", "坚持", "转折", "故事", "感恩", "谦卑"])
            if has_person and has_story:
                score += 8

        if template.get("id") == "award_profile":
            has_award = any(x in text for x in ["获奖", "荣获", "荣膺", "奖项", "表彰"])
            has_person = any(x in text for x in ["教授", "学生", "学者", "团队"])
            if has_award and has_person:
                score += 6

        return score

    def _variant_score(self, variant: Dict[str, Any], normalized: Dict[str, Any]) -> int:
        text = self._template_match_text(normalized).lower()
        score = 0
        for item in variant.get("keywords") or []:
            keyword = str(item or "").strip().lower()
            if keyword and keyword in text:
                score += 2
        return score

    def _stable_variant_index(self, normalized: Dict[str, Any], count: int) -> int:
        if count <= 1:
            return 0
        seed = "|".join(
            [
                str(normalized.get("candidate_id") or ""),
                str(normalized.get("topic_id") or ""),
                str(normalized.get("title") or ""),
                str(normalized.get("source_title") or ""),
                str(normalized.get("primary_keyword") or ""),
            ]
        )
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % count

    def _select_template_variant(self, template: Dict[str, Any], normalized: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        variants = template.get("variants")
        if not isinstance(variants, list) or not variants:
            return None
        valid = [v for v in variants if isinstance(v, dict)]
        if not valid:
            return None
        ranked = [
            (self._variant_score(variant, normalized), index, variant)
            for index, variant in enumerate(valid)
        ]
        ranked.sort(key=lambda item: (-item[0], item[1]))
        best_score = ranked[0][0]
        # 在最相关的一组 variants 中做稳定轮换，避免同类型文章都套同一种结构。
        # 如果全部没有命中关键词，则在该模板所有 variants 中轮换。
        if best_score <= 0:
            candidates = [item[2] for item in ranked]
        else:
            candidates = [item[2] for item in ranked if item[0] >= best_score - 2]
            if len(candidates) < 2 and len(ranked) >= 2:
                candidates = [item[2] for item in ranked[: min(3, len(ranked))]]
        idx = self._stable_variant_index(normalized, len(candidates))
        return dict(candidates[idx])

    def _select_outline_template(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        templates = self._outline_templates()
        if not templates:
            return {}
        ranked = [
            (self._template_score(template, normalized), index, template)
            for index, template in enumerate(templates)
        ]
        ranked.sort(key=lambda item: (-item[0], item[1]))
        best_score, _, best_template = ranked[0]
        if best_score <= 0:
            content_type = str(normalized.get("content_type") or "").strip()
            fallback_by_type = {
                "case_study": "case_breakdown",
                "comparison": "analysis_framework",
                "opinion": "analysis_framework",
                "guide": "practical_guide",
                "how_to": "practical_guide",
                "news": "news_explainer",
            }
            fallback_id = fallback_by_type.get(content_type)
            for template in templates:
                if template.get("id") == fallback_id:
                    selected = dict(template)
                    break
            else:
                selected = dict(best_template)
        else:
            selected = dict(best_template)

        variant = self._select_template_variant(selected, normalized)
        if variant:
            selected["variant_id"] = variant.get("id")
            selected["variant_name"] = variant.get("name")
            selected["variant_notes"] = variant.get("notes") or ""
            if isinstance(variant.get("sections"), list) and variant.get("sections"):
                selected["sections"] = [str(x) for x in variant.get("sections") if str(x).strip()]
        return selected

    def _word_count_instruction(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self._writing_config()
        overall = normalized.get("article_overall_score")
        word_count = int(normalized.get("article_word_count") or 0)
        word_count_score = normalized.get("word_count_score")
        is_notice = normalized.get("article_is_notice") is True
        if is_notice:
            min_words = int(cfg["notice_min_words"])
            max_words = int(cfg["notice_max_words"])
            target_word_count = int(cfg["notice_target_word_count"])
        else:
            min_words = int(cfg["standard_min_words"])
            max_words = int(cfg["standard_max_words"])
            target_word_count = int(cfg["target_word_count"])
        score_in_rewrite_range = (
            overall is not None
            and float(cfg["high_score_min"]) <= float(overall) <= float(cfg["high_score_max"])
        )
        out_of_range = word_count > 0 and not (min_words <= word_count <= max_words)
        word_count_score_low = (
            word_count_score is not None
            and float(word_count_score) < float(cfg["word_count_score_low_threshold"])
        )
        should_adjust = (
            score_in_rewrite_range
            and (out_of_range or word_count_score_low)
        )
        if should_adjust:
            if out_of_range:
                direction = "扩写" if word_count < min_words else "压缩"
            elif word_count and word_count < target_word_count:
                direction = "扩写"
            elif word_count and word_count > target_word_count:
                direction = "压缩"
            else:
                direction = "重做篇幅规划"
            reason_parts = []
            if out_of_range:
                reason_parts.append(f"原文字数约 {word_count}，不在 {min_words}-{max_words} 字标准范围内")
            if word_count_score_low:
                reason_parts.append(
                    f"字数分为 {round(float(word_count_score), 2)}，低于 {int(cfg['word_count_score_low_threshold'])}"
                )
            reason = "；".join(reason_parts)
            instruction = (
                f"原文评分为 {round(float(overall), 2)}，属于可改写高价值素材；"
                f"{reason}。"
                f"写作时必须重新生成字数要求：将成稿{direction}到约 {target_word_count} 字，"
                f"最终控制在 {min_words}-{max_words} 字。"
                "如果需要扩写，只能补充原文已有事实的背景解释、影响分析和读者关心的问题；"
                "如果需要压缩，优先删掉重复背景和低信息密度段落，不能删掉关键事实。"
            )
            action = direction
        else:
            if is_notice:
                instruction = (
                    f"这篇文章属于通知/公告类，不强制写成长文；目标成稿建议 {min_words}-{max_words} 字，"
                    f"约 {target_word_count} 字即可，重点写清楚时间、对象、要求、变化和行动提示。"
                )
            else:
                instruction = (
                    f"非通知类文章目标成稿至少 {min_words} 字，建议写到 {target_word_count} 字左右；"
                    f"最终控制在 {min_words}-{max_words} 字，优先保持信息完整与可读性。"
                )
            action = "keep"
        return {
            "standard_min_words": min_words,
            "standard_max_words": max_words,
            "target_word_count": target_word_count,
            "source_word_count": word_count,
            "word_count_score": word_count_score,
            "word_count_score_low_threshold": int(cfg["word_count_score_low_threshold"]),
            "is_notice": bool(is_notice),
            "should_adjust_word_count": bool(should_adjust),
            "action": action,
            "instruction": instruction,
        }

    def _title_instruction(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        threshold = float(self._writing_config()["title_major_rewrite_threshold"])
        title_score = normalized.get("article_title_style_score")
        if title_score is not None and float(title_score) < threshold:
            mode = "major_rewrite"
            instruction = (
                f"标题分为 {round(float(title_score), 2)}，低于 {int(threshold)}。"
                "请重新生成标题：至少先构思 3 个新标题方向，再选择最适合发布的一个作为 article.title。"
                "新标题必须保留事实核心，但要重新设计信息点、看点和表达方式，不能照搬原题或只替换几个词。"
            )
        else:
            mode = "minor_rewrite"
            if title_score is None:
                instruction = "未提供标题分。请小幅优化标题，保持事实核心，增强清晰度和传播性。"
            else:
                instruction = (
                    f"标题分为 {round(float(title_score), 2)}。"
                    "请小幅优化标题：保留原标题主体，只调整措辞、清晰度和吸引力。"
                )
        return {
            "title_score": title_score,
            "rewrite_mode": mode,
            "instruction": instruction,
        }

    def _style_instruction(self) -> Dict[str, Any]:
        return {
            "style_id": "human_editorial_feature",
            "style_name": "编辑改写稿",
            "goal": "写成一篇自然的编辑改写稿。不要像是在逐条执行提示词，也不要把文章修得过分工整。",
            "rules": [
                "大纲只是材料整理顺序，不必逐段照搬；可以合并、调序或弱化某些小节，让文章读起来像自然写成的稿子。",
                "不要刻意制造“长段+单句短段”的节奏。短句可以用，但不要规律性地用来制造特写感。",
                "不要为了避开套话而显得过度克制。必要时可以使用普通连接词，但避免连续出现宣传腔。",
                "保留少量自然的编辑判断，例如某个事实先讲、某个背景后补；不要把所有信息都解释得刚刚好。",
                "技术内容解释到读者大致明白即可，允许略有留白，不要写成百科条目。",
                "结尾可以收在一个事实、一个未完成的问题或人物下一步方向上，不必升华。",
                "模拟资深主笔的非对称写作思维：选 1-2 个核心细节多写几笔，次要信息快速带过，不要平均分配篇幅。",
                "拆掉过于工整的对仗句。少用“不是……而是……”“一方面……另一方面……”“对于……对于……”这类平衡结构。",
                "尽量使用隐性逻辑过渡。两句话本来连得上时，不必加“然而”“在这个过程中”“事实上”“因此”。",
                "保留一点颗粒感：允许某个事实略微突出来，允许段落之间有轻微跳跃，不要把文章打磨成完全平滑的说明文。",
                "不要按“人物引入→获奖→领域介绍→成果1→成果2→方法论→AI话题→未来方向→哲理结尾”的标准人物稿模板顺序推进。可以从成果、方法、一句原文引语或某个具体难题切入。",
                "少写路标句。不要频繁用“这种认识也贯穿……”“这样的工作往往……”“这一特点在……体现得尤为明显”“类似的故事后来再次出现”等句子给读者指路。",
                "如果原文没有采访现场、观察细节或作者亲历，不要伪造；但可以用材料中的具体名词、年份、动作制造真实颗粒，例如“2010年的预测”“三年后验证”“Wilson Loop”“筛选数千种材料”。",
                "少解释学科，多写人；少总结规律，多写过程；少用标准转场，多用具体细节。",
                "遇到科研概念时，优先从人物视角进入，例如“对某某来说，真正有意思的是……”；不要先写一段教科书式定义。",
                "把概括句换成具体时间、动作或等待过程。例如不要只写“实验结果终于出现”，要写“从理论论文发表到实验验证，中间隔着三年的等待”。",
                "允许信息密度不均衡：某个关键细节可以多写，枯燥背景可以一笔带过，不要让每段都像 100-150 字的均匀模块。",
            ],
            "avoid_patterns": [
                "整篇文章完全贴合大纲顺序，像把提纲逐条扩写。",
                "频繁使用单句成段，形成过于规律的呼吸感。",
                "每段都很均衡，每个事实都被解释得过满。",
                "完美避开所有套话，反而显得像在执行禁用词清单。",
                "结尾强行总结价值或喊口号。",
                "大面积使用对称句式、排比句或“对于A/对于B”的工整比较。",
                "把所有大纲点都写成差不多长的段落，像填空题。",
                "使用“地图、灯塔、航程、星辰大海、打开大门、拓宽边界”等大模型常见收尾意象来完成情感升华。",
                "连续使用路标句，把每个段落之间的逻辑关系都说得过于清楚。",
                "把科研人物稿写成固定模板：先身份、再奖项、再研究领域、再成果、再方法、最后未来。",
                "大量使用“在这一领域”“不过”“为此”“近年来”“在他看来”等标准转场搭骨架。",
                "用科普教科书口吻解释概念，而不是把概念放回人物的研究选择和具体过程里。",
            ],
            "soft_avoid_phrases": [
                "不仅……更……",
                "值得一提的是",
                "可以说",
                "这意味着",
                "然而",
                "尽管如此",
                "在这个过程中",
                "在这一背景下",
                "事实上",
                "简单来说",
                "过去……如今……",
                "这种认识也贯穿",
                "这样的工作往往",
                "这一特点在",
                "类似的故事后来再次出现",
                "人工智能的出现，则为",
                "在这一领域",
                "不过",
                "为此",
                "近年来",
                "在他看来",
                "地图之外",
                "打开大门",
                "拓宽边界",
                "灯塔",
                "星辰大海",
                "充分体现了",
                "具有重要意义",
                "注入新动能",
                "开启新篇章",
                "赋能",
                "助力",
                "彰显",
                "进一步推动",
                "未来可期",
                "奠定坚实基础",
            ],
        }

    def _writer_outline(self, normalized: Dict[str, Any], highlights: List[str], facts: List[Dict[str, Any]], template: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = self._brief_config()
        angle = str(normalized.get("content_angle") or "general")
        title = str(normalized.get("title") or normalized.get("source_title") or "改写主题")
        fact_texts = [str(item.get("fact") or "") for item in facts if isinstance(item, dict)]
        anchor_points = _uniq_strings(highlights + fact_texts, limit=6)

        def _points(start: int, end: int) -> List[str]:
            selected = anchor_points[start:end]
            if selected:
                return selected
            return [str(normalized.get("source_summary") or normalized.get("source_title") or title)]

        section_titles = {
            "conditions": ["适合人群与适用前提", "核心条件拆解", "准备建议与常见误区"],
            "process": ["流程全览", "关键步骤与材料", "执行建议与风险提示"],
            "school_selection": ["选择维度", "关键对比点", "决策建议"],
            "comparison": ["核心差异", "适用场景对比", "选择建议"],
            "roi": ["投入成本", "潜在收益", "决策边界与建议"],
            "value": ["核心价值", "适用场景", "落地建议"],
            "fit": ["适合谁", "不适合谁", "决策建议"],
            "general": ["背景与问题", "关键内容拆解", "落地建议"],
        }
        if template and isinstance(template.get("sections"), list):
            titles = [str(x) for x in template.get("sections") if str(x).strip()]
        else:
            titles = section_titles.get(angle, section_titles["general"])
        titles = titles[: cfg["max_outline_sections"]]
        sections: List[Dict[str, Any]] = []
        for idx, section_title in enumerate(titles):
            start = idx * 2
            sections.append(
                {
                    "title": section_title,
                    "key_points": _points(start, start + 2),
                    "writing_tips": [
                        "说明这一部分要写什么，不要直接复制原文。",
                        "优先使用 source_highlights 和 key_facts 中的事实。",
                        "如果证据不足，用保守表述，不扩写未证实细节。",
                    ],
                    "notes": "template",
                }
            )
        return {
            "title": title,
            "template_id": (template or {}).get("id"),
            "template_name": (template or {}).get("name"),
            "template_notes": (template or {}).get("notes"),
            "variant_id": (template or {}).get("variant_id"),
            "variant_name": (template or {}).get("variant_name"),
            "variant_notes": (template or {}).get("variant_notes"),
            "sections": sections,
        }

    def _writer_prompt_package(self, research_brief: Dict[str, Any]) -> Dict[str, Any]:
        outline = research_brief.get("writer_outline") if isinstance(research_brief.get("writer_outline"), dict) else {}
        style = research_brief.get("style_instruction") if isinstance(research_brief.get("style_instruction"), dict) else {}
        lines = [
            "你是 WriterAgent。请根据以下 Research Brief 写一篇适合发布的原创文章。",
            "",
            "## 核心要求",
            "- 只依据 brief 中的源素材、亮点和关键事实写作，不编造数据、人物、结论。",
            "- 遵守标题改写策略和字数策略。",
            "- writer_outline 是材料组织建议，不是必须逐段照抄的目录；允许合并、调序和自然过渡。",
            "- 字数策略是硬性验收要求：非通知类正文 content_md 必须控制在 900-1200 字，不能写成短讯；通知/公告类按字数策略保持简洁。",
            "- 遵守文风要求，避免写得像逐条执行提示词的 AI 稿。",
            "- 正文使用 Markdown。",
            "- 输出必须是 JSON，包含 article.title、article.meta_description、article.content_md。",
            "",
            "## 文风要求",
            str(style.get("goal") or ""),
        ]
        for rule in style.get("rules") or []:
            lines.append(f"- {rule}")
        if style.get("avoid_patterns"):
            lines.extend(
                [
                    "",
                    "## 尤其要避免",
                ]
            )
            for pattern in style.get("avoid_patterns") or []:
                lines.append(f"- {pattern}")
        if style.get("soft_avoid_phrases"):
            lines.extend(
                [
                    "",
                    "## 少用表达",
                    "这些词不是绝对禁止，但不要集中出现，也不要为了避开它们而写得过分刻意：",
                ]
            )
            for phrase in style.get("soft_avoid_phrases") or []:
                lines.append(f"- {phrase}")
        lines.extend(
            [
                "",
                "## 标题策略",
                str((research_brief.get("title_instruction") or {}).get("instruction") or ""),
                "如果标题策略是 major_rewrite，输出 JSON 中必须额外包含 article.title_options，列出 3 个备选标题，并将最佳标题写入 article.title。",
                "",
                "## 字数策略",
                str((research_brief.get("word_count_instruction") or {}).get("instruction") or ""),
                "如果 should_adjust_word_count 为 true，必须按字数策略重做篇幅，不要沿用原文字数结构。",
                "",
                "## QualityAgent 扣分反馈",
                str(research_brief.get("quality_rewrite_feedback_prompt") or "无"),
                "如果这里列出了扣分点，必须优先修复；如果与大纲冲突，以 QualityAgent 扣分反馈为准。",
                "",
                "## 大纲模板",
                f"{outline.get('template_name') or ''}（{outline.get('template_id') or ''}）",
                str(outline.get("template_notes") or ""),
                f"细分写法：{outline.get('variant_name') or ''}（{outline.get('variant_id') or ''}）",
                str(outline.get("variant_notes") or ""),
                "",
                "## 文章大纲",
            ]
        )
        for section in outline.get("sections") or []:
            if not isinstance(section, dict):
                continue
            lines.append(f"- {section.get('title') or ''}")
            for point in section.get("key_points") or []:
                lines.append(f"  - 内容点：{point}")
            for tip in section.get("writing_tips") or []:
                lines.append(f"  - 写作提示：{tip}")
        lines.extend(
            [
                "",
                "## Research Brief JSON",
                json.dumps(research_brief, ensure_ascii=False, indent=2),
            ]
        )
        return {
            "prompt_type": "writer_prompt_from_research_brief",
            "prompt_text": "\n".join(lines).strip(),
            "generated_at": datetime.now().isoformat(),
        }

    async def _call_llm(self, prompt: str) -> str:
        if self.llm is None:
            raise RuntimeError("research_llm_not_configured")
        messages = [
            SystemMessage(content="你是调研研究员。你必须输出纯 JSON，不要输出代码块或解释文字。"),
            HumanMessage(content=prompt),
        ]
        if hasattr(self.llm, "ainvoke"):
            resp = await self.llm.ainvoke(messages)
            return resp.content if hasattr(resp, "content") else str(resp)
        resp = await asyncio.to_thread(self.llm.invoke, messages)
        return resp.content if hasattr(resp, "content") else str(resp)

    def _outline_llm_prompt(
        self,
        normalized: Dict[str, Any],
        highlights: List[str],
        facts: List[Dict[str, Any]],
        risk_points: List[str],
        constraints: List[str],
        template: Dict[str, Any],
    ) -> str:
        return json.dumps(
            {
                "task": "阅读全文材料，为 WriterAgent 生成写作大纲。大纲必须拆分文章结构，说明每一部分写什么，并给出写作提示。",
                "selected_template": template,
                "title_instruction": self._title_instruction(normalized),
                "word_count_instruction": self._word_count_instruction(normalized),
                "source_article": {
                    "title": normalized.get("source_title") or normalized.get("title"),
                    "summary": normalized.get("source_summary"),
                    "content": _truncate_text(normalized.get("source_content"), self._brief_config()["max_source_chars"]),
                    "url": normalized.get("source_url"),
                },
                "scores": {
                    "overall_score": normalized.get("article_overall_score"),
                    "title_style_score": normalized.get("article_title_style_score"),
                    "word_count": normalized.get("article_word_count"),
                },
                "source_highlights": highlights,
                "key_facts": facts,
                "risk_points": risk_points,
                "constraints": constraints,
                "return_json_schema": {
                    "writer_outline": {
                        "title": "建议标题或原题",
                        "template_id": "模板id",
                        "template_name": "模板名称",
                        "template_notes": "模板说明",
                        "sections": [
                            {
                                "title": "章节标题",
                                "key_points": ["这一部分要写的信息点"],
                                "writing_tips": ["给 WriterAgent 的具体写作提示"],
                                "notes": "llm",
                            }
                        ],
                    }
                },
            },
            ensure_ascii=False,
        )

    async def _maybe_llm_writer_outline(
        self,
        *,
        normalized: Dict[str, Any],
        highlights: List[str],
        facts: List[Dict[str, Any]],
        risk_points: List[str],
        constraints: List[str],
        template: Dict[str, Any],
        mode: str,
    ) -> Optional[Dict[str, Any]]:
        if (mode or "").strip().lower() != "live":
            return None
        try:
            raw = await self._call_llm(
                self._outline_llm_prompt(
                    normalized=normalized,
                    highlights=highlights,
                    facts=facts,
                    risk_points=risk_points,
                    constraints=constraints,
                    template=template,
                )
            )
            payload = _extract_json(raw)
        except Exception:
            return None
        outline = payload.get("writer_outline") if isinstance(payload, dict) else None
        if not isinstance(outline, dict) or not isinstance(outline.get("sections"), list):
            return None
        outline.setdefault("template_id", template.get("id"))
        outline.setdefault("template_name", template.get("name"))
        outline.setdefault("template_notes", template.get("notes"))
        return outline

    async def _rewrite_branch_output(self, normalized: Dict[str, Any], mode: str = "mock") -> Dict[str, Any]:
        cfg = self._brief_config()
        highlights = self._pick_source_highlights(normalized)
        facts = self._extract_key_facts(normalized, highlights)
        risk_points = self._extract_risk_points(normalized)
        constraints = self._rewrite_constraints(normalized, risk_points)
        template = self._select_outline_template(normalized)
        writer_outline = await self._maybe_llm_writer_outline(
            normalized=normalized,
            highlights=highlights,
            facts=facts,
            risk_points=risk_points,
            constraints=constraints,
            template=template,
            mode=mode,
        )
        if not writer_outline:
            writer_outline = self._writer_outline(normalized, highlights, facts, template)
        title_instruction = self._title_instruction(normalized)
        word_count_instruction = self._word_count_instruction(normalized)
        now = datetime.now().isoformat()

        source_snapshot = {
            "source_title": normalized.get("source_title") or "",
            "source_summary": normalized.get("source_summary") or "",
            "source_url": normalized.get("source_url") or "",
            "source_content_excerpt": _truncate_text(normalized.get("source_content"), cfg["max_source_chars"]),
            "material_score": normalized.get("material_score"),
            "article_overall_score": normalized.get("article_overall_score"),
            "article_title_style_score": normalized.get("article_title_style_score"),
            "word_count_score": normalized.get("word_count_score"),
            "article_is_notice": normalized.get("article_is_notice"),
            "article_word_count": normalized.get("article_word_count"),
            "quality_score": normalized.get("quality_score"),
            "quality_dimensions": normalized.get("quality_dimensions") or {},
            "quality_rewrite_feedback_prompt": normalized.get("quality_rewrite_feedback_prompt") or "",
        }
        warnings = []
        if not normalized.get("primary_keyword"):
            warnings.append("missing_primary_keyword")
        if not normalized.get("source_content"):
            warnings.append("missing_source_content")
        if not normalized.get("source_url"):
            warnings.append("missing_source_url")

        research_brief = {
            "brief_type": "rewrite_candidate_research_brief",
            "workflow_route": normalized.get("workflow_route"),
            "route_tier": normalized.get("route_tier"),
            "topic_id": normalized.get("topic_id"),
            "candidate_id": normalized.get("candidate_id"),
            "title": normalized.get("title"),
            "primary_keyword": normalized.get("primary_keyword"),
            "secondary_keywords": normalized.get("secondary_keywords") or [],
            "target_keywords": normalized.get("target_keywords") or [],
            "search_intent": normalized.get("search_intent"),
            "content_type": normalized.get("content_type"),
            "content_angle": normalized.get("content_angle_label") or normalized.get("content_angle"),
            "source_snapshot": source_snapshot,
            "source_highlights": highlights,
            "key_facts": facts,
            "risk_points": risk_points,
            "rewrite_constraints": constraints,
            "quality_rewrite_feedback_prompt": normalized.get("quality_rewrite_feedback_prompt") or "",
            "title_instruction": title_instruction,
            "word_count_instruction": word_count_instruction,
            "style_instruction": self._style_instruction(),
            "writer_outline": writer_outline,
            "suggested_sections": [section.get("title") for section in writer_outline.get("sections") or []],
            "warnings": warnings,
            "generated_at": now,
        }
        research_brief["writer_prompt"] = self._writer_prompt_package(research_brief)

        source_title = str(normalized.get("source_title") or normalized.get("title") or "")
        source_url = str(normalized.get("source_url") or "")
        source_summary = str(normalized.get("source_summary") or "")
        citations = []
        if source_title or source_url:
            citations.append(
                {
                    "title": source_title,
                    "url": source_url,
                    "source": "crawler_candidate",
                    "authority": "medium" if source_url else "unknown",
                    "citation": source_title or source_url,
                    "note": "rewrite_candidate_source",
                }
            )

        statistics = []
        material_score = normalized.get("material_score")
        if material_score is not None:
            statistics.append(
                {
                    "metric": "material_score",
                    "value": material_score,
                    "unit": "",
                    "note": "crawler_evaluation",
                    "source": "crawler",
                }
            )

        result = {
            "research_brief": research_brief,
            "background": {
                "definition": (highlights[0] if highlights else source_title or normalized.get("title") or ""),
                "industry_context": source_summary,
                "common_pain_points": risk_points,
            },
            "statistics": statistics,
            "cases": [],
            "quotes": [],
            "sources": [
                {
                    "type": "crawler_candidate",
                    "title": source_title,
                    "url": source_url,
                    "authority": "medium" if source_url else "unknown",
                    "note": "rewrite_candidate_source",
                }
            ],
            "citations": citations,
            "outline": {"sections": writer_outline.get("sections") or []},
            "warnings": warnings,
            "is_mock": False,
            "data_confidence": "medium" if normalized.get("source_content") else "low",
            "generated_at": now,
        }
        normalized_result = normalize_research_result(result)
        normalized_result["research_brief"] = research_brief
        normalized_result["writer_prompt"] = research_brief["writer_prompt"]
        normalized_result["warnings"] = list(normalized_result.get("warnings") or []) + validate_research_result(normalized_result)
        return normalized_result

    async def execute(self, topic: Dict[str, Any], mode: str = "mock") -> Dict[str, Any]:
        topic = topic if isinstance(topic, dict) else {}
        # 从 original_url 抓取原文
        if not topic.get("source_content") and topic.get("original_url"):
            fetcher = URLContentFetcher()
            result = await fetcher.fetch(str(topic["original_url"]))
            if result.success and result.content:
                topic["source_content"] = result.content
        if self._is_rewrite_candidate_input(topic):
            normalized_topic = self._normalize_rewrite_topic(topic)
            return await self._rewrite_branch_output(normalized_topic, mode=mode)

        title, keywords = self._topic_keywords(topic)
        warnings: List[str] = []

        required = ["title", "primary_keyword", "content_type"]
        for k in required:
            if not str(topic.get(k) or "").strip():
                warnings.append(f"missing_topic_field:{k}")

        mode_val = (mode or "mock").strip().lower()
        is_mock = mode_val != "live"
        data_confidence = "low" if is_mock else "unknown"

        collected: Dict[str, Any] = {}
        if is_mock:
            collector = DataCollector(config=self.config)
            try:
                collected = await collector.collect(
                    topic=title or str(topic.get("title") or ""),
                    keywords=keywords,
                    sources=["official_statistics", "academic_papers", "expert_opinions"],
                )
            except Exception as e:
                warnings.append(f"collector_failed:{e}")
            finally:
                try:
                    await collector.close()
                except Exception:
                    pass

        base = self._mock_materials(topic, keywords)
        raw: Dict[str, Any] = {
            **base,
            "warnings": warnings + list((collected.get("warnings") or []) if isinstance(collected, dict) else []),
            "is_mock": is_mock,
            "data_confidence": data_confidence,
            "generated_at": datetime.now().isoformat(),
        }
        normalized = normalize_research_result(raw)
        normalized["warnings"] = list(normalized.get("warnings") or []) + validate_research_result(normalized)
        return normalized


def run_research_agent_sync(*, topic: Dict[str, Any], mode: str = "mock") -> Dict[str, Any]:
    return asyncio.run(ResearchAgent().execute(topic=topic, mode=mode))
