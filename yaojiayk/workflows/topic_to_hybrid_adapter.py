import asyncio
from typing import Any, Dict, List, Optional


def select_best_topic(topic_agent_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    topics = topic_agent_result.get("topics") if isinstance(topic_agent_result, dict) else None
    if not isinstance(topics, list) or not topics:
        return None
    return sorted(topics, key=lambda x: float((x or {}).get("priority_score") or 0.0), reverse=True)[0]


def topic_item_to_hybrid_topic(topic_item: Dict[str, Any]) -> Dict[str, Any]:
    target_keywords = topic_item.get("target_keywords") if isinstance(topic_item, dict) else None
    primary_keyword = ""
    secondary_keywords: List[str] = []
    if isinstance(target_keywords, list) and target_keywords:
        primary_keyword = str(target_keywords[0] or "")
        secondary_keywords = [str(x) for x in target_keywords[1:] if str(x).strip()]
    else:
        primary_keyword = str(topic_item.get("primary_keyword") or "")

    content_type = str(topic_item.get("content_type") or "guide")
    if content_type not in {"guide", "comparison", "list", "case_study", "how_to"}:
        content_type = "guide"

    return {
        "id": topic_item.get("id") or "",
        "title": str(topic_item.get("title") or ""),
        "primary_keyword": primary_keyword,
        "secondary_keywords": secondary_keywords,
        "content_type": content_type,
        "min_word_count": 1500,
        "max_word_count": 3000,
        "source": "topic_agent",
        "priority": topic_item.get("priority") or "",
        "priority_score": topic_item.get("priority_score") or 0,
        "source_record_id": topic_item.get("candidate_id") or topic_item.get("source_record_id") or "",
        "route_tier": topic_item.get("route_tier"),
        "rewrite_required": topic_item.get("rewrite_required"),
        "publish_candidate": topic_item.get("publish_candidate"),
        "material_score": topic_item.get("material_score"),
        "workflow_route": topic_item.get("workflow_route"),
        "source_title": topic_item.get("source_title"),
        "source_url": topic_item.get("source_url"),
        "source_summary": topic_item.get("source_summary"),
    }


def run_topic_agent_then_hybrid(
    *,
    seed_keywords: List[str],
    topic_agent_mode: Optional[str] = None,
    topic_limit: int = 5,
    config_dir: str = "agents",
    image_mode: str = "plan_only",
) -> Dict[str, Any]:
    from agents.topic_agent import TopicAgent
    from yaojiayk.workflows.hybrid_workflow import HybridWorkflow

    agent = TopicAgent(mode=topic_agent_mode)
    topic_agent_result = asyncio.run(agent.execute(keywords=seed_keywords, limit=topic_limit, mode=topic_agent_mode))
    picked = select_best_topic(topic_agent_result)
    if not picked:
        return {"topic_agent_result": topic_agent_result, "picked_topic": None, "hybrid_result": None}
    hybrid_topic = topic_item_to_hybrid_topic(picked)
    hybrid = HybridWorkflow(config_dir=config_dir, image_mode=image_mode)
    hybrid_result = hybrid.run(hybrid_topic)
    return {"topic_agent_result": topic_agent_result, "picked_topic": picked, "hybrid_topic": hybrid_topic, "hybrid_result": hybrid_result}

