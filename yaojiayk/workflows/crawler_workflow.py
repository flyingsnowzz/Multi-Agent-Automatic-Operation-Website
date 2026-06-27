#!/usr/bin/env python3
"""
Crawler 摄取/清洗/分流工作流（独立流程）

为什么它看起来与 hybrid_workflow.py 差异很大：
- hybrid_workflow.py 的输入是“一个 topic”，目标是产出“从调研到发布”的整条内容生产链路；
  每一步都要生成较大段内容/结构化结果，因此每个阶段都用 CrewAI 来做 LLM 生成。
- crawler_workflow.py 的输入是“一批爬虫 item（待处理内容）”，目标是把它们做“清洗与路由”：
  去重、门禁评估、相关性判断、状态更新、分流（discard/pass_to_scoring）。
  这类工作更强调确定性与稳定性，所以这里的模式是：
  - LangGraph 负责批处理循环与状态机（逐条处理、可重复运行、可追踪）
  - 规则/工具负责去重与评估（便于解释与调参）
  - CrewAI 只在需要 LLM 的地方介入：历史兼容决策逻辑，当前 crawler 主链路不再依赖它

它如何“利用 agents/ 目录”：
- 读取 agents/crawler_processor_agent/config.yaml 作为阈值与状态字段配置
- 读取 agents/crawler_processor_agent/prompt.md 作为 crawler 门禁规则说明与历史兼容 helper 提示模板
- 调用 agents/crawler_processor_agent/tools/* 提供的读库/评估/去重/更新状态工具
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

try:
    from crewai import Agent, Crew, Process, Task
except Exception:
    Agent = Crew = Process = Task = None

try:
    from langgraph.graph import END, StateGraph
except Exception:
    END = None
    StateGraph = None

from agents.crawler_processor_agent.tools.content_evaluator import evaluate_content
from agents.crawler_processor_agent.tools.crawler_db_reader import (
    read_crawler_pending,
    update_crawler_status,
)
from agents.crawler_processor_agent.tools.dedup_checker import check_duplicate


class CrawlerIngestState(TypedDict):
    """
    LangGraph 状态机的 State（批处理流程）。

    与 hybrid_workflow 的差异点：
    - hybrid_workflow 把每个阶段产物挂到 state（research_result/write_result/...）
    - crawler_workflow 把“当前处理 item + 批处理累计统计”挂到 state（current_item/processed/counts）
    """
    cfg: Dict[str, Any]
    crawler_db_cfg: Dict[str, Any]
    dedup_cfg: Dict[str, Any]
    criteria_cfg: Dict[str, Any]
    execution_cfg: Dict[str, Any]
    llm_cfg: Dict[str, Any]
    config_dir: str
    prompt_template: str

    limit: int
    min_id: Optional[int]
    max_id: Optional[int]
    dry_run: bool
    target_keywords: List[str]
    published_articles: Optional[List[Dict[str, Any]]]

    pending_items: List[Dict[str, Any]]
    current_item: Optional[Dict[str, Any]]
    input_valid: bool

    dedup_result: Optional[Dict[str, Any]]
    eval_result: Optional[Dict[str, Any]]

    decision: Optional[str]
    status_to_update: Optional[str]
    decision_reason: Optional[str]
    reason_codes: List[str]
    next_agent: Optional[str]
    next_payload: Optional[Dict[str, Any]]

    processed: List[Dict[str, Any]]
    counts: Dict[str, int]
    error: Optional[str]


def _expand_env(value: Any) -> Any:
    """
    把 config.yaml 中 ${ENV_KEY} 的写法展开为环境变量值。

    用途：
    - config.yaml 里常用 ${CRAWLER_DB_USER} 这种形式存敏感信息引用
    - 代码运行时从 os.environ 注入，避免硬编码
    """
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1].strip()
            defaults = {
                "CRAWLER_DB_HOST": "localhost",
                "CRAWLER_DB_PORT": "3306",
                "CMS_DB_HOST": "localhost",
                "CMS_DB_PORT": "3306",
            }
            return os.getenv(env_key, defaults.get(env_key, ""))
        return value
    return value


def _load_crawler_processor_config(config_dir: str) -> Dict[str, Any]:
    """
    加载 agents/crawler_processor_agent/config.yaml 作为本工作流的运行配置。

    说明：
    - 这里的 cfg 主要用于阈值、状态字段、数据库连接信息等
    - LLM 相关配置也从 cfg["llm"] 读取（用于 CrewAI 决策阶段）
    """
    try:
        import yaml
    except Exception as e:
        raise RuntimeError("缺少依赖 PyYAML，无法读取 crawler_processor_agent/config.yaml") from e

    config_path = os.path.join(config_dir, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _expand_env(raw)


def _safe_json_loads(text: Any) -> Any:
    """
    把 LLM 输出尽量解析为 dict。

    设计目的：
    - CrewAI 任务约定输出 JSON 字符串，但实际有可能输出非 JSON
    - 这里做容错，保证不会因为解析失败而中断整个批处理流程
    """
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return {"raw": text}
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _apply_short_content_bonus(evaluation: Dict[str, Any], criteria: Dict[str, Any]) -> Dict[str, Any]:
    """
    对短内容做轻微质量补偿（避免新闻类短内容全部被低估）。

    依据：agents/crawler_processor_agent/config.yaml -> evaluation_criteria.short_content_*
    """
    if not isinstance(evaluation, dict):
        return evaluation
    word_count = int(evaluation.get("word_count") or 0)
    threshold = int(criteria.get("short_content_threshold") or 0)
    min_word_count = int(criteria.get("min_word_count") or 0)
    bonus = float(criteria.get("short_content_bonus") or 1.0)
    if threshold > 0 and word_count >= min_word_count and word_count <= threshold and bonus > 1:
        quality = float(evaluation.get("quality_score") or 0)
        boosted = min(quality * bonus, 1.0)
        evaluation["quality_score"] = boosted
        if "base_usability_score" in evaluation:
            evaluation["base_usability_score"] = boosted
        relevance = float(evaluation.get("relevance_score") or evaluation.get("base_relevance_score") or 0)
        seo = float(evaluation.get("seo_potential_score") or 0)
        evaluation["material_score"] = round((relevance * 0.35 + boosted * 0.45 + seo * 0.20) * 100, 2)
    return evaluation


def _score_snapshot(eval_result: Dict[str, Any], dedup_result: Dict[str, Any]) -> Dict[str, Any]:
    """提取统一评分快照，便于记录、调试和下游审计。"""
    return {
        "quality_score": float(eval_result.get("quality_score") or 0),
        "relevance_score": float(eval_result.get("relevance_score") or 0),
        "seo_potential_score": float(eval_result.get("seo_potential_score") or 0),
        "readability_score": float(eval_result.get("readability_score") or 0),
        "word_count": int(eval_result.get("word_count") or 0),
        "has_copyright_risk": bool(eval_result.get("has_copyright_risk")),
        "is_duplicate": bool(dedup_result.get("is_duplicate")),
        "duplicate_similarity": float(dedup_result.get("similarity_score") or 0),
    }


def _format_decision_reason(decision: str, reason_codes: List[str], scores: Dict[str, Any]) -> str:
    """把机器可读原因压缩成人能扫一眼看懂的说明。"""
    score_text = (
        f"quality={scores['quality_score']:.2f}, "
        f"relevance={scores['relevance_score']:.2f}, "
        f"seo={scores['seo_potential_score']:.2f}, "
        f"words={scores['word_count']}, "
        f"duplicate={scores['is_duplicate']}"
    )
    reason_text = ", ".join(reason_codes) if reason_codes else "rules_matched"
    return f"{decision}: {reason_text}; {score_text}"


def _resolve_pass_to_scoring_status(crawler_db_cfg: Dict[str, Any]) -> str:
    """
    解析 crawler 通过门禁后的状态值。

    说明：
    - 优先使用新契约 `pass_to_scoring_status`
    - 若当前环境尚未迁移完成，则兼容回退到历史 `pass_to_topic_status`
    """
    return (
        crawler_db_cfg.get("pass_to_scoring_status")
        or crawler_db_cfg.get("pass_to_topic_status")
        or "pass_to_scoring"
    )


def _gate_failure_codes(eval_result: Dict[str, Any]) -> List[str]:
    details = eval_result.get("details") or {}
    failures = details.get("gate_failures")
    if not isinstance(failures, list):
        return []
    return [str(code) for code in failures if code]


def _derive_gate_failures(eval_result: Dict[str, Any], criteria_cfg: Dict[str, Any]) -> List[str]:
    """
    从评估结果的顶层字段和 details 中统一推导门禁失败原因。

    目的：
    - 不把门禁判定绑死在 `details.gate_failures`
    - 对 mock/兼容输入和补偿后结果保持稳定
    """
    failures = list(dict.fromkeys(_gate_failure_codes(eval_result)))

    min_base_relevance = float(criteria_cfg.get("min_base_relevance_score", criteria_cfg.get("min_relevance_score", 0.4)) or 0.4)
    min_base_usability = float(criteria_cfg.get("min_base_usability_score", criteria_cfg.get("min_quality_score", 0.5)) or 0.5)
    require_source_ok = bool(criteria_cfg.get("require_source_ok", False))
    require_content_complete = bool(criteria_cfg.get("require_content_complete", False))
    require_topic_hint = bool(criteria_cfg.get("require_topic_hint", False))
    max_noise_ratio = float(criteria_cfg.get("max_noise_ratio", 0.35) or 0.35)

    if bool(eval_result.get("has_copyright_risk")) and "copyright_risk" not in failures:
        failures.append("copyright_risk")
    if require_source_ok and not bool(eval_result.get("source_ok")) and "invalid_source" not in failures:
        failures.append("invalid_source")
    if require_content_complete and not bool(eval_result.get("content_complete")) and "content_incomplete" not in failures:
        failures.append("content_incomplete")
    if require_topic_hint and not str(eval_result.get("topic_hint") or "").strip() and "empty_topic_hint" not in failures:
        failures.append("empty_topic_hint")
    if float(eval_result.get("noise_ratio") or 0) > max_noise_ratio and "noise_too_high" not in failures:
        failures.append("noise_too_high")
    if float(eval_result.get("base_relevance_score") or eval_result.get("relevance_score") or 0) < min_base_relevance and "low_base_relevance" not in failures:
        failures.append("low_base_relevance")
    if float(eval_result.get("base_usability_score") or eval_result.get("quality_score") or 0) < min_base_usability and "low_base_usability" not in failures:
        failures.append("low_base_usability")
    return failures


def _sync_gate_fields(evaluation: Dict[str, Any], criteria: Dict[str, Any]) -> Dict[str, Any]:
    """让门禁相关字段与当前评估结果重新保持一致。"""
    if not isinstance(evaluation, dict):
        return evaluation
    failures = _derive_gate_failures(evaluation, criteria)
    gate_passed = not failures
    details = dict(evaluation.get("details") or {})
    details["gate_failures"] = failures
    evaluation["details"] = details
    evaluation["gate_passed"] = gate_passed
    evaluation["gate_result"] = "pass_to_scoring" if gate_passed else "discard"
    evaluation["next_agent"] = "ScoringAgent" if gate_passed else None
    if gate_passed:
        evaluation["reason"] = "通过 crawler 门禁，可传给 ScoringAgent"
    else:
        evaluation["reason"] = "未通过门禁：" + ", ".join(failures) if failures else "未通过门禁"
    return evaluation


def _decide(
    *,
    eval_result: Dict[str, Any],
    dedup_result: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    crawler 门禁层纯规则决策：
    - discard：去重失败、评估失败、命中重复或任一门禁条件失败
    - pass_to_scoring：通过全部门禁，交给评分层继续判断内容价值
    """
    crawler_db_cfg = cfg.get("crawler_db") or {}
    criteria_cfg = cfg.get("evaluation_criteria") or {}
    scores = _score_snapshot(eval_result, dedup_result)
    gate_failures = _derive_gate_failures(eval_result, criteria_cfg)

    reason_codes: List[str] = []
    if not eval_result.get("success", True):
        reason_codes.append("evaluation_failed")
    if not dedup_result.get("success", True):
        reason_codes.append("dedup_failed")
    if scores["is_duplicate"]:
        reason_codes.append("duplicate")
    for code in gate_failures:
        if code not in reason_codes:
            reason_codes.append(code)

    gate_result = eval_result.get("gate_result") or ("discard" if gate_failures else "pass_to_scoring")
    should_discard = (
        (not eval_result.get("success", True))
        or (not dedup_result.get("success", True))
        or scores["is_duplicate"]
        or gate_result == "discard"
    )

    if should_discard:
        discard_codes = reason_codes or ["discard_rule_matched"]
        return {
            "decision": "discard",
            "status_to_update": crawler_db_cfg.get("discard_status") or "discarded",
            "reason_codes": discard_codes,
            "decision_reason": _format_decision_reason("discard", discard_codes, scores),
            "scores": scores,
        }

    pass_codes = ["gate_passed", "not_duplicate"]
    return {
        "decision": "pass_to_scoring",
        "status_to_update": _resolve_pass_to_scoring_status(crawler_db_cfg),
        "reason_codes": pass_codes,
        "decision_reason": _format_decision_reason("pass_to_scoring", pass_codes, scores),
        "scores": scores,
    }


