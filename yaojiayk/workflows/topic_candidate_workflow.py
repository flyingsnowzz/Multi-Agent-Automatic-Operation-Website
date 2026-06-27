"""
Topic Candidate Workflow

负责将爬虫初筛通过(status='pass_to_topic')的候选素材，
通过 TopicAgent 做选题提炼、大纲规划与分类，然后持久化写入 PostgreSQL 的 topics 表，
最后更新爬虫库中候选素材的状态为 processed 归档。
"""

import os
import yaml
import logging
from typing import Dict, List, Any, Optional
from sqlalchemy import create_engine, text

from agents.scoring_agent import TopicAgent
from agents.scoring_agent.tools.topic_candidate_reader import TopicCandidateReader
from agents.crawler_processor_agent.tools.crawler_db_reader import update_crawler_status

logger = logging.getLogger(__name__)


def _load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1].strip()
            return os.getenv(env_key, "")
        return value
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _get_postgres_engine():
    """根据环境变量构建 PostgreSQL 连接引擎"""
    db_user = os.environ.get("POSTGRES_USER", "postgres")
    db_password = os.environ.get("POSTGRES_PASSWORD", "password")
    db_host = os.environ.get("POSTGRES_HOST", "localhost")
    db_port = os.environ.get("POSTGRES_PORT", "5432")
    db_name = os.environ.get("POSTGRES_DB", "multi_agent_cms")
    db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    return create_engine(db_url)


