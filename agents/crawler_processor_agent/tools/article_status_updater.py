#!/usr/bin/env python3
"""
文章状态更新工具。
当 original_url 抓取失败时标记文章 is_del=1。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False

logger = logging.getLogger(__name__)


class ArticleStatusUpdater:
    """更新 crawler_news_main 的文章状态。"""

    def __init__(self, db_config: Dict[str, Any]):
        if not HAS_PYMYSQL:
            raise ImportError("pymysql 未安装")
        self.db_config = db_config

    def _connect(self):
        return pymysql.connect(
            host=self.db_config.get("host", "localhost"),
            port=self.db_config.get("port", 3306),
            user=self.db_config.get("user", "root"),
            password=self.db_config.get("password", ""),
            database=self.db_config.get("database", "final_test"),
            charset="utf8mb4",
        )

    def mark_deleted(self, article_id: int, reason: str = "original_url_fetch_failed") -> bool:
        """标记文章为已删除（原文 URL 失效）。"""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE crawler_news_main SET is_del = 1 WHERE id = %s",
                    (article_id,),
                )
                conn.commit()
            logger.info(f"Marked article {article_id} as deleted: {reason}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark article {article_id}: {e}")
            return False
        finally:
            conn.close()