def _build_scoring_payload(
    *,
    item: Dict[str, Any],
    eval_result: Dict[str, Any],
    dedup_result: Dict[str, Any],
    target_keywords: List[str],
    source_summary_max_length: int,
) -> Dict[str, Any]:
    """
    通过门禁后的标准化素材：
    - 只传递 crawler 已经确定的事实，不在此层做后续生产决策
    """
    content = item.get("content") or ""
    summary_limit = max(int(source_summary_max_length or 220), 1)
    summary = content[:summary_limit].strip()
    return {
        "title": item.get("title") or "",
        "content": content,
        "source_url": item.get("source_url") or "",
        "published_at": item.get("published_at"),
        "target_keywords": target_keywords,
        "topic_hint": eval_result.get("topic_hint") or item.get("title") or "",
        "source_title": item.get("title") or "",
        "source_summary": summary,
        "gate_result": eval_result.get("gate_result") or "pass_to_scoring",
        "base_relevance_score": float(eval_result.get("base_relevance_score") or 0),
        "base_usability_score": float(eval_result.get("base_usability_score") or 0),
        "source_ok": bool(eval_result.get("source_ok")),
        "content_complete": bool(eval_result.get("content_complete")),
        "noise_ratio": float(eval_result.get("noise_ratio") or 0),
        "material_score": float(eval_result.get("material_score") or 0),
        "word_count": int(eval_result.get("word_count") or 0),
        "evaluation": eval_result,
        "dedup": dedup_result,
        "meta": {
            "source": "crawler",
            "crawler_record_id": item.get("id"),
            "author": item.get("author"),
            "category": item.get("category"),
            "spider_name": item.get("spider_name"),
        },
    }


