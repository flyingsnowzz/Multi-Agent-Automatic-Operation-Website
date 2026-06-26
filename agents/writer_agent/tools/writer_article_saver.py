"""
WriterAgent 文章存库工具
- 将生成的文章写入 writer_article_outputs 表 (MySQL)
- 无 MySQL 时自动降级为文件存储
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(str(value or "")):
        raise ValueError("invalid_identifier")
    return value


class WriterArticleSaver:
    """保存 WriterAgent 生成的文章。优先 MySQL，降级为 JSON 文件。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.host = self.config.get("host") or os.environ.get("MYSQL_HOST", "localhost")
        self.port = int(self.config.get("port") or os.environ.get("MYSQL_PORT", 3306))
        self.database = self.config.get("database") or "research_article_data"
        self.user = self.config.get("user") or os.environ.get("MYSQL_USER", "")
        self.password = self.config.get("password") or os.environ.get("MYSQL_PASSWORD", "")
        self._conn = None
        self._file_dir = Path(
            self.config.get("file_dir")
            or os.environ.get("WRITER_OUTPUT_DIR")
            or "output/writer_articles"
        )
        self._file_dir.mkdir(parents=True, exist_ok=True)

    async def _get_conn(self):
        if self._conn is None:
            try:
                import aiomysql
                self._conn = await aiomysql.connect(
                    host=self.host, port=self.port, user=self.user,
                    password=self.password, db=self.database, charset="utf8mb4",
                )
            except Exception:
                self._conn = None
        return self._conn

    async def save(self, article_data: Dict[str, Any]) -> Dict[str, Any]:
        """保存文章。先尝试 MySQL，失败则存文件。"""
        result = {"success": False, "method": "none", "error": None}

        # Try MySQL
        try:
            conn = await self._get_conn()
            if conn:
                await self._save_mysql(conn, article_data)
                result["success"] = True
                result["method"] = "mysql"
                return result
        except Exception as e:
            result["error"] = str(e)

        # Fallback to file
        try:
            path = self._save_file(article_data)
            result["success"] = True
            result["method"] = "file"
            result["path"] = str(path)
        except Exception as e:
            result["error"] = result.get("error") or str(e)

        return result

    async def _save_mysql(self, conn, data: Dict[str, Any]):
        import aiomysql
        query = """
            INSERT INTO writer_article_outputs
            (candidate_id, source_article_id, original_url, source_title, article_score,
             writer_prompt, writer_model, generation_status, generated_title,
             generated_meta_description, generated_content_md, generated_article_json,
             quality_checks, warnings, generated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            data.get("candidate_id") or 0,
            data.get("source_article_id"),
            data.get("original_url") or "",
            data.get("source_title") or "",
            data.get("article_score") or 0,
            data.get("writer_prompt") or "",
            data.get("writer_model") or "deepseek-chat",
            "generated",
            data.get("generated_title") or data.get("title") or "",
            data.get("generated_meta_description") or data.get("meta_description") or "",
            data.get("generated_content_md") or data.get("content") or "",
            json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else "{}",
            json.dumps(data.get("quality_checks") or {}, ensure_ascii=False),
            json.dumps(data.get("warnings") or [], ensure_ascii=False),
            datetime.now(),
        )
        async with conn.cursor() as cursor:
            await cursor.execute(query, params)
            await conn.commit()

    def _save_file(self, data: Dict[str, Any]) -> Path:
        aid = data.get("source_article_id") or data.get("article_id") or "unknown"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"article_{aid}_{ts}.json"
        path = self._file_dir / fname
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path


async def save_writer_article(article_data: Dict[str, Any], config: Optional[Dict] = None) -> Dict[str, Any]:
    """便捷函数：保存 WriterAgent 生成的文章"""
    saver = WriterArticleSaver(config)
    return await saver.save(article_data)