async def run_candidate_to_topics_workflow(
    *,
    limit: int = 10,
    topic_agent_mode: Optional[str] = None,
    crawler_config_dir: str = "agents/crawler_processor_agent",
    topic_config_dir: str = "agents/topic_agent",
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    自数据库消费 pass_to_topic 素材生成选题任务工作流。
    """
    # 1. 加载爬虫配置以连接候选库
    cfg_path = os.path.join(crawler_config_dir, "config.yaml")
    crawler_cfg = _expand_env(_load_yaml(cfg_path))
    crawler_db_cfg = crawler_cfg.get("crawler_db") or {}
    processed_status = crawler_db_cfg.get("processed_status") or "processed"

    # 加载 TopicAgent 配置以获取自定义状态和路由定义
    topic_cfg_path = os.path.join(topic_config_dir, "config.yaml")
    topic_cfg = _expand_env(_load_yaml(topic_cfg_path))
    cfg_status = topic_cfg.get("candidate_status") or {}
    accepted_status = cfg_status.get("accepted") or "topic_accepted"
    rejected_status = cfg_status.get("rejected") or "topic_rejected"
    cfg_routes = topic_cfg.get("workflow_routes") or {}
    rewrite_route = cfg_routes.get("rewrite_candidate") or "full_rewrite_flow"
    publish_route = cfg_routes.get("publish_candidate") or "light_publish_flow"

    # 2. 读取 pass_to_topic 候选数据
    reader = TopicCandidateReader(crawler_db_cfg)
    read_res = await reader.read_candidates(limit=limit)
    if not read_res.get("success"):
        return {
            "success": False,
            "error": f"Failed to read candidates: {read_res.get('error')}",
            "processed_count": 0
        }

    candidates = read_res.get("data") or []
    if not candidates:
        return {
            "success": True,
            "message": "No candidates found with status 'pass_to_topic'.",
            "processed_count": 0,
            "topics": [],
            "rejected": [],
            "accepted_count": 0,
            "rejected_count": 0
        }

    # 3. 初始化 TopicAgent 运行意图推导
    agent = TopicAgent(config_path=os.path.join(topic_config_dir, "config.yaml"), mode=topic_agent_mode)
    agent_res = await agent.execute_on_candidates(candidates, mode=topic_agent_mode)
    topics = agent_res.get("topics") or []
    rejected = agent_res.get("rejected") or []

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "processed_count": len(topics),
            "topics": topics,
            "rejected": rejected,
            "accepted_count": len(topics),
            "rejected_count": len(rejected),
        }

    # 4. 建立 PG 连接并写入 topics 表，同时更新 Crawler 状态
    engine = _get_postgres_engine()
    processed_topics = []
    processed_rejected = []

    insert_query = text("""
        INSERT INTO topics (
            title, target_keywords, search_volume, difficulty, intent_type, content_type, outline, priority, status, source
        ) VALUES (
            :title, :target_keywords, :search_volume, :difficulty, :intent_type, :content_type, :outline, :priority, :status, :source
        ) RETURNING id
    """)

    insert_task_query = text("""
        INSERT INTO tasks (
            agent_name, task_type, input_data, status
        ) VALUES (
            :agent_name, :task_type, :input_data, :status
        )
    """)

    # 处理通过初筛的 topics
    for topic in topics:
        try:
            cand_id = topic.get("candidate_id")
            route_tier = topic.get("route_tier")
            wf_route = topic.get("workflow_route")
            if not wf_route:
                if route_tier == "rewrite_candidate":
                    wf_route = rewrite_route
                elif route_tier == "publish_candidate":
                    wf_route = publish_route
            topic_id = None

            # 1. 写入 topics 关系型数据库
            with engine.connect() as conn:
                res = conn.execute(
                    insert_query,
                    {
                        "title": topic["title"],
                        "target_keywords": topic["target_keywords"],
                        "search_volume": topic["search_volume"],
                        "difficulty": int(topic["keyword_difficulty"]),
                        "intent_type": topic["search_intent"],
                        "content_type": topic["content_type"],
                        "outline": {
                            "points": topic["outline_points"],
                            "candidate_metadata": {
                                "route_tier": topic.get("route_tier"),
                                "rewrite_required": topic.get("rewrite_required"),
                                "publish_candidate": topic.get("publish_candidate"),
                                "source_title": topic.get("source_title"),
                                "source_summary": topic.get("source_summary"),
                                "source_url": topic.get("source_url"),
                                "material_score": topic.get("material_score"),
                                "workflow_route": wf_route,
                            }
                        },
                        "priority": 5 if topic["priority"] == "high" else (3 if topic["priority"] == "medium" else 1),
                        "status": "pending",
                        "source": "crawler"
                    }
                )
                conn.commit()
                row = res.fetchone()
                topic_id = str(row[0]) if row else None

            # 2. 写入 tasks 关系型数据库，创建 topic task
            if topic_id:
                task_agent_name = None
                task_type = None
                target_keywords = topic.get("target_keywords") if isinstance(topic.get("target_keywords"), list) else []
                primary_keyword = str(topic.get("primary_keyword") or (target_keywords[0] if target_keywords else ""))
                secondary_keywords = topic.get("secondary_keywords")
                if not isinstance(secondary_keywords, list):
                    secondary_keywords = [str(x) for x in target_keywords[1:] if str(x).strip()]
                if route_tier == "rewrite_candidate":
                    task_agent_name = "ResearchAgent"
                    task_type = "research_for_rewrite"
                elif route_tier == "publish_candidate":
                    task_agent_name = "CMSAgent"
                    task_type = "publish"

                with engine.connect() as conn:
                    if task_agent_name and task_type:
                        conn.execute(
                            insert_task_query,
                            {
                                "agent_name": task_agent_name,
                                "task_type": task_type,
                                "input_data": {
                                    "workflow_route": wf_route,
                                    "route_tier": route_tier,
                                    "rewrite_required": bool(topic.get("rewrite_required", False)),
                                    "publish_candidate": bool(topic.get("publish_candidate", False)),
                                    "topic_id": topic_id,
                                    "candidate_id": cand_id,
                                    "title": topic["title"],
                                    "primary_keyword": primary_keyword,
                                    "secondary_keywords": secondary_keywords,
                                    "target_keywords": target_keywords,
                                    "search_intent": topic.get("search_intent"),
                                    "content_type": topic.get("content_type"),
                                    "content_angle": topic.get("content_angle"),
                                    "source_title": topic.get("source_title"),
                                    "source_summary": topic.get("source_summary"),
                                    "source_url": topic.get("source_url"),
                                    "source_content": topic.get("source_content") or topic.get("source_summary"),
                                    "material_score": topic.get("material_score"),
                                    "evaluation": topic.get("evaluation") or {},
                                    "dedup": topic.get("dedup") or {},
                                    "routing_payload": topic.get("routing_payload") or {},
                                },
                                "status": "pending"
                            }
                        )
                        conn.commit()

            # 3. 更新 crawler_db 中的状态为 accepted_status 归档
            if cand_id is not None:
                routing_payload = topic.get("routing_payload") or {}
                routing_payload["topic_accepted"] = True
                if wf_route:
                    routing_payload["workflow_route"] = wf_route
                await update_crawler_status(
                    config=crawler_db_cfg,
                    record_id=cand_id,
                    new_status=accepted_status,
                    routing_payload=routing_payload
                )

            topic_copy = dict(topic)
            if topic_id:
                topic_copy["postgres_topic_id"] = topic_id
            processed_topics.append(topic_copy)

        except Exception as e:
            logger.exception("Failed to persist topic or update status for candidate_id=%s: %s", topic.get("candidate_id"), e)

    # 处理未通过初筛的 rejected 列表
    for topic in rejected:
        try:
            cand_id = topic.get("candidate_id")

            # 被拒绝，更新 crawler_db 状态为 rejected_status 并写入 reject_reason
            if cand_id is not None:
                routing_payload = topic.get("routing_payload") or {}
                routing_payload["topic_accepted"] = False
                routing_payload["reject_reason"] = topic.get("reject_reason")
                await update_crawler_status(
                    config=crawler_db_cfg,
                    record_id=cand_id,
                    new_status=rejected_status,
                    routing_payload=routing_payload
                )
            
            processed_rejected.append(dict(topic))

        except Exception as e:
            logger.exception("Failed to update status for rejected candidate_id=%s: %s", topic.get("candidate_id"), e)

    return {
        "success": True,
        "processed_count": len(processed_topics),
        "topics": processed_topics,
        "rejected": processed_rejected,
        "accepted_count": len(processed_topics),
        "rejected_count": len(processed_rejected),
    }
