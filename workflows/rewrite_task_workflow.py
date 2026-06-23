"""
Rewrite Task Workflow

为 rewrite_candidate/full_rewrite_flow 提供最小的数据库任务运行层：
- 执行 ResearchAgent task，并把 research_brief 持久化到 tasks.output_data
- 基于成功的 Research task 派生 WriterAgent task
- 执行 WriterAgent task，并通过 research_task_id 从数据库读取 research_brief
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import MetaData, Table, and_, create_engine, insert, select, update


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


def _load_task(conn, tasks: Table, task_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(select(tasks).where(tasks.c.id == task_id)).mappings().first()
    return _normalize_task_row(row) if row else None


def _set_task_state(
    conn,
    tasks: Table,
    *,
    task_id: str,
    status: str,
    error_message: Optional[str] = None,
    output_data: Any = None,
    started: bool = False,
    completed: bool = False,
) -> None:
    values: Dict[str, Any] = {"status": status, "error_message": error_message}
    now = _utcnow()
    if started:
        values["started_at"] = now
    if completed:
        values["completed_at"] = now
    if output_data is not None:
        values["output_data"] = output_data
    conn.execute(update(tasks).where(tasks.c.id == task_id).values(**values))


def _writer_input_from_research_task(research_task: Dict[str, Any]) -> Dict[str, Any]:
    source = research_task.get("input_data") or {}
    return {
        "workflow_route": source.get("workflow_route"),
        "route_tier": source.get("route_tier"),
        "topic_id": source.get("topic_id"),
        "candidate_id": source.get("candidate_id"),
        "title": source.get("title"),
        "primary_keyword": source.get("primary_keyword"),
        "secondary_keywords": source.get("secondary_keywords") if isinstance(source.get("secondary_keywords"), list) else [],
        "target_keywords": source.get("target_keywords") if isinstance(source.get("target_keywords"), list) else [],
        "search_intent": source.get("search_intent"),
        "content_type": source.get("content_type"),
        "content_angle": source.get("content_angle"),
        "research_task_id": str(research_task.get("id") or ""),
        "research_brief_id": None,
    }


def _find_existing_writer_task(conn, tasks: Table, *, research_task_id: str) -> Optional[Dict[str, Any]]:
    rows = conn.execute(
        select(tasks).where(
            and_(
                tasks.c.agent_name == "WriterAgent",
                tasks.c.task_type == "rewrite_from_research",
            )
        )
    ).mappings()
    for row in rows:
        task = _normalize_task_row(row)
        if str((task.get("input_data") or {}).get("research_task_id") or "") == research_task_id:
            return task
    return None


def _editor_input_from_writer_task(writer_task: Dict[str, Any]) -> Dict[str, Any]:
    source = writer_task.get("input_data") or {}
    payload = {
        "workflow_route": source.get("workflow_route"),
        "route_tier": source.get("route_tier"),
        "topic_id": source.get("topic_id"),
        "candidate_id": source.get("candidate_id"),
        "title": source.get("title"),
        "primary_keyword": source.get("primary_keyword"),
        "secondary_keywords": source.get("secondary_keywords") if isinstance(source.get("secondary_keywords"), list) else [],
        "target_keywords": source.get("target_keywords") if isinstance(source.get("target_keywords"), list) else [],
        "search_intent": source.get("search_intent"),
        "content_type": source.get("content_type"),
        "content_angle": source.get("content_angle"),
        "writer_task_id": str(writer_task.get("id") or ""),
    }
    # Editor 只通过 writer_task_id 回读文章，不在 input_data 中复制整篇 article。
    payload.pop("article", None)
    payload.pop("write_result", None)
    payload.pop("content_md", None)
    return payload


def _find_existing_editor_task(conn, tasks: Table, *, writer_task_id: str) -> Optional[Dict[str, Any]]:
    rows = conn.execute(
        select(tasks).where(
            and_(
                tasks.c.agent_name == "EditorAgent",
                tasks.c.task_type == "edit_from_writer",
            )
        )
    ).mappings()
    for row in rows:
        task = _normalize_task_row(row)
        if str((task.get("input_data") or {}).get("writer_task_id") or "") == writer_task_id:
            return task
    return None


def _create_writer_task(conn, tasks: Table, research_task: Dict[str, Any]) -> str:
    existing = _find_existing_writer_task(conn, tasks, research_task_id=str(research_task.get("id") or ""))
    if existing:
        return str(existing.get("id") or "")

    writer_task_id = str(uuid4())
    conn.execute(
        insert(tasks).values(
            id=writer_task_id,
            agent_name="WriterAgent",
            task_type="rewrite_from_research",
            input_data=_writer_input_from_research_task(research_task),
            output_data=None,
            status="pending",
            error_message=None,
        )
    )
    return writer_task_id


def _create_editor_task(conn, tasks: Table, writer_task: Dict[str, Any]) -> str:
    existing = _find_existing_editor_task(conn, tasks, writer_task_id=str(writer_task.get("id") or ""))
    if existing:
        return str(existing.get("id") or "")

    editor_task_id = str(uuid4())
    conn.execute(
        insert(tasks).values(
            id=editor_task_id,
            agent_name="EditorAgent",
            task_type="edit_from_writer",
            input_data=_editor_input_from_writer_task(writer_task),
            output_data=None,
            status="pending",
            error_message=None,
        )
    )
    return editor_task_id


async def run_research_task(
    *,
    task_id: str,
    db_url: Optional[str] = None,
    research_config_path: str = "agents/research_agent/config.yaml",
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    from agents.research_agent import ResearchAgent

    engine = _get_engine(db_url)
    try:
        tasks = _tasks_table(engine)

        with engine.begin() as conn:
            task = _load_task(conn, tasks, task_id)
            if not task:
                raise ValueError(f"research_task_not_found:{task_id}")
            _set_task_state(conn, tasks, task_id=task_id, status="running", started=True, error_message=None)
            task = _load_task(conn, tasks, task_id)

        try:
            agent = ResearchAgent(config_path=research_config_path)
            input_data = (task or {}).get("input_data") or {}
            research_mode = mode or str(input_data.get("research_mode") or "mock")
            result = await agent.execute(topic=input_data, mode=research_mode)
        except Exception as exc:
            with engine.begin() as conn:
                _set_task_state(conn, tasks, task_id=task_id, status="failed", error_message=str(exc), completed=True)
            raise

        with engine.begin() as conn:
            _set_task_state(
                conn,
                tasks,
                task_id=task_id,
                status="completed",
                error_message=None,
                output_data=result if isinstance(result, dict) else {"raw": result},
                completed=True,
            )
            research_task = _load_task(conn, tasks, task_id)
            writer_task_id = _create_writer_task(conn, tasks, research_task or {"id": task_id, "input_data": {}})

        return {
            "research_task_id": task_id,
            "writer_task_id": writer_task_id,
            "output_data": result if isinstance(result, dict) else {"raw": result},
        }
    finally:
        engine.dispose()


def _blocked_output(*, research_task_id: str) -> Dict[str, Any]:
    return {
        "block_reason": "missing_research_brief",
        "research_task_id": research_task_id,
        "generated_at": _utcnow().isoformat(),
    }


def _editor_blocked_output(*, writer_task_id: str) -> Dict[str, Any]:
    return {
        "block_reason": "missing_writer_output",
        "writer_task_id": writer_task_id,
        "generated_at": _utcnow().isoformat(),
    }


def _topic_from_writer_input(input_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "workflow_route": input_data.get("workflow_route"),
        "route_tier": input_data.get("route_tier"),
        "topic_id": input_data.get("topic_id"),
        "candidate_id": input_data.get("candidate_id"),
        "title": input_data.get("title"),
        "primary_keyword": input_data.get("primary_keyword"),
        "secondary_keywords": input_data.get("secondary_keywords") if isinstance(input_data.get("secondary_keywords"), list) else [],
        "target_keywords": input_data.get("target_keywords") if isinstance(input_data.get("target_keywords"), list) else [],
        "search_intent": input_data.get("search_intent"),
        "content_type": input_data.get("content_type"),
        "content_angle": input_data.get("content_angle"),
    }


async def run_writer_task(
    *,
    task_id: str,
    db_url: Optional[str] = None,
    writer_config_path: str = "agents/writer_agent/config.yaml",
    writer_prompt_path: str = "agents/writer_agent/prompt.md",
    dry_run: bool = True,
) -> Dict[str, Any]:
    from agents.writer_agent import WriterAgent

    engine = _get_engine(db_url)
    try:
        tasks = _tasks_table(engine)

        blocked_result: Optional[Dict[str, Any]] = None
        with engine.begin() as conn:
            task = _load_task(conn, tasks, task_id)
            if not task:
                raise ValueError(f"writer_task_not_found:{task_id}")
            _set_task_state(conn, tasks, task_id=task_id, status="running", started=True, error_message=None)
            task = _load_task(conn, tasks, task_id)

            input_data = (task or {}).get("input_data") or {}
            research_task_id = str(input_data.get("research_task_id") or "")
            research_task = _load_task(conn, tasks, research_task_id) if research_task_id else None
            research_output = (research_task or {}).get("output_data") if isinstance((research_task or {}).get("output_data"), dict) else None
            research_brief = (research_output or {}).get("research_brief") if isinstance(research_output, dict) else None
            if not research_brief:
                _set_task_state(
                    conn,
                    tasks,
                    task_id=task_id,
                    status="writing_blocked",
                    error_message="missing_research_brief",
                    output_data=_blocked_output(research_task_id=research_task_id),
                    completed=True,
                )
                blocked = _load_task(conn, tasks, task_id) or {}
                blocked_result = {
                    "task_id": task_id,
                    "status": blocked.get("status"),
                    "error_message": blocked.get("error_message"),
                    "output_data": blocked.get("output_data"),
                }

        if blocked_result is not None:
            return blocked_result

        outline = None
        if isinstance(research_output, dict):
            outline = research_output.get("outline") or ((research_output.get("research_brief") or {}).get("writer_outline"))

        agent = WriterAgent(config_path=writer_config_path, prompt_path=writer_prompt_path)
        result = await agent.execute(
            topic=_topic_from_writer_input(input_data),
            outline=outline if isinstance(outline, dict) else None,
            materials=research_output if isinstance(research_output, dict) else {},
            brand_config={},
            dry_run=dry_run,
        )

        with engine.begin() as conn:
            _set_task_state(
                conn,
                tasks,
                task_id=task_id,
                status="completed",
                error_message=None,
                output_data=result if isinstance(result, dict) else {"raw": result},
                completed=True,
            )
            writer_task = _load_task(conn, tasks, task_id)
            editor_task_id = _create_editor_task(conn, tasks, writer_task or {"id": task_id, "input_data": {}})

        return {
            "task_id": task_id,
            "status": "completed",
            "editor_task_id": editor_task_id,
            "output_data": result if isinstance(result, dict) else {"raw": result},
        }
    finally:
        engine.dispose()


async def run_editor_task(
    *,
    task_id: str,
    db_url: Optional[str] = None,
    editor_config_path: str = "agents/editor_agent/config.yaml",
    editor_prompt_path: str = "agents/editor_agent/prompt.md",
    dry_run: bool = True,
) -> Dict[str, Any]:
    from agents.editor_agent import EditorAgent

    engine = _get_engine(db_url)
    try:
        tasks = _tasks_table(engine)

        blocked_result: Optional[Dict[str, Any]] = None
        with engine.begin() as conn:
            task = _load_task(conn, tasks, task_id)
            if not task:
                raise ValueError(f"editor_task_not_found:{task_id}")
            _set_task_state(conn, tasks, task_id=task_id, status="running", started=True, error_message=None)
            task = _load_task(conn, tasks, task_id)

            input_data = (task or {}).get("input_data") or {}
            writer_task_id = str(input_data.get("writer_task_id") or "")
            writer_task = _load_task(conn, tasks, writer_task_id) if writer_task_id else None
            writer_output = (writer_task or {}).get("output_data") if isinstance((writer_task or {}).get("output_data"), dict) else None
            article = (writer_output or {}).get("article") if isinstance(writer_output, dict) else None
            if not isinstance(article, dict) or not str(article.get("content_md") or article.get("content") or "").strip():
                _set_task_state(
                    conn,
                    tasks,
                    task_id=task_id,
                    status="editing_blocked",
                    error_message="missing_writer_output",
                    output_data=_editor_blocked_output(writer_task_id=writer_task_id),
                    completed=True,
                )
                blocked = _load_task(conn, tasks, task_id) or {}
                blocked_result = {
                    "task_id": task_id,
                    "status": blocked.get("status"),
                    "error_message": blocked.get("error_message"),
                    "output_data": blocked.get("output_data"),
                }

        if blocked_result is not None:
            return blocked_result

        agent = EditorAgent(config_path=editor_config_path, prompt_path=editor_prompt_path)
        result = await agent.execute(article=article, topic=_topic_from_writer_input(input_data), dry_run=dry_run)

        with engine.begin() as conn:
            _set_task_state(
                conn,
                tasks,
                task_id=task_id,
                status="completed",
                error_message=None,
                output_data=result if isinstance(result, dict) else {"raw": result},
                completed=True,
            )

        return {
            "task_id": task_id,
            "status": "completed",
            "output_data": result if isinstance(result, dict) else {"raw": result},
        }
    finally:
        engine.dispose()


def run_research_task_sync(**kwargs) -> Dict[str, Any]:
    return asyncio.run(run_research_task(**kwargs))


def run_writer_task_sync(**kwargs) -> Dict[str, Any]:
    return asyncio.run(run_writer_task(**kwargs))


def run_editor_task_sync(**kwargs) -> Dict[str, Any]:
    return asyncio.run(run_editor_task(**kwargs))
