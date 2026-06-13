"""
Topic Candidate Reader Tool

从爬虫数据库中读取通过初筛且状态为 pass_to_topic 的候选素材。
支持解析包含初筛评估详情和三层分流意图的 routing_payload 字段。
"""

import json
import logging
from typing import Dict, List, Any, Optional
from crewai.tools import tool

from agents.crawler_processor_agent.tools.crawler_db_reader import CrawlerDBReader

logger = logging.getLogger(__name__)


class TopicCandidateReader(CrawlerDBReader):
    """选题候选素材读取器"""

    def __init__(self, config: Dict[str, Any]):
        cfg = dict(config or {})
        # 将待读取的 pending_status 覆写为 pass_to_topic_status 或 pass_to_topic
        cfg["pending_status"] = cfg.get("pass_to_topic_status") or cfg.get("pending_status") or "pass_to_topic"
        super().__init__(cfg)

    async def read_candidates(
        self,
        limit: int = 10,
        min_id: Optional[Any] = None,
        max_id: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        读取状态为 pass_to_topic 的素材，并深度解析 routing_payload 内的三层初筛信息。
        """
        result = await self.read_pending(limit=limit, min_id=min_id, max_id=max_id)
        if not result.get("success"):
            return result

        data = result.get("data") or []
        candidates = []

        for item in data:
            raw_row = item.get("raw_data") or {}

            # 解析 routing_payload JSON 字段
            routing_payload = {}
            payload_raw = raw_row.get("routing_payload")
            if isinstance(payload_raw, dict):
                routing_payload = payload_raw
            elif isinstance(payload_raw, str) and payload_raw.strip():
                try:
                    routing_payload = json.loads(payload_raw)
                except Exception as e:
                    logger.warning("Failed to parse routing_payload JSON in mysql row: %s", e)

            # 兼容读取，优先从 routing_payload 中提取三层分流所需字段，退而读取单独列
            material_score = routing_payload.get("material_score") or item.get("material_score")
            if material_score is None:
                material_score = (routing_payload.get("evaluation") or {}).get("material_score") or 0.0

            route_tier = routing_payload.get("route_tier") or item.get("route_tier")

            rewrite_required = routing_payload.get("rewrite_required")
            if rewrite_required is None:
                rewrite_required = item.get("rewrite_required")
            if rewrite_required is None:
                rewrite_required = (route_tier == "rewrite_candidate")

            publish_candidate = routing_payload.get("publish_candidate")
            if publish_candidate is None:
                publish_candidate = item.get("publish_candidate")
            if publish_candidate is None:
                publish_candidate = (route_tier == "publish_candidate")

            topic_hint = routing_payload.get("topic_hint") or item.get("topic_hint") or item.get("title") or ""
            source_title = routing_payload.get("source_title") or item.get("source_title") or item.get("title") or ""
            source_summary = routing_payload.get("source_summary") or item.get("source_summary") or item.get("content") or ""
            source_url = routing_payload.get("source_url") or item.get("source_url") or item.get("source_url") or ""

            candidate = {
                "id": item.get("id"),
                "status": "pass_to_topic",
                "material_score": float(material_score or 0.0),
                "route_tier": route_tier,
                "rewrite_required": bool(rewrite_required),
                "publish_candidate": bool(publish_candidate),
                "topic_hint": topic_hint,
                "source_title": source_title,
                "source_summary": source_summary,
                "source_url": source_url,
                "dedup": routing_payload.get("dedup") or {},
                "evaluation": routing_payload.get("evaluation") or {},
                "routing_payload": routing_payload
            }
            candidates.append(candidate)

        return {
            "success": True,
            "data": candidates,
            "total": len(candidates)
        }


@tool
async def get_topic_candidate_reader_tool(config: Dict[str, Any]) -> TopicCandidateReader:
    """获取选题候选素材读取器工具"""
    return TopicCandidateReader(config)
