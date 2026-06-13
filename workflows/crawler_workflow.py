#!/usr/bin/env python3
"""
Crawler 摄取/清洗/分流工作流（独立流程）

为什么它看起来与 hybrid_workflow.py 差异很大：
- hybrid_workflow.py 的输入是“一个 topic”，目标是产出“从调研到发布”的整条内容生产链路；
  每一步都要生成较大段内容/结构化结果，因此每个阶段都用 CrewAI 来做 LLM 生成。
- crawler_workflow.py 的输入是“一批爬虫 item（待处理内容）”，目标是把它们做“清洗与路由”：
  去重、素材评估、状态更新、分流（discard/pass_to_topic）。
  这类工作更强调确定性与稳定性，所以这里的模式是：
  - LangGraph 负责批处理循环与状态机（逐条处理、可重复运行、可追踪）
    - 规则/工具负责去重与评估（便于解释与调参）
    - crawler 只做 discard / pass_to_topic 分流，不负责发布与改写

它如何“利用 agents/ 目录”：
- 读取 agents/crawler_processor_agent/config.yaml 作为阈值与状态字段配置
- 读取 agents/crawler_processor_agent/prompt.md 作为 CrewAI 决策/改写简报的提示模板
- 调用 agents/crawler_processor_agent/tools/* 提供的读库/评估/去重/更新状态工具
"""

import json
import os
import re
import logging
import uuid
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
from workflows.run_artifacts import write_run_artifacts

logger = logging.getLogger(__name__)


class CrawlerIngestState(TypedDict):
    """
    LangGraph 状态机的 State（批处理流程）。

    与 hybrid_workflow 的差异点：
    - hybrid_workflow 把每个阶段产物挂到 state（research_result/write_result/...）
    - crawler_workflow 把“当前处理 item + 批处理累计统计”挂到 state（current_item/processed/counts）
    """
    cfg: Dict[str, Any]
    crawler_db_cfg: Dict[str, Any]
    published_db_cfg: Dict[str, Any]
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
    error: Optional[Dict[str, Any]]
    llm_error: Optional[Dict[str, Any]]
    trace_id: Optional[str]


def _trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _current_input_id(state: Dict[str, Any]) -> str:
    item = state.get("current_item") if isinstance(state, dict) else {}
    if isinstance(item, dict):
        return str(item.get("id") or item.get("url") or item.get("title") or "")
    return ""