def _load_crawler_prompt(config_dir: str) -> str:
    """
    读取 agents/crawler_processor_agent/prompt.md。

    这里不做复杂模板渲染，只把 prompt.md 作为“系统说明 + 规则说明”拼进 CrewAI 决策任务里。
    """
    prompt_path = os.path.join(config_dir, "prompt.md")
    if not os.path.isfile(prompt_path):
        return ""
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def _decide_with_crewai(
    *,
    item: Dict[str, Any],
    eval_result: Dict[str, Any],
    target_keywords: List[str],
    llm_cfg: Dict[str, Any],
    decision_cfg: Dict[str, Any],
    prompt_template: str,
) -> Dict[str, Any]:
    """
    历史兼容的 LLM helper。

    为什么这里仍保留工具评估，而不是让 LLM 自己评估：
    - 去重/字数/底线规则属于确定性逻辑，交给工具更可控
    - LLM 更适合做“标题优化/保留要点补充”这种非确定性工作
    """
    if not all([Agent, Crew, Process, Task]):
        raise RuntimeError("CrewAI 未安装，无法使用历史兼容 LLM helper")

    model = (llm_cfg.get("model") or "gpt-4o") if isinstance(llm_cfg, dict) else "gpt-4o"

    agent = Agent(
        role="爬虫内容处理专家",
        goal="基于评估结果补充结构化说明，辅助门禁结果审阅。",
        backstory="你负责辅助解释 crawler 门禁结果，帮助补充标题优化建议与保留要点，但不替代主工作流的确定性门禁判断。",
        verbose=False,
        allow_delegation=False,
        llm=model,
    )

    quality = float(eval_result.get("quality_score") or 0)
    relevance = float(eval_result.get("relevance_score") or 0)
    seo_potential = float(eval_result.get("seo_potential_score") or 0)

    prompt = (
        f"{prompt_template}\n\n"
        "以下是本次需要你决策的单条内容，以及系统已经计算好的去重/评估结果。你不需要再次调用工具。\n\n"
        f"标题：{item.get('title') or ''}\n"
        f"来源：{item.get('source_url') or ''}\n"
        f"目标关键词：{target_keywords}\n"
        f"评分：质量={quality:.2f} 相关性={relevance:.2f} SEO潜力={seo_potential:.2f}\n\n"
        f"去重结果：{json.dumps(decision_cfg.get('dedup_result') or {}, ensure_ascii=False)}\n"
        f"评估结果：{json.dumps(decision_cfg.get('eval_result') or {}, ensure_ascii=False)}\n"
        f"阈值配置：{json.dumps(decision_cfg.get('thresholds') or {}, ensure_ascii=False)}\n\n"
        "要求：\n"
        "- 必须输出 JSON\n"
        "- 当前 helper 仅保留为历史兼容能力，主工作流默认不再调用\n"
        "- suggested_title/must_keep 可选\n\n"
        f"正文（截断）：{(item.get('content') or '')[:1500]}"
    )

    task = Task(
        description=prompt,
        agent=agent,
        expected_output="JSON 对象字符串",
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    )

    result = crew.kickoff()
    return _safe_json_loads(result)


