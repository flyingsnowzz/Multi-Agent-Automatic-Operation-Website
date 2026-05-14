#!/usr/bin/env python3
"""
Crawler 摄取/清洗/分流工作流（独立流程）

为什么它看起来与 hybrid_workflow.py 差异很大：
- hybrid_workflow.py 的输入是“一个 topic”，目标是产出“从调研到发布”的整条内容生产链路；
  每一步都要生成较大段内容/结构化结果，因此每个阶段都用 CrewAI 来做 LLM 生成。
- crawler_workflow.py 的输入是“一批爬虫 item（待处理内容）”，目标是把它们做“清洗与路由”：
  去重、质量评估、相关性判断、状态更新、分流（discard/publish/rewrite）。
  这类工作更强调确定性与稳定性，所以这里的模式是：
  - LangGraph 负责批处理循环与状态机（逐条处理、可重复运行、可追踪）
  - 规则/工具负责去重与评估（便于解释与调参）
  - CrewAI 只在需要 LLM 的地方介入：生成 rewrite 简报、辅助决策结构化输出

它如何“利用 agents/ 目录”：
- 读取 agents/crawler_processor_agent/config.yaml 作为阈值与状态字段配置
- 读取 agents/crawler_processor_agent/prompt.md 作为 CrewAI 决策/改写简报的提示模板
- 调用 agents/crawler_processor_agent/tools/* 提供的读库/评估/去重/更新状态工具
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from crewai import Agent, Crew, Process, Task
from langgraph.graph import END, StateGraph

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

    dedup_result: Optional[Dict[str, Any]]
    eval_result: Optional[Dict[str, Any]]

    decision: Optional[str]
    status_to_update: Optional[str]
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
            return os.getenv(env_key, "")
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
    bonus = float(criteria.get("short_content_bonus") or 1.0)
    if threshold > 0 and 0 < word_count <= threshold and bonus > 1:
        quality = float(evaluation.get("quality_score") or 0)
        evaluation["quality_score"] = min(quality * bonus, 1.0)
    return evaluation


def _decide(
    *,
    eval_result: Dict[str, Any],
    dedup_result: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    纯规则决策（不依赖 LLM）：
    - publish：质量/相关/SEO 潜力都达标且无重复/无风险
    - discard：重复或低于任一底线（质量/相关/字数）
    - rewrite：介于两者之间，进入改写链路

    注意：
    - 这是兜底策略：当 LLM 决策输出不合规时，会回退到此函数
    """
    execution_cfg = cfg.get("execution") or {}
    criteria_cfg = cfg.get("evaluation_criteria") or {}
    crawler_db_cfg = cfg.get("crawler_db") or {}

    auto_publish_threshold = float(execution_cfg.get("auto_publish_threshold") or 0.8)
    rewrite_threshold = float(execution_cfg.get("rewrite_threshold") or 0.5)

    min_quality = float(criteria_cfg.get("min_quality_score") or 0.5)
    min_relevance = float(criteria_cfg.get("min_relevance_score") or 0.4)
    min_seo_potential = float(criteria_cfg.get("min_seo_potential_score") or 0.4)
    min_word_count = int(criteria_cfg.get("min_word_count") or 80)
    max_word_count = int(criteria_cfg.get("max_word_count") or 5000)

    is_duplicate = bool(dedup_result.get("is_duplicate"))
    quality_score = float(eval_result.get("quality_score") or 0)
    relevance_score = float(eval_result.get("relevance_score") or 0)
    seo_potential_score = float(eval_result.get("seo_potential_score") or 0)
    word_count = int(eval_result.get("word_count") or 0)
    has_copyright_risk = bool(eval_result.get("has_copyright_risk"))

    discard = (
        is_duplicate
        or quality_score < min_quality
        or relevance_score < min_relevance
        or word_count < min_word_count
        or word_count > max_word_count
    )

    publish = (
        (quality_score >= auto_publish_threshold)
        and (relevance_score >= min_relevance)
        and (seo_potential_score >= min_seo_potential)
        and (not is_duplicate)
        and (not has_copyright_risk)
    )

    rewrite = (
        (quality_score >= rewrite_threshold)
        and (relevance_score >= min_relevance)
        and (not is_duplicate)
    )

    if publish:
        return {
            "decision": "publish",
            "status_to_update": crawler_db_cfg.get("ready_to_publish_status") or "ready_to_publish",
        }
    if discard:
        return {
            "decision": "discard",
            "status_to_update": crawler_db_cfg.get("discard_status") or "discarded",
        }
    if rewrite:
        return {
            "decision": "rewrite",
            "status_to_update": crawler_db_cfg.get("ready_to_rewrite_status") or "ready_to_rewrite",
        }
    return {
        "decision": "discard",
        "status_to_update": crawler_db_cfg.get("discard_status") or "discarded",
    }