def _workflow_error(stage: str, exc: Exception, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    input_id = _current_input_id(state or {})
    trace_id = str((state or {}).get("trace_id") or _trace_id())
    logger.exception("crawler_workflow_error stage=%s input_id=%s trace_id=%s", stage, input_id, trace_id)
    return {
        "stage": stage,
        "type": exc.__class__.__name__,
        "message": str(exc),
        "input_id": input_id,
        "trace_id": trace_id,
    }


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
    return _normalize_crawler_config(_expand_env(raw))


def _normalize_crawler_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一 crawler 配置字段命名，同时兼容旧版术语。

    当前规范术语：
    - evaluation_criteria.material_score_threshold
    - evaluation_criteria.input_required_fields
    - evaluation_criteria.source_summary_max_length
    - decision_rules.discard_conditions 中使用 material_score / has_risk / source_ok / topic_hint
    - metrics.metrics_to_track 中使用 average_material_score
    """
    normalized = dict(cfg or {})

    criteria_cfg = dict(normalized.get("evaluation_criteria") or {})
    if "material_score_threshold" not in criteria_cfg and "min_quality_score" in criteria_cfg:
        criteria_cfg["material_score_threshold"] = criteria_cfg.get("min_quality_score")
    if "discard_below_score" not in criteria_cfg:
        criteria_cfg["discard_below_score"] = criteria_cfg.get("material_score_threshold") or 40.0
    if "publish_candidate_threshold" not in criteria_cfg:
        criteria_cfg["publish_candidate_threshold"] = 80.0
    if "input_required_fields" not in criteria_cfg and "required_fields" in criteria_cfg:
        criteria_cfg["input_required_fields"] = criteria_cfg.get("required_fields")
    if "source_summary_max_length" not in criteria_cfg:
        criteria_cfg["source_summary_max_length"] = 220
    normalized["evaluation_criteria"] = criteria_cfg

    decision_rules = dict(normalized.get("decision_rules") or {})
    discard_conditions = decision_rules.get("discard_conditions") or []
    canonical_conditions = []
    alias_map = {
        "quality_score": "material_score",
        "min_quality_score": "material_score_threshold",
        "has_copyright_risk": "has_risk",
    }
    for condition in discard_conditions:
        text = str(condition)
        for legacy, current in alias_map.items():
            text = text.replace(legacy, current)
        canonical_conditions.append(text)
    if canonical_conditions:
        decision_rules["discard_conditions"] = canonical_conditions
    normalized["decision_rules"] = decision_rules

    metrics_cfg = dict(normalized.get("metrics") or {})
    metrics_to_track = metrics_cfg.get("metrics_to_track") or []
    rewritten_metrics = []
    for metric in metrics_to_track:
        if str(metric) == "average_quality_score":
            rewritten_metrics.append("average_material_score")
        else:
            rewritten_metrics.append(metric)
    if rewritten_metrics:
        metrics_cfg["metrics_to_track"] = rewritten_metrics
    normalized["metrics"] = metrics_cfg
    return normalized


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


def _decide(
    *,
    eval_result: Dict[str, Any],
    dedup_result: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    crawler 最终唯一分流点：
    - discard
    - pass_to_topic
    """
    crawler_db_cfg = cfg.get("crawler_db") or {}
    criteria_cfg = cfg.get("evaluation_criteria") or {}
    is_duplicate = bool(dedup_result.get("is_duplicate"))
    similarity_score = float(dedup_result.get("similarity_score") or 0.0)
    material_score = float(eval_result.get("material_score") or 0.0)
    has_risk = bool(eval_result.get("has_risk"))
    source_ok = bool(eval_result.get("source_ok"))
    topic_hint = str(eval_result.get("topic_hint") or "").strip()

    require_source_ok = bool(criteria_cfg.get("require_source_ok", True))
    require_topic_hint = bool(criteria_cfg.get("require_topic_hint", True))

    hard_discard = (
        is_duplicate
        or similarity_score >= 0.85
        or has_risk
        or (require_source_ok and (not source_ok))
        or (require_topic_hint and (not topic_hint))
    )

    discard_below_score = float(criteria_cfg.get("discard_below_score") or criteria_cfg.get("material_score_threshold") or 40.0)
    publish_candidate_threshold = float(criteria_cfg.get("publish_candidate_threshold") or 80.0)

    if hard_discard or material_score < discard_below_score:
        return {
            "decision": "discard",
            "status_to_update": crawler_db_cfg.get("discard_status") or "discarded",
            "route_tier": None,
            "rewrite_required": None,
            "publish_candidate": None,
        }

    # 40 <= score < 80
    if material_score < publish_candidate_threshold:
        return {
            "decision": "pass_to_topic",
            "status_to_update": crawler_db_cfg.get("pass_to_topic_status") or "pass_to_topic",
            "route_tier": "rewrite_candidate",
            "rewrite_required": True,
            "publish_candidate": False,
        }

    # score >= 80
    return {
        "decision": "pass_to_topic",
        "status_to_update": crawler_db_cfg.get("pass_to_topic_status") or "pass_to_topic",
        "route_tier": "publish_candidate",
        "rewrite_required": False,
        "publish_candidate": True,
    }


def _build_topic_payload(
    *,
    item: Dict[str, Any],
    eval_result: Dict[str, Any],
    target_keywords: List[str],
    source_summary_max_length: int = 220,
    route_tier: Optional[str] = None,
    rewrite_required: Optional[bool] = None,
    publish_candidate: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    pass_to_topic 分支产物：
    - 形成可交给 TopicAgent 的 payload（选题线索格式）
    """
    content = item.get("content") or ""
    summary = _build_source_summary(content, limit=source_summary_max_length)
    payload = {
        "topic_hint": str(eval_result.get("topic_hint") or ""),
        "source_title": item.get("title") or "",
        "source_summary": summary,
        "source_url": item.get("source_url") or "",
        "material_score": float(eval_result.get("material_score") or 0.0),
    }
    if route_tier is not None:
        payload["route_tier"] = route_tier
    if rewrite_required is not None:
        payload["rewrite_required"] = rewrite_required
    if publish_candidate is not None:
        payload["publish_candidate"] = publish_candidate
    return payload


def _build_source_summary(content: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；：,.;: ") + "..."


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
        goal="基于评估结果做出 discard 或 pass_to_topic 的结构化决策",
        backstory="你负责过滤低质、重复或高风险素材，只保留能进入 TopicAgent 的候选素材。",
        verbose=False,
        allow_delegation=False,
        llm=model,
    )

    material_score = float(eval_result.get("material_score") or 0)

    prompt = (
        f"{prompt_template}\n\n"
        "以下是本次需要你决策的单条内容，以及系统已经计算好的去重/评估结果。你不需要再次调用工具。\n\n"
        f"标题：{item.get('title') or ''}\n"
        f"来源：{item.get('source_url') or ''}\n"
        f"目标关键词：{target_keywords}\n"
        f"素材评分（0-100）：material_score={material_score:.2f}\n\n"
        f"去重结果：{json.dumps(decision_cfg.get('dedup_result') or {}, ensure_ascii=False)}\n"
        f"评估结果：{json.dumps(decision_cfg.get('eval_result') or {}, ensure_ascii=False)}\n"
        f"阈值配置：{json.dumps(decision_cfg.get('thresholds') or {}, ensure_ascii=False)}\n\n"
        "要求：\n"
        "- 必须输出 JSON\n"
        "- decision: discard/pass_to_topic\n"
        "- status_to_update: discarded/pass_to_topic（或配置中对应值）\n"
        "- next_agent: decision=pass_to_topic 时为 TopicAgent；discard 时为 null\n\n"
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
    state["published_db_cfg"] = cfg.get("published_content_db") or {}
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
        "pass_to_topic": 0,
        "error": 0,
        "duplicate": 0,
    }
    state["error"] = None
    state["llm_error"] = None
    state["trace_id"] = state.get("trace_id") or _trace_id()
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
        state["error"] = {
            "stage": "fetch_pending",
            "type": "ReadCrawlerPendingError",
            "message": str(pending.get("error") or "读取爬虫数据库失败"),
            "input_id": "",
            "trace_id": str(state.get("trace_id") or _trace_id()),
        }
        state["pending_items"] = []
        return state

    state["pending_items"] = pending.get("data") or []
    return state


async def _pick_next_item_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    从 pending_items 取出下一条作为 current_item，并清理上一条的中间结果字段。
    同时进行类型安全、空白字符校验、URL 格式自检以及全局异常防护。
    """
    try:
        items = state.get("pending_items") or []
        if not items:
            state["current_item"] = None
            return state
        
        current_item = items.pop(0)
        state["pending_items"] = items
        state["current_item"] = current_item
        
        # 重置上一轮循环的中间状态
        state["dedup_result"] = None
        state["eval_result"] = None
        state["decision"] = None
        state["status_to_update"] = None
        state["next_agent"] = None
        state["next_payload"] = None

        cfg = state.get("cfg") or {}
        crawler_db_cfg = cfg.get("crawler_db") or {}
        criteria_cfg = cfg.get("evaluation_criteria") or {}
        required_fields = (
            criteria_cfg.get("input_required_fields")
            or criteria_cfg.get("required_fields")
            or ["title", "content", "source_url"]
        )

        # 1. 类型安全防御：必须是字典对象
        if not isinstance(current_item, dict):
            logger.error("current_item is not a dictionary: %s", type(current_item))
            current_item = {"id": None, "title": "", "content": "", "source_url": "", "raw_item": current_item}
            state["current_item"] = current_item
            state["decision"] = "discard"
            state["status_to_update"] = crawler_db_cfg.get("discard_status") or "discarded"
            state["eval_result"] = {
                "success": False,
                "error": "invalid_item_type",
                "material_score": 0,
                "has_risk": False,
                "source_ok": False,
                "topic_hint": "",
                "reason": "item 不是字典对象",
                "word_count": 0,
            }
            return state

        # 2. 字段自检（包含空白字符及无效类型处理）
        missing = []
        if isinstance(required_fields, list):
            for f in required_fields:
                k = str(f)
                val = current_item.get(k)
                
                # 检查字段是否存在或是否仅有空白字符
                if val is None:
                    missing.append(k)
                elif isinstance(val, str) and not val.strip():
                    missing.append(k)
                
                # 对 URL 字段进行额外的格式有效性检验
                if k == "source_url" and val:
                    val_str = str(val).strip().lower()
                    if not (val_str.startswith("http://") or val_str.startswith("https://")):
                        missing.append(f"{k}(invalid_format)")

        if missing:
            state["decision"] = "discard"
            state["status_to_update"] = crawler_db_cfg.get("discard_status") or "discarded"
            state["dedup_result"] = {
                "success": True,
                "is_duplicate": False,
                "reason": "missing_or_invalid_fields",
                "missing_fields": missing,
            }
            state["eval_result"] = {
                "success": False,
                "error": "missing_or_invalid_fields",
                "missing_fields": missing,
                "material_score": 0,
                "has_risk": False,
                "source_ok": False,
                "topic_hint": "",
                "reason": "字段缺失或 source_url 非法",
                "word_count": 0,
            }
            
    except Exception as e:
        logger.exception("Exception in _pick_next_item_node: %s", str(e))
        cfg = state.get("cfg") or {}
        crawler_db_cfg = cfg.get("crawler_db") or {}
        state["decision"] = "discard"
        state["status_to_update"] = crawler_db_cfg.get("discard_status") or "discarded"
        state["error"] = {
            "stage": "pick_next_item",
            "type": e.__class__.__name__,
            "message": f"节点异常: {str(e)}",
            "input_id": "",
            "trace_id": str(state.get("trace_id") or ""),
        }
        
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
    - published_articles 缓存在 state 中，避免对每一条待处理内容重复进行数据库全量查询
    """
    if state.get("decision") is not None:
        return state
    item = state.get("current_item") or {}
    dedup_cfg = state.get("dedup_cfg") or {}

    # 首次进入节点时，若没有传入已发布文章缓存，则执行一次全量查询并写入 state 进行缓存
    if state.get("published_articles") is None:
        from agents.crawler_processor_agent.tools.dedup_checker import DedupChecker
        checker = DedupChecker({
            **dedup_cfg,
            "published_db_config": state.get("published_db_cfg") or {},
            "published_limit": dedup_cfg.get("published_limit") or 2000,
        })
        articles, warn = await checker._query_published_articles(limit=checker.published_limit)
        state["published_articles"] = articles if isinstance(articles, list) else []
        if warn:
            logger.warning("Querying published articles warning: %s", warn)

    dedup_result = await check_duplicate(
        title=item.get("title") or "",
        content=item.get("content") or "",
        source_url=item.get("source_url") or "",
        published_articles=state.get("published_articles"),
        threshold=dedup_cfg.get("threshold"),
        algorithm=dedup_cfg.get("algorithm"),
        config={
            **dedup_cfg,
            "published_db_config": state.get("published_db_cfg") or {},
            "published_limit": dedup_cfg.get("published_limit") or 2000,
        },
    )
    state["dedup_result"] = dedup_result
    if dedup_result.get("is_duplicate"):
        state["counts"]["duplicate"] = int(state["counts"].get("duplicate") or 0) + 1
    return state


async def _evaluate_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    评估节点：
    - 输出统一素材评估结构
    - material_score / has_risk / source_ok / topic_hint / reason
    """
    if state.get("decision") is not None:
        return state
    item = state.get("current_item") or {}
    criteria_cfg = state.get("criteria_cfg") or {}
    cfg = state.get("cfg") or {}

    eval_result = await evaluate_content(
        title=item.get("title") or "",
        content=item.get("content") or "",
        source_url=item.get("source_url") or "",
        target_keywords=state.get("target_keywords") or [],
        config={
            "min_word_count": criteria_cfg.get("min_word_count"),
            "max_word_count": criteria_cfg.get("max_word_count"),
            "copyright_risk": cfg.get("copyright_risk") or {},
            "domain_blacklist": cfg.get("domain_blacklist") or [],
        },
    )
    state["eval_result"] = eval_result
    return state


async def _decide_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    决策节点：
    - crawler 唯一最终分流点
    - 只输出 discard / pass_to_topic
    """
    if state.get("decision") is not None:
        return state
    item = state.get("current_item") or {}
    dedup_result = state.get("dedup_result") or {}
    eval_result = state.get("eval_result") or {}

    cfg = state.get("cfg") or {}
    rule_decision = _decide(eval_result=eval_result, dedup_result=dedup_result, cfg=cfg)
    state["decision"] = rule_decision.get("decision")
    state["status_to_update"] = rule_decision.get("status_to_update")

    if state["decision"] == "pass_to_topic":
        criteria_cfg = state.get("criteria_cfg") or {}
        state["next_agent"] = "TopicAgent"
        state["next_payload"] = _build_topic_payload(
            item=item,
            eval_result=eval_result,
            target_keywords=state.get("target_keywords") or [],
            source_summary_max_length=int(criteria_cfg.get("source_summary_max_length") or 220),
            route_tier=rule_decision.get("route_tier"),
            rewrite_required=rule_decision.get("rewrite_required"),
            publish_candidate=rule_decision.get("publish_candidate"),
        )
    else:
        state["next_agent"] = None
        state["next_payload"] = None
    return state


async def _update_status_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    状态更新节点：
    - dry_run=True 时不落库
    - dry_run=False 时把 status_to_update 写回爬虫库，并附加完整的 routing_payload 分流上下文
    """
    if state.get("dry_run"):
        return state
    item = state.get("current_item") or {}
    record_id = item.get("id")
    if record_id is None:
        return state

    eval_result = state.get("eval_result") or {}
    dedup_result = state.get("dedup_result") or {}
    decision = state.get("decision")

    material_score = float(eval_result.get("material_score") or 0.0)
    topic_hint = str(eval_result.get("topic_hint") or "").strip()

    if decision == "discard":
        route_tier = None
        rewrite_required = False
        publish_candidate = False
    else:
        next_payload = state.get("next_payload") or {}
        route_tier = next_payload.get("route_tier")
        rewrite_required = bool(next_payload.get("rewrite_required", False))
        publish_candidate = bool(next_payload.get("publish_candidate", False))

    criteria_cfg = state.get("criteria_cfg") or {}
    summary_len = int(criteria_cfg.get("source_summary_max_length") or 220)
    source_summary = _build_source_summary(item.get("content") or "", limit=summary_len)

    routing_payload = {
        "material_score": material_score,
        "route_tier": route_tier,
        "rewrite_required": rewrite_required,
        "publish_candidate": publish_candidate,
        "topic_hint": topic_hint,
        "source_title": item.get("title") or "",
        "source_summary": source_summary,
        "source_url": item.get("source_url") or "",
        "dedup": dedup_result,
        "evaluation": eval_result,
    }

    status = state.get("status_to_update") or "processed"
    result = await update_crawler_status(
        state.get("crawler_db_cfg") or {},
        record_id=record_id,
        new_status=status,
        routing_payload=routing_payload,
    )
    if not result.get("success"):
        state["error"] = {
            "stage": "update_status",
            "type": "UpdateCrawlerStatusError",
            "message": str(result.get("error") or "更新爬虫状态失败"),
            "input_id": str(record_id),
            "trace_id": str(state.get("trace_id") or _trace_id()),
        }
    return state


async def _record_node(state: CrawlerIngestState) -> CrawlerIngestState:
    """
    记录节点：
    - 累计 counts（total/discard/pass_to_topic/error/duplicate）
    - 把每条 item 的处理结果追加到 processed 列表
    """
    item = state.get("current_item") or {}
    record_id = item.get("id") if isinstance(item, dict) else None
    title = item.get("title") if isinstance(item, dict) else ""

    counts = state.get("counts") or {}
    counts["total"] = int(counts.get("total") or 0) + 1

    decision = state.get("decision") or "discard"
    if decision == "pass_to_topic":
        counts["pass_to_topic"] = int(counts.get("pass_to_topic") or 0) + 1
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
    persist_run: bool = True,
    runs_root: str = "runs",
    **_,
) -> Dict[str, Any]:
    """
    对外入口（异步）：
    - items=None：从爬虫 DB 读取 pending
    - items!=None：直接处理传入数据（便于测试/事件触发）
    - 返回：统计 + 每条 item 的决策结果（含 next_payload）
    """
    cfg = _normalize_crawler_config(config or _load_crawler_processor_config(config_dir))
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
        "llm_error": None,
        "trace_id": _trace_id(),
    }

    result = await app.ainvoke(initial)
    run_id = str(initial["trace_id"])
    out = {
        "workflow": "crawler",
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "dry_run": bool(result.get("dry_run")),
        "error": result.get("error"),
        "counts": result.get("counts") or {},
        "items": result.get("processed") or [],
    }
    if persist_run:
        out["artifact_dir"] = str(os.path.join(runs_root, "crawler", run_id))
        run_dir = write_run_artifacts(
            workflow="crawler",
            run_id=run_id,
            input_payload={
                "limit": limit,
                "min_id": min_id,
                "max_id": max_id,
                "target_keywords": target_keywords or [],
                "dry_run": dry_run,
                "items": items or [],
                "published_articles": published_articles,
                "config_dir": config_dir,
                "config": config,
            },
            result_payload=out,
            error_payload=out.get("error"),
            runs_root=runs_root,
        )
        out["artifact_dir"] = str(run_dir)
    return out