async def _init_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    初始化节点：
    - 从 cfg 拆出子配置（db/dedup/criteria/execution/llm）
    - 读取 prompt.md 缓存到 state（避免每条 item 都读文件）
    - 初始化统计与结果容器
    """
    cfg = state.get("cfg") or {}

    state["cfg"] = cfg
    state["crawler_db_cfg"] = cfg.get("crawler_db") or {}
    state["dedup_cfg"] = cfg.get("dedup") or {}
    state["criteria_cfg"] = cfg.get("evaluation_criteria") or {}
    state["execution_cfg"] = cfg.get("execution") or {}
    state["llm_cfg"] = cfg.get("llm") or {}
    state["prompt_template"] = state.get("prompt_template") or _load_crawler_prompt(state.get("config_dir") or "agents/crawler_processor_agent")

    state["pending_items"] = state.get("pending_items") or []
    state["processed"] = state.get("processed") or []
    state["counts"] = state.get("counts") or {
        "total": 0,
        "discard": 0,
        "pass_to_scoring": 0,
        "error": 0,
        "duplicate": 0,
    }
    state["error"] = None
    return state


async def _fetch_pending_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    拉取待处理数据：
    - 如果调用 run_crawler_workflow 时传了 items，则跳过读库
    - 否则通过 crawler_db_reader 从爬虫库读取 pending 内容
    """
    if state.get("pending_items"):
        return state

    pending = await read_crawler_pending(
        state.get("crawler_db_cfg") or {},
        limit=state.get("limit") or 10,
        min_id=state.get("min_id"),
        max_id=state.get("max_id"),
    )
    if not pending.get("success"):
        state["error"] = pending.get("error") or "读取爬虫数据库失败"
        state["pending_items"] = []
        return state

    state["pending_items"] = pending.get("data") or []
    return state


