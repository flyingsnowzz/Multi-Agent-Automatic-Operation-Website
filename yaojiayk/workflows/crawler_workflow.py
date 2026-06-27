#!/usr/bin/env python3
"""
Crawler 轻职责工作流（独立流程）

旧文档中的 CrawlerProcessor 定位是“爬虫内容链路入口”，而不是“门禁评估与分流决策器”。
因此这里的目标收口为：
- 从爬虫库读取待处理内容
- 做最小结构清洗与基础字段校验
- 生成统一的 review payload
- 把素材交给后续独立 Review 阶段
- dry_run 关闭时写回一个中性状态，表示 crawler 已完成入口处理
"""

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

try:
    from langgraph.graph import END, StateGraph
except Exception:
    END = None
    StateGraph = None

from agents.crawler_processor_agent.tools.crawler_db_reader import (
    read_crawler_pending,
    update_crawler_status,
)
from yaojiayk.workflows.run_artifacts import write_run_artifacts


class CrawlerIngestState(TypedDict):
    cfg: Dict[str, Any]
    crawler_db_cfg: Dict[str, Any]
    criteria_cfg: Dict[str, Any]
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

    decision: Optional[str]
    status_to_update: Optional[str]
    decision_reason: Optional[str]
    reason_codes: List[str]
    next_agent: Optional[str]
    next_payload: Optional[Dict[str, Any]]
    validation_result: Optional[Dict[str, Any]]

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
    try:
        import yaml
    except Exception as e:
        raise RuntimeError("缺少依赖 PyYAML，无法读取 crawler_processor_agent/config.yaml") from e

    config_path = os.path.join(config_dir, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _expand_env(raw)


def _resolve_review_status(crawler_db_cfg: Dict[str, Any]) -> str:
    """解析 crawler 完成入口处理后的状态值。"""
    return (
        crawler_db_cfg.get("review_pending_status")
        or crawler_db_cfg.get("review_status")
        or crawler_db_cfg.get("pass_to_topic_status")
        or crawler_db_cfg.get("processed_status")
        or "processed"
    )


def _resolve_error_status(crawler_db_cfg: Dict[str, Any]) -> str:
    return crawler_db_cfg.get("error_status") or "error"


def _summarize_content(content: str, max_length: int) -> str:
    limit = max(int(max_length or 220), 1)
    return content[:limit].strip()


def _build_review_payload(
    *,
    item: Dict[str, Any],
    target_keywords: List[str],
    source_summary_max_length: int,
) -> Dict[str, Any]:
    """生成交给 Review 阶段的轻量标准化素材。"""
    content = item.get("content") or ""
    return {
        "title": item.get("title") or "",
        "content": content,
        "source_url": item.get("source_url") or "",
        "published_at": item.get("published_at"),
        "target_keywords": target_keywords,
        "source_title": item.get("title") or "",
        "source_summary": _summarize_content(content, source_summary_max_length),
        "handoff_stage": "review",
        "normalized_by": "CrawlerProcessorAgent",
        "word_count": len(str(content).split()),
        "meta": {
            "source": "crawler",
            "crawler_record_id": item.get("id"),
            "author": item.get("author"),
            "category": item.get("category"),
            "spider_name": item.get("spider_name"),
        },
    }


def _load_crawler_prompt(config_dir: str) -> str:
    prompt_path = os.path.join(config_dir, "prompt.md")
    if not os.path.isfile(prompt_path):
        return ""
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


async def _init_node(state: CrawlerIngestState) -> CrawlerIngestState:
    cfg = state.get("cfg") or {}

    state["cfg"] = cfg
    state["crawler_db_cfg"] = cfg.get("crawler_db") or {}
    state["criteria_cfg"] = cfg.get("evaluation_criteria") or {}
    state["prompt_template"] = state.get("prompt_template") or _load_crawler_prompt(state.get("config_dir") or "agents/crawler_processor_agent")

    state["pending_items"] = state.get("pending_items") or []
    state["processed"] = state.get("processed") or []
    state["counts"] = state.get("counts") or {
        "total": 0,
        "handoff_to_review": 0,
        "error": 0,
    }
    state["error"] = None
    return state


async def _fetch_pending_node(state: CrawlerIngestState) -> CrawlerIngestState:
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
        return {
            "id": raw_item.get("id"),
            "title": str(raw_item.get("title") or "").strip(),
            "content": str(raw_item.get("content") or "").strip(),
            "source_url": str(raw_item.get("source_url") or "").strip(),
            "published_at": raw_item.get("published_at"),
            "author": raw_item.get("author"),
            "category": raw_item.get("category"),
            "spider_name": raw_item.get("spider_name"),
            "raw_item": raw_item,
        }
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
    """为基础字段校验生成技术校验结果。"""
    return {
        "success": True,
        "reason": "输入必填字段缺失：" + ", ".join(missing_fields),
        "valid": False,
        "normalized": False,
        "details": {
            "missing_required_fields": missing_fields,
            "input_validation_failed": True,
        },
    }


async def _pick_next_item_node(state: CrawlerIngestState) -> CrawlerIngestState:
    items = state.get("pending_items") or []
    if not items:
        state["current_item"] = None
        return state
    state["current_item"] = _normalize_pending_item(items.pop(0))
    state["pending_items"] = items
    state["input_valid"] = True
    state["decision"] = None
    state["status_to_update"] = None
    state["decision_reason"] = None
    state["reason_codes"] = []
    state["next_agent"] = None
    state["next_payload"] = None
    state["validation_result"] = None
    return state


async def _validate_input_node(state: CrawlerIngestState) -> CrawlerIngestState:
    item = state.get("current_item") or {}
    criteria_cfg = state.get("criteria_cfg") or {}
    missing_fields = _missing_required_fields(item, criteria_cfg)
    if not missing_fields:
        state["input_valid"] = True
        state["validation_result"] = {
            "success": True,
            "valid": True,
            "normalized": True,
            "details": {
                "missing_required_fields": [],
                "input_validation_failed": False,
            },
        }
        return state

    state["input_valid"] = False
    state["validation_result"] = _build_input_validation_eval(item, missing_fields)
    return state


def _route_after_pick(state: CrawlerIngestState) -> str:
    if state.get("current_item") is None:
        return "end"
    return "validate_input"


async def _decide_node(state: CrawlerIngestState) -> CrawlerIngestState:
    item = state.get("current_item") or {}
    validation_result = state.get("validation_result") or {}
    crawler_db_cfg = state.get("crawler_db_cfg") or {}
    criteria_cfg = state.get("criteria_cfg") or {}

    if state.get("input_valid", True):
        state["decision"] = "handoff_to_review"
        state["status_to_update"] = _resolve_review_status(crawler_db_cfg)
        state["decision_reason"] = "crawler 已完成入口处理，交给后续 Review 阶段"
        state["reason_codes"] = ["normalized", "ready_for_review"]
        state["next_agent"] = "ReviewAgent"
        state["next_payload"] = _build_review_payload(
            item=item,
            target_keywords=state.get("target_keywords") or [],
            source_summary_max_length=int(criteria_cfg.get("source_summary_max_length") or 220),
        )
    else:
        missing_fields = (validation_result.get("details") or {}).get("missing_required_fields") or []
        state["decision"] = "error"
        state["status_to_update"] = _resolve_error_status(crawler_db_cfg)
        state["decision_reason"] = validation_result.get("reason") or "crawler 输入基础校验失败"
        state["reason_codes"] = [f"missing_{field}" for field in missing_fields] or ["input_validation_failed"]
        state["next_agent"] = None
        state["next_payload"] = None
    return state


async def _update_status_node(state: CrawlerIngestState) -> CrawlerIngestState:
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
        error_message=state.get("decision_reason") if status == _resolve_error_status(state.get("crawler_db_cfg") or {}) else None,
    )
    if not result.get("success"):
        state["error"] = result.get("error") or "更新爬虫状态失败"
    return state