def _build_publish_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    publish 分支产物：
    - 形成可交给 CMSAgent 的 payload（这里不直接调用 CMS，只生成下游输入）
    """
    return {
        "title": item.get("title") or "",
        "content": item.get("content") or "",
        "source_url": item.get("source_url") or "",
        "meta": {
            "source": "crawler",
            "crawler_record_id": item.get("id"),
            "published_at": item.get("published_at"),
            "author": item.get("author"),
            "category": item.get("category"),
            "spider_name": item.get("spider_name"),
        },
    }


def _build_rewrite_payload(
    *,
    item: Dict[str, Any],
    eval_result: Dict[str, Any],
    target_keywords: List[str],
) -> Dict[str, Any]:
    """
    rewrite 分支产物（基础结构）：
    - rewrite_instructions 由 LLM 决策节点填充（否则为空）
    - 后续可以把 payload 投递给 WriterAgent → EditorAgent → SEOAgent → CMSAgent
    """
    rewrite_instructions = ""
    return {
        "original_title": item.get("title") or "",
        "original_content": item.get("content") or "",
        "source_url": item.get("source_url") or "",
        "rewrite_instructions": rewrite_instructions,
        "target_keywords": target_keywords,
        "meta": {
            "source": "crawler",
            "crawler_record_id": item.get("id"),
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
    使用 CrewAI 进行“结构化决策 + 改写简报生成”。

    为什么这里仍保留工具评估，而不是让 LLM 自己评估：
    - 去重/字数/底线规则属于确定性逻辑，交给工具更可控
    - LLM 更适合做“改写策略/标题优化/保留要点”这种非确定性工作
    """
    model = (llm_cfg.get("model") or "gpt-4o") if isinstance(llm_cfg, dict) else "gpt-4o"

    agent = Agent(
        role="爬虫内容处理专家",
        goal="基于评估结果做出丢弃/直接发布/改写的决策，并输出结构化路由结果",
        backstory="你负责把爬虫内容变成可运营的内容资产：过滤低质/重复/风险内容，优质内容直接发布，中等内容生成明确改写简报。",
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
        "- decision: discard/publish/rewrite\n"
        "- status_to_update: discarded/ready_to_publish/ready_to_rewrite（或配置中对应值）\n"
        "- next_agent: decision=publish 时为 CMSAgent；decision=rewrite 时为 WriterAgent；discard 时为 null\n"
        "- rewrite_instructions: decision=rewrite 时必填，其它情况为空字符串\n"
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
        "publish": 0,
        "rewrite": 0,
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


async def _pick_next_item_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    从 pending_items 取出下一条作为 current_item，并清理上一条的中间结果字段。
    """
    items = state.get("pending_items") or []
    if not items:
        state["current_item"] = None
        return state
    state["current_item"] = items.pop(0)
    state["pending_items"] = items
    state["dedup_result"] = None
    state["eval_result"] = None
    state["decision"] = None
    state["status_to_update"] = None
    state["next_agent"] = None
    state["next_payload"] = None
    return state


def _route_after_pick(state: CrawlerIngestState) -> str:
    """
    pick_next 后的路由：
    - 没有 item → END
    - 有 item → 进入 dedup 节点
    """
    if state.get("current_item") is None:
        return "end"
    return "dedup"


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
    state["eval_result"] = _apply_short_content_bonus(eval_result, criteria_cfg)
    return state


async def _decide_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    决策节点：
    - 将 dedup_result/eval_result/阈值配置汇总交给 CrewAI 输出 JSON 决策
    - 如果 LLM 输出不合规，则回退到纯规则 _decide
    - 决策后生成 next_payload，供下游投递
    """
    item = state.get("current_item") or {}
    dedup_result = state.get("dedup_result") or {}
    eval_result = state.get("eval_result") or {}

    cfg = state.get("cfg") or {}
    execution_cfg = cfg.get("execution") or {}
    criteria_cfg = cfg.get("evaluation_criteria") or {}
    crawler_db_cfg = cfg.get("crawler_db") or {}

    thresholds = {
        "auto_publish_threshold": float(execution_cfg.get("auto_publish_threshold") or 0.8),
        "rewrite_threshold": float(execution_cfg.get("rewrite_threshold") or 0.5),
        "min_quality_score": float(criteria_cfg.get("min_quality_score") or 0.5),
        "min_relevance_score": float(criteria_cfg.get("min_relevance_score") or 0.4),
        "min_seo_potential_score": float(criteria_cfg.get("min_seo_potential_score") or 0.4),
        "min_word_count": int(criteria_cfg.get("min_word_count") or 80),
        "max_word_count": int(criteria_cfg.get("max_word_count") or 5000),
        "discard_status": crawler_db_cfg.get("discard_status") or "discarded",
        "ready_to_publish_status": crawler_db_cfg.get("ready_to_publish_status") or "ready_to_publish",
        "ready_to_rewrite_status": crawler_db_cfg.get("ready_to_rewrite_status") or "ready_to_rewrite",
    }

    llm_decision = _decide_with_crewai(
        item=item,
        eval_result=eval_result,
        target_keywords=state.get("target_keywords") or [],
        llm_cfg=state.get("llm_cfg") or {},
        decision_cfg={"dedup_result": dedup_result, "eval_result": eval_result, "thresholds": thresholds},
        prompt_template=state.get("prompt_template") or "",
    )

    decision = llm_decision if isinstance(llm_decision, dict) else {}
    if decision.get("decision") not in ("discard", "publish", "rewrite"):
        decision = _decide(eval_result=eval_result, dedup_result=dedup_result, cfg=cfg)
        state["decision"] = decision.get("decision")
        state["status_to_update"] = decision.get("status_to_update")
    else:
        state["decision"] = decision.get("decision")
        state["status_to_update"] = decision.get("status_to_update")

    if state["decision"] == "publish":
        state["next_agent"] = "CMSAgent"
        state["next_payload"] = _build_publish_payload(item)
    elif state["decision"] == "rewrite":
        payload = _build_rewrite_payload(
            item=item,
            eval_result=eval_result,
            target_keywords=state.get("target_keywords") or [],
        )
        if isinstance(decision, dict):
            payload["rewrite_instructions"] = decision.get("rewrite_instructions") or payload.get("rewrite_instructions") or ""
            if decision.get("suggested_title"):
                payload["suggested_title"] = decision.get("suggested_title")
            if decision.get("must_keep"):
                payload["must_keep"] = decision.get("must_keep")
        state["next_agent"] = "WriterAgent"
        state["next_payload"] = payload
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
    - 累计 counts（total/discard/publish/rewrite/error/duplicate）
    - 把每条 item 的处理结果追加到 processed 列表
    """
    item = state.get("current_item") or {}
    record_id = item.get("id")
    title = item.get("title") or ""

    counts = state.get("counts") or {}
    counts["total"] = int(counts.get("total") or 0) + 1

    decision = state.get("decision") or "discard"
    if decision == "publish":
        counts["publish"] = int(counts.get("publish") or 0) + 1
    elif decision == "rewrite":
        counts["rewrite"] = int(counts.get("rewrite") or 0) + 1
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
    g = StateGraph(CrawlerIngestState)
    g.add_node("init", _init_node)
    g.add_node("fetch_pending", _fetch_pending_node)
    g.add_node("pick_next", _pick_next_item_node)
    g.add_node("dedup", _dedup_node)
    g.add_node("evaluate", _evaluate_node)
    g.add_node("decide", _decide_node)
    g.add_node("update_status", _update_status_node)
    g.add_node("record", _record_node)

    g.set_entry_point("init")
    g.add_edge("init", "fetch_pending")
    g.add_edge("fetch_pending", "pick_next")
    g.add_conditional_edges("pick_next", _route_after_pick, {"dedup": "dedup", "end": END})
    g.add_edge("dedup", "evaluate")
    g.add_edge("evaluate", "decide")
    g.add_edge("decide", "update_status")
    g.add_edge("update_status", "record")
    g.add_edge("record", "pick_next")
    return g


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
    app = _build_graph().compile()

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
        "config_dir": config_dir,
        "prompt_template": "",
        "current_item": None,
        "dedup_result": None,
        "eval_result": None,
        "decision": None,
        "status_to_update": None,
        "next_agent": None,
        "next_payload": None,
        "processed": [],
        "counts": {},
        "error": None,
    }

    result = await app.ainvoke(initial)
    return {
        "workflow": "crawler_ingest",
        "timestamp": datetime.now().isoformat(),
        "dry_run": bool(result.get("dry_run")),
        "error": result.get("error"),
        "counts": result.get("counts") or {},
        "items": result.get("processed") or [],
    }