def _normalize_pending_item(raw_item: Any) -> Dict[str, Any]:
    """把待处理输入规范成 dict，避免异常脏数据打断整批流程。"""
    if isinstance(raw_item, dict):
        return raw_item
    return {
        "id": None,
        "title": "",
        "content": "",
        "source_url": "",
        "raw_item": raw_item,
    }


def _missing_required_fields(item: Dict[str, Any], criteria_cfg: Dict[str, Any]) -> List[str]:
    """根据 crawler 配置校验输入基础字段。"""
    required_fields = criteria_cfg.get("input_required_fields") or criteria_cfg.get("required_fields") or []
    missing: List[str] = []
    for field in required_fields:
        key = str(field or "").strip()
        if not key:
            continue
        value = item.get(key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(key)
            continue
        if isinstance(value, (list, dict)) and not value:
            missing.append(key)
    return missing


def _build_input_validation_eval(item: Dict[str, Any], missing_fields: List[str]) -> Dict[str, Any]:
    """为输入必填字段校验失败生成统一的评估结果。"""
    failures = [f"missing_{field}" for field in missing_fields]
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "")
    source_url = str(item.get("source_url") or "").strip()
    return {
        "success": True,
        "quality_score": 0.0,
        "relevance_score": 0.0,
        "seo_potential_score": 0.0,
        "material_score": 0.0,
        "has_risk": False,
        "topic_hint": "",
        "reason": "输入必填字段缺失：" + ", ".join(missing_fields),
        "base_relevance_score": 0.0,
        "base_usability_score": 0.0,
        "source_ok": bool(source_url),
        "content_complete": bool(title and content.strip()),
        "noise_ratio": 0.0,
        "gate_passed": False,
        "gate_result": "discard",
        "next_agent": None,
        "compatibility_mode": True,
        "word_count": 0,
        "readability_score": 0.0,
        "has_copyright_risk": False,
        "details": {
            "gate_failures": failures,
            "missing_required_fields": missing_fields,
            "input_validation_failed": True,
        },
    }