async def _record_node(state: CrawlerIngestState) -> CrawlerIngestState:
    item = state.get("current_item") or {}
    record_id = item.get("id")
    title = item.get("title") or ""

    counts = state.get("counts") or {}
    counts["total"] = int(counts.get("total") or 0) + 1

    decision = state.get("decision") or "error"
    if decision == "handoff_to_review":
        counts["handoff_to_review"] = int(counts.get("handoff_to_review") or 0) + 1
    else:
        counts["error"] = int(counts.get("error") or 0) + 1

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
            "next_agent": state.get("next_agent"),
            "next_payload": state.get("next_payload"),
            "normalized_item": item,
            "validation": state.get("validation_result"),
            "dedup": None,
            "evaluation": None,
            "dry_run": state.get("dry_run"),
        }
    )
    state["processed"] = processed
    return state


def _build_graph() -> StateGraph:
    if StateGraph is None or END is None:
        raise RuntimeError("LangGraph 未安装，无法构建状态图")

    g = StateGraph(CrawlerIngestState)
    g.add_node("init", _init_node)
    g.add_node("fetch_pending", _fetch_pending_node)
    g.add_node("pick_next", _pick_next_item_node)
    g.add_node("validate_input", _validate_input_node)
    g.add_node("decide", _decide_node)
    g.add_node("update_status", _update_status_node)
    g.add_node("record", _record_node)

    g.set_entry_point("init")
    g.add_edge("init", "fetch_pending")
    g.add_edge("fetch_pending", "pick_next")
    g.add_conditional_edges("pick_next", _route_after_pick, {"validate_input": "validate_input", "end": END})
    g.add_edge("validate_input", "decide")
    g.add_edge("decide", "update_status")
    g.add_edge("update_status", "record")
    g.add_edge("record", "pick_next")
    return g


async def _run_sequential(initial: CrawlerIngestState) -> CrawlerIngestState:
    state = await _init_node(initial)
    state = await _fetch_pending_node(state)
    while True:
        state = await _pick_next_item_node(state)
        if state.get("current_item") is None:
            break
        state = await _validate_input_node(state)
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
    runs_root: Optional[str] = None,
    **_,
) -> Dict[str, Any]:
    cfg = config or _load_crawler_processor_config(config_dir)
    initial: CrawlerIngestState = {
        "cfg": cfg,
        "crawler_db_cfg": {},
        "criteria_cfg": {},
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
        "decision": None,
        "status_to_update": None,
        "decision_reason": None,
        "reason_codes": [],
        "next_agent": None,
        "next_payload": None,
        "validation_result": None,
        "processed": [],
        "counts": {},
        "error": None,
    }

    if StateGraph is None:
        result = await _run_sequential(initial)
    else:
        app = _build_graph().compile()
        result = await app.ainvoke(initial)
    out = {
        "workflow": "crawler_ingest",
        "timestamp": datetime.now().isoformat(),
        "dry_run": bool(result.get("dry_run")),
        "error": result.get("error"),
        "counts": result.get("counts") or {},
        "items": result.get("processed") or [],
    }
    if runs_root:
        run_id = uuid.uuid4().hex
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
            },
            result_payload=out,
            error_payload=out.get("error"),
            runs_root=runs_root,
        )
        out["artifact_dir"] = str(run_dir)
    return out
