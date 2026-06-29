"""Crawler workflow without the retired yaojiayk package."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from agents.crawler_processor_agent.tools.content_evaluator import ContentEvaluator
from agents.crawler_processor_agent.tools.crawler_db_reader import CrawlerDBReader
from agents.crawler_processor_agent.tools.dedup_checker import check_duplicate


def _resolve_env(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _load_config(config_dir: str, override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    path = Path(config_dir) / "config.yaml"
    base: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            base = _resolve_env(yaml.safe_load(f) or {})
    if override:
        base.update(override)
    return base


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item if isinstance(item, dict) else {}
    content = raw.get("content") or raw.get("description") or raw.get("content_md") or ""
    return {
        **raw,
        "id": raw.get("id") or raw.get("article_id") or raw.get("news_id"),
        "title": raw.get("title") or "",
        "content": content,
        "source_url": raw.get("source_url") or raw.get("original_url") or "",
    }


def _handoff_payload(item: Dict[str, Any], evaluation: Dict[str, Any], dedup: Dict[str, Any]) -> Dict[str, Any]:
    content = item.get("content") or ""
    return {
        "article_id": item.get("id"),
        "title": item.get("title"),
        "content": content,
        "source_url": item.get("source_url"),
        "gate_result": evaluation.get("gate_result"),
        "material_score": evaluation.get("material_score"),
        "base_relevance_score": evaluation.get("base_relevance_score"),
        "base_usability_score": evaluation.get("base_usability_score"),
        "topic_hint": evaluation.get("topic_hint"),
        "is_duplicate": dedup.get("is_duplicate", False),
        "similarity_score": dedup.get("similarity_score", 0.0),
    }


async def _load_items(
    *,
    cfg: Dict[str, Any],
    items: Optional[List[Dict[str, Any]]],
    limit: int,
    min_id: Optional[int],
    max_id: Optional[int],
) -> Dict[str, Any]:
    if items is not None:
        return {"success": True, "data": [_normalize_item(item) for item in items], "total": len(items)}

    reader = CrawlerDBReader(cfg.get("crawler_db") or {})
    result = await reader.read_pending(limit=limit, min_id=min_id, max_id=max_id)
    if not result.get("success"):
        return result
    result["data"] = [_normalize_item(item) for item in result.get("data") or []]
    return result


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
    **_: Any,
) -> Dict[str, Any]:
    cfg = _load_config(config_dir, config)
    loaded = await _load_items(cfg=cfg, items=items, limit=limit, min_id=min_id, max_id=max_id)
    if not loaded.get("success"):
        return {"success": False, "status": "error", "error": loaded.get("error"), "items": []}

    evaluation_cfg = cfg.get("evaluation_criteria") or {}
    dedup_cfg = cfg.get("dedup") or {}
    evaluator = ContentEvaluator(evaluation_cfg)

    processed: List[Dict[str, Any]] = []
    handoff: List[Dict[str, Any]] = []
    discarded: List[Dict[str, Any]] = []

    for item in loaded.get("data") or []:
        evaluation = await evaluator.evaluate(
            title=item.get("title", ""),
            content=item.get("content", ""),
            source_url=item.get("source_url", ""),
            target_keywords=target_keywords or [],
        )
        dedup = await check_duplicate(
            title=item.get("title", ""),
            content=item.get("content", ""),
            published_articles=published_articles or [],
            config=dedup_cfg,
        )
        duplicate = bool(dedup.get("is_duplicate"))
        gate_passed = bool(evaluation.get("gate_passed")) and not duplicate
        status = "handoff_to_review" if gate_passed else "discard"
        record = {
            "id": item.get("id"),
            "title": item.get("title"),
            "status": status,
            "dry_run": dry_run,
            "evaluation": evaluation,
            "dedup": dedup,
            "handoff_payload": _handoff_payload(item, evaluation, dedup),
        }
        processed.append(record)
        if gate_passed:
            handoff.append(record["handoff_payload"])
        else:
            discarded.append(record)

    return {
        "success": True,
        "status": "completed",
        "dry_run": dry_run,
        "total": len(processed),
        "handoff_to_review_count": len(handoff),
        "discard_count": len(discarded),
        "items": processed,
        "handoff_payloads": handoff,
    }