async def _pick_next_item_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    从 pending_items 取出下一条作为 current_item，并清理上一条的中间结果字段。
    """
    items = state.get("pending_items") or []
    if not items:
        state["current_item"] = None
        return state
    state["current_item"] = _normalize_pending_item(items.pop(0))
    state["pending_items"] = items
    state["input_valid"] = True
    state["dedup_result"] = None
    state["eval_result"] = None
    state["decision"] = None
    state["status_to_update"] = None
    state["decision_reason"] = None
    state["reason_codes"] = []
    state["next_agent"] = None
    state["next_payload"] = None
    return state


async def _validate_input_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    输入结构校验节点：
    - 只检查 crawler 自身要求的基础必填字段
    - 缺字段时直接构造 discard 所需上下文，跳过去重和评估
    """
    item = state.get("current_item") or {}
    criteria_cfg = state.get("criteria_cfg") or {}
    missing_fields = _missing_required_fields(item, criteria_cfg)
    if not missing_fields:
        state["input_valid"] = True
        return state

    state["input_valid"] = False
    state["dedup_result"] = {
        "success": True,
        "is_duplicate": False,
        "similarity_score": 0.0,
        "matched_article": None,
        "details": {},
    }
    state["eval_result"] = _build_input_validation_eval(item, missing_fields)
    return state


def _route_after_pick(state: CrawlerIngestState) -> str:
    """
    pick_next 后的路由：
    - 没有 item → END
    - 有 item → 进入 dedup 节点
    """
    if state.get("current_item") is None:
        return "end"
    return "validate_input"


def _route_after_validate(state: CrawlerIngestState) -> str:
    """输入校验通过后才继续去重，否则直接进入最终决策。"""
    if state.get("input_valid", True):
        return "dedup"
    return "decide"


async def _dedup_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    去重节点：
    - 使用 dedup_checker 与已发布内容做相似度判断
    - published_articles 可通过调用方注入（或未来对接 CMS DB 查询）
    """
    item = state.get("current_item") or {}
    dedup_cfg = state.get("dedup_cfg") or {}

    dedup_result = await check_duplicate(
        title=item.get("title") or "",
        content=item.get("content") or "",
        published_articles=state.get("published_articles"),
        threshold=dedup_cfg.get("threshold"),
        algorithm=dedup_cfg.get("algorithm"),
        config=dedup_cfg,
    )
    state["dedup_result"] = dedup_result
    if dedup_result.get("is_duplicate"):
        state["counts"]["duplicate"] = int(state["counts"].get("duplicate") or 0) + 1
    return state


async def _evaluate_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    评估节点：
    - 使用 content_evaluator 计算质量/相关性/SEO潜力
    - 可选对短内容加分（配置驱动）
    """
    item = state.get("current_item") or {}
    criteria_cfg = state.get("criteria_cfg") or {}

    eval_result = await evaluate_content(
        title=item.get("title") or "",
        content=item.get("content") or "",
        source_url=item.get("source_url") or "",
        target_keywords=state.get("target_keywords") or [],
        config={
            "min_word_count": criteria_cfg.get("min_word_count"),
            "max_word_count": criteria_cfg.get("max_word_count"),
            "use_llm": False,
        },
    )
    eval_result = _apply_short_content_bonus(eval_result, criteria_cfg)
    state["eval_result"] = _sync_gate_fields(eval_result, criteria_cfg)
    return state


