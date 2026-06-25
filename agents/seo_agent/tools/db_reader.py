#!/usr/bin/env python3
"""
数据库读取工具 — 从 writer_article_outputs 表读取 WriterAgent 生成的文章。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False


@dataclass
class ArticleRecord:
    id: int
    candidate_id: int
    source_title: Optional[str]
    article_score: float
    generated_title: Optional[str]
    generated_meta_description: Optional[str]
    generated_content_md: Optional[str]
    generated_article_json: Optional[Dict[str, Any]]
    generation_status: str
    generated_at: Optional[datetime]


def _build_db_config() -> Dict[str, Any]:
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": os.environ.get("DB_USER", "root"),
        "password": os.environ.get("DB_PASSWORD", ""),
        "database": os.environ.get("DB_NAME", "research_article_data"),
    }


class ArticleDBReader:
    """从 writer_article_outputs 表读取已生成的文章。"""

    def __init__(self, db_config: Optional[Dict[str, Any]] = None):
        if not HAS_PYMYSQL:
            raise ImportError("pymysql 未安装，请运行: pip install pymysql")
        self.db_config = db_config or _build_db_config()

    def _connect(self):
        return pymysql.connect(
            host=self.db_config["host"],
            port=self.db_config["port"],
            user=self.db_config["user"],
            password=self.db_config["password"],
            database=self.db_config["database"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )

    def fetch_generated(
        self,
        *,
        limit: int = 10,
        min_score: Optional[float] = None,
        candidate_id: Optional[int] = None,
    ) -> List[ArticleRecord]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                where = ["generation_status = 'generated'", "generated_content_md IS NOT NULL"]
                params: List[Any] = []
                if min_score is not None:
                    where.append("article_score >= %s")
                    params.append(min_score)
                if candidate_id is not None:
                    where.append("candidate_id = %s")
                    params.append(candidate_id)

                sql = (
                    "SELECT id, candidate_id, source_title, article_score, "
                    "generated_title, generated_meta_description, generated_content_md, "
                    "generated_article_json, generation_status, generated_at "
                    "FROM writer_article_outputs "
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY article_score DESC, generated_at DESC "
                    "LIMIT %s"
                )
                params.append(limit)
                cur.execute(sql, params)
                rows = cur.fetchall()

            return [ArticleRecord(
                id=r["id"],
                candidate_id=r["candidate_id"],
                source_title=r.get("source_title"),
                article_score=float(r["article_score"] or 0),
                generated_title=r.get("generated_title"),
                generated_meta_description=r.get("generated_meta_description"),
                generated_content_md=r.get("generated_content_md"),
                generated_article_json=r.get("generated_article_json") if isinstance(r.get("generated_article_json"), dict) else None,
                generation_status=r["generation_status"],
                generated_at=r.get("generated_at"),
            ) for r in rows]
        finally:
            conn.close()

    def fetch_by_id(self, article_id: int) -> Optional[ArticleRecord]:
        records = self.fetch_generated(limit=1)
        for r in records:
            if r.id == article_id:
                return r
        return None
