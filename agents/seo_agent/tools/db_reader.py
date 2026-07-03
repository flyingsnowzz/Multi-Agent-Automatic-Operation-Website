#!/usr/bin/env python3
"""
数据库读取工具 — 从 writer_article_outputs 表读取 WriterAgent 生成的文章。
"""

# 这个文件的作用很单一：
# - 它不做 SEO 分析
# - 只负责把 WriterAgent 已生成的文章从数据库里读出来
# - 然后供 `SEOAgent.execute_from_db()` 批量处理

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
    """数据库记录的内存表示。

    dataclass 的好处是字段清晰，后续在代码里访问属性时更直观。
    """
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
    """从环境变量组装数据库配置。"""
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
        """初始化数据库读取器。"""
        if not HAS_PYMYSQL:
            raise ImportError("pymysql 未安装，请运行: pip install pymysql")
        self.db_config = db_config or _build_db_config()

    def _connect(self):
        """建立 MySQL 连接。"""
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
        """读取已生成文章列表。

        过滤条件：
        - generation_status = 'generated'
        - generated_content_md 非空
        - 可选最小分数 / candidate_id
        """
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
        """按文章 ID 查询单条记录。

        当前实现是复用 `fetch_generated(limit=1)` 的简化写法；
        如果后续数据量变大，建议改成直接 `WHERE id = %s` 查询。
        """
        records = self.fetch_generated(limit=1)
        for r in records:
            if r.id == article_id:
                return r
        return None
