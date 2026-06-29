"""
Scheduled Publish Workflow

从 tasks 表中领取待发布任务并调用 CMSAgent 执行发布。
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, and_, create_engine, select, update

from agents.cms_agent.cms_agent import CMSAgent

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_engine(db_url: Optional[str] = None):
    if db_url:
        return create_engine(db_url)
    db_user = os.environ.get("POSTGRES_USER", "postgres")
    db_password = os.environ.get("POSTGRES_PASSWORD", "password")
    db_host = os.environ.get("POSTGRES_HOST", "localhost")
    db_port = os.environ.get("POSTGRES_PORT", "5432")
    db_name = os.environ.get("POSTGRES_DB", "multi_agent_cms")
    return create_engine(f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}")


def _tasks_table(engine) -> Table:
    metadata = MetaData()
    return Table("tasks", metadata, autoload_with=engine)


def _normalize_json(value: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else (default or {})
        except Exception:
            return default or {}
    return default or {}


def _normalize_task_row(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["input_data"] = _normalize_json(data.get("input_data"), default={})
    output_val = data.get("output_data")
    if output_val is None:
        data["output_data"] = None
    else:
        data["output_data"] = _normalize_json(output_val, default={})
    return data


async def run_scheduled_publish(
    limit: int = 10,
    dry_run: bool = True,
    db_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    读取数据库 tasks 表，查询并执行 CMSAgent 发布任务。
    
    参数:
        limit: 每次最多处理的任务数量
        dry_run: 是否为试运行模式
        db_url: 数据库连接 URL（若为 None，使用默认 Postgres 配置）
    """
    engine = _get_engine(db_url)
    try:
        tasks = _tasks_table(engine)

        # 1. 查询待发布任务
        with engine.begin() as conn:
            stmt = select(tasks).where(
                and_(
                    tasks.c.agent_name == "CMSAgent",
                    tasks.c.task_type == "publish",
                    tasks.c.status == "pending"
                )
            )
            if hasattr(tasks.c, "created_at"):
                stmt = stmt.order_by(tasks.c.created_at.asc())
            rows = conn.execute(stmt).mappings().all()

        # 2. 原子领取任务（控制不超过 limit）
        claimed_tasks = []
        skipped_count = 0

        for row in rows:
            if len(claimed_tasks) >= limit:
                break
            task_id = str(row["id"])

            with engine.begin() as conn:
                # 只有 status='pending' 时更新为 status='publishing'
                claim_stmt = update(tasks).where(
                    and_(
                        tasks.c.id == task_id,
                        tasks.c.status == "pending"
                    )
                ).values(
                    status="publishing",
                    started_at=_utcnow()
                )
                res = conn.execute(claim_stmt)
                if res.rowcount == 1:
                    claimed_tasks.append(_normalize_task_row(row))
                else:
                    skipped_count += 1

        # 3. 处理已领取任务 (执行次数不超过 picked)
        published_count = 0
        dry_run_count = 0
        failed_count = 0
        all_claimed_task_ids = [t["id"] for t in claimed_tasks]

        for task in claimed_tasks:
            task_id = task["id"]
            input_data = task["input_data"] or {}

            # 解析并兼容 input_data 结构
            if "article" in input_data and "page_info" in input_data:
                article = input_data.get("article")
                page_info = input_data.get("page_info")
                images = input_data.get("images")
            else:
                # 从扁平字段推导最小 CMS payload
                tags = input_data.get("secondary_keywords")
                if not tags:
                    tags = input_data.get("target_keywords")
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                elif not isinstance(tags, list):
                    tags = []

                article = {
                    "title": input_data.get("title") or "",
                    "content_md": input_data.get("source_content") or input_data.get("source_summary") or "",
                    "content": input_data.get("source_content") or input_data.get("source_summary") or "",
                }
                page_info = {
                    "category": input_data.get("content_type") or "",
                    "tags": tags,
                    "slug": ""
                }
                images = {}

            try:
                # 初始化 CMSAgent 并安全注入 dry_run 配置
                agent = CMSAgent()
                publishing = agent.config.setdefault("publishing", {})
                publishing["dry_run"] = bool(dry_run)

                # 执行发布逻辑
                result = await agent.execute(
                    article=article,
                    page_info=page_info,
                    images=images
                )

                status_str = result.get("status")
                errors = result.get("errors") or []

                if errors or status_str in {"failed", "publish_blocked", "retry_pending"}:
                    err_msg = f"CMSAgent returned status '{status_str}' with errors: {errors}"
                    with engine.begin() as conn:
                        conn.execute(
                            update(tasks).where(tasks.c.id == task_id).values(
                                status="publish_failed",
                                error_message=err_msg[:500],
                                completed_at=_utcnow(),
                                output_data=result
                            )
                        )
                    failed_count += 1
                elif status_str == "dry_run":
                    # 试运行模式，将状态重置为 pending 以便后续发布，不误标为已发布
                    with engine.begin() as conn:
                        conn.execute(
                            update(tasks).where(tasks.c.id == task_id).values(
                                status="pending",
                                error_message=None,
                                completed_at=_utcnow(),
                                output_data=result
                            )
                        )
                    dry_run_count += 1
                elif status_str in {"published", "draft", "scheduled"}:
                    # 真实发布成功
                    with engine.begin() as conn:
                        conn.execute(
                            update(tasks).where(tasks.c.id == task_id).values(
                                status="published",
                                error_message=None,
                                completed_at=_utcnow(),
                                output_data=result
                            )
                        )
                    published_count += 1
                else:
                    # 其它未知状态按失败处理
                    err_msg = f"CMSAgent returned unknown status: {status_str}"
                    with engine.begin() as conn:
                        conn.execute(
                            update(tasks).where(tasks.c.id == task_id).values(
                                status="publish_failed",
                                error_message=err_msg,
                                completed_at=_utcnow(),
                                output_data=result
                            )
                        )
                    failed_count += 1

            except Exception as exc:
                err_msg = str(exc)
                err_type = type(exc).__name__
                with engine.begin() as conn:
                    conn.execute(
                        update(tasks).where(tasks.c.id == task_id).values(
                            status="publish_failed",
                            error_message=err_msg[:500],
                            completed_at=_utcnow(),
                            output_data={"error_type": err_type, "error_detail": err_msg}
                        )
                    )
                failed_count += 1

        return {
            "picked": len(claimed_tasks),
            "published": published_count,
            "dry_run": dry_run_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "task_ids": all_claimed_task_ids
        }
    finally:
        engine.dispose()