async def _decide_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    决策节点：
    - 默认使用纯规则 _decide，保证稳定、便宜、可解释
    - crawler 只做门禁层输出：discard / pass_to_scoring
    - 决策后生成标准化素材 payload，供评分层继续判断价值
    """
    item = state.get("current_item") or {}
    dedup_result = state.get("dedup_result") or {}
    eval_result = state.get("eval_result") or {}

    cfg = state.get("cfg") or {}

    decision = _decide(eval_result=eval_result, dedup_result=dedup_result, cfg=cfg)
    state["decision"] = decision.get("decision")
    state["status_to_update"] = decision.get("status_to_update")
    state["decision_reason"] = decision.get("decision_reason")
    state["reason_codes"] = decision.get("reason_codes") or []

    if state["decision"] == "pass_to_scoring":
        state["next_agent"] = "ScoringAgent"
        state["next_payload"] = _build_scoring_payload(
            item=item,
            eval_result=eval_result,
            dedup_result=dedup_result,
            target_keywords=state.get("target_keywords") or [],
            source_summary_max_length=int((state.get("criteria_cfg") or {}).get("source_summary_max_length") or 220),
        )
    else:
        state["next_agent"] = None
        state["next_payload"] = None
    return state


async def _update_status_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    状态更新节点：
    - dry_run=True 时不落库
    - dry_run=False 时把 status_to_update 写回爬虫库
    """
    if state.get("dry_run"):
        return state
    item = state.get("current_item") or {}
    record_id = item.get("id")
    if record_id is None:
        return state

    status = state.get("status_to_update") or "processed"
    result = await update_crawler_status(
        state.get("crawler_db_cfg") or {},
        record_id=record_id,
        new_status=status,
    )
    if not result.get("success"):
        state["error"] = result.get("error") or "更新爬虫状态失败"
    return state


async def _record_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    记录节点：
    - 累计 counts（total/discard/pass_to_scoring/error/duplicate）
    - 把每条 item 的处理结果追加到 processed 列表
    """
    item = state.get("current_item") or {}
    record_id = item.get("id")
    title = item.get("title") or ""

    counts = state.get("counts") or {}
    counts["total"] = int(counts.get("total") or 0) + 1

    decision = state.get("decision") or "discard"
    if decision == "pass_to_scoring":
        counts["pass_to_scoring"] = int(counts.get("pass_to_scoring") or 0) + 1
    elif decision == "error":
        counts["error"] = int(counts.get("error") or 0) + 1
    else:
        counts["discard"] = int(counts.get("discard") or 0) + 1

    state["counts"] = counts

    processed = state.get("processed") or []
    processed.append(
        {
            "record_id": record_id,
            "title": title,
            "decision": decision,
            "status_to_update": state.get("status_to_update"),
            "decision_reason": state.get("decision_reason"),
            "reason_codes": state.get("reason_codes"),
            "scores": _score_snapshot(state.get("eval_result") or {}, state.get("dedup_result") or {}),
            "next_agent": state.get("next_agent"),
            "next_payload": state.get("next_payload"),
            "dedup": state.get("dedup_result"),
            "evaluation": state.get("eval_result"),
            "dry_run": state.get("dry_run"),
        }
    )
    state["processed"] = processed
    return state


def _build_graph() -> StateGraph:
    """
    组装 LangGraph 状态机（批处理循环）：
    init → fetch_pending → pick_next → dedup → evaluate → decide → update_status → record → pick_next → ...
    """
    if StateGraph is None or END is None:
        raise RuntimeError("LangGraph 未安装，无法构建状态图")

    g = StateGraph(CrawlerIngestState)
    g.add_node("init", _init_node)
    g.add_node("fetch_pending", _fetch_pending_node)
    g.add_node("pick_next", _pick_next_item_node)
    g.add_node("validate_input", _validate_input_node)
    g.add_node("dedup", _dedup_node)
    g.add_node("evaluate", _evaluate_node)
    g.add_node("decide", _decide_node)
    g.add_node("update_status", _update_status_node)
    g.add_node("record", _record_node)

    g.set_entry_point("init")
    g.add_edge("init", "fetch_pending")
    g.add_edge("fetch_pending", "pick_next")
    g.add_conditional_edges("pick_next", _route_after_pick, {"validate_input": "validate_input", "end": END})
    g.add_conditional_edges("validate_input", _route_after_validate, {"dedup": "dedup", "decide": "decide"})
    g.add_edge("dedup", "evaluate")
    g.add_edge("evaluate", "decide")
    g.add_edge("decide", "update_status")
    g.add_edge("update_status", "record")
    g.add_edge("record", "pick_next")
    return g


async def _run_sequential(initial: CrawlerIngestState) -> CrawlerIngestState:
    """
    无 LangGraph 环境下的顺序执行 fallback。

    生产环境仍建议安装 LangGraph 使用状态图；这个 fallback 主要用于本地验证、
    单元测试和轻量部署，让审查评分分支不被编排依赖卡住。
    """
    state = await _init_node(initial)
    state = await _fetch_pending_node(state)
    while True:
        state = await _pick_next_item_node(state)
        if state.get("current_item") is None:
            break
        state = await _validate_input_node(state)
        if state.get("input_valid", True):
            state = await _dedup_node(state)
            state = await _evaluate_node(state)
        state = await _decide_node(state)
        state = await _update_status_node(state)
        state = await _record_node(state)
    return state


async def run_crawler_workflow(
    *,
    limit: int = 10,
    min_id: Optional[int] = None,
    max_id: Optional[int] = None,
    target_keywords: Optional[List[str]] = None,
    dry_run: bool = True,
    items: Optional[List[Dict[str, Any]]] = None,
    published_articles: Optional[List[Dict[str, Any]]] = None,
    config: Optional[Dict[str, Any]] = None,
    config_dir: str = "agents/crawler_processor_agent",
    **_,
) -> Dict[str, Any]:
    """
    对外入口（异步）：
    - items=None：从爬虫 DB 读取 pending
    - items!=None：直接处理传入数据（便于测试/事件触发）
    - 返回：统计 + 每条 item 的决策结果（含 next_payload）
    """
    cfg = config or _load_crawler_processor_config(config_dir)
    initial: CrawlerIngestState = {
        "cfg": cfg,
        "crawler_db_cfg": {},
        "dedup_cfg": {},
        "criteria_cfg": {},
        "execution_cfg": {},
        "llm_cfg": {},
        "limit": limit,
        "min_id": min_id,
        "max_id": max_id,
        "dry_run": dry_run,
        "target_keywords": target_keywords or [],
        "published_articles": published_articles,
        "pending_items": items or [],
        "input_valid": True,
        "config_dir": config_dir,
        "prompt_template": "",
        "current_item": None,
        "dedup_result": None,
        "eval_result": None,
        "decision": None,
        "status_to_update": None,
        "decision_reason": None,
        "reason_codes": [],
        "next_agent": None,
        "next_payload": None,
        "processed": [],
        "counts": {},
        "error": None,
    }

    if StateGraph is None:
        result = await _run_sequential(initial)
    else:
        app = _build_graph().compile()
        result = await app.ainvoke(initial)
    return {
        "workflow": "crawler_ingest",
        "timestamp": datetime.now().isoformat(),
        "dry_run": bool(result.get("dry_run")),
        "error": result.get("error"),
        "counts": result.get("counts") or {},
        "items": result.get("processed") or [],
    }
