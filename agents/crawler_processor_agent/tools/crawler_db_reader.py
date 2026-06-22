"""
Crawler Database Reader Tool

从 MySQL 爬虫结果库读取待处理内容(status=pending)。
"""

import os
import asyncio
from typing import Dict, List, Any, Optional

try:
    from crewai.tools import tool
except Exception:
    def tool(func):
        return func


class CrawlerDBReader:
    """爬虫数据库读取器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化爬虫数据库读取器。

        Args:
            config: 数据库配置，包含：
                - type: 数据库类型（当前使用 mysql）
                - host: 主机地址
                - port: 端口
                - database: 数据库名
                - table: 表名/集合名
                - user: 用户名（可选）
                - password: 密码（可选）
                - status_field: 状态字段名
                - pending_status: 待处理状态值
                - field_mapping: 字段映射（用于兼容杂乱的爬虫字段）
                  - title: 标题字段
                  - content: 正文字段
                  - source_url: 来源URL字段
                  - published_at: 发布时间字段
                  - author: 作者字段
                  - category: 分类字段
                  - spider_name: 爬虫名称字段
        """
        self.config = config
        self.db_type = config.get("type", "mysql")
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 3306)
        self.database = config.get("database", "")
        self.table = config.get("table", "")
        self.user = config.get("user", "")
        self.password = config.get("password", "")
        self.status_field = config.get("status_field", "status")
        self.pending_status = config.get("pending_status", "pending")
        self.field_mapping = config.get("field_mapping", {})

        # 默认字段映射（如果未配置）
        self._default_mapping = {
            "title": "title",
            "content": "content",
            "source_url": "source_url",
            "published_at": "published_at",
            "author": "author",
            "category": "category",
            "spider_name": "spider_name"
        }

        # 延迟初始化数据库连接
        self._conn = None

    async def _get_mysql_conn(self):
        """获取 MySQL 连接"""
        if self._conn is None:
            import aiomysql
            self._conn = await aiomysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db=self.database,
                charset="utf8mb4"
            )
        return self._conn

    async def read_pending(
        self,
        limit: int = 10,
        min_id: Optional[int] = None,
        max_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        读取待处理内容(status=pending)。

        Args:
            limit: 最大读取记录数,默认 10
            min_id: 最小 ID(用于分页),可选
            max_id: 最大 ID(用于分页),可选

        Returns:
            包含 success, data, total 的字典
        """
        try:
            if self.db_type != "mysql":
                return {
                    "success": False,
                    "error": f"当前仅支持 MySQL 爬虫结果库，不支持: {self.db_type}"
                }
            return await self._read_mysql_pending(limit, min_id, max_id)
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _read_mysql_pending(
        self,
        limit: int = 10,
        min_id: Optional[int] = None,
        max_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """读取 MySQL 中 status=pending 的记录"""
        import aiomysql

        conn = await self._get_mysql_conn()

        # 合并字段映射
        mapping = {**self._default_mapping, **self.field_mapping}

        # 构造查询条件
        conditions = [f"{self.status_field} = %s"]
        params = [self.pending_status]

        if min_id is not None:
            conditions.append("id >= %s")
            params.append(min_id)

        if max_id is not None:
            conditions.append("id <= %s")
            params.append(max_id)

        where_clause = " AND ".join(conditions)

        # 查询 SQL（SELECT * 以支持杂乱的爬虫字段）
        query = f"""
            SELECT * 
            FROM {self.table}
            WHERE {where_clause}
            ORDER BY id ASC
            LIMIT %s
        """
        params.append(limit)

        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()

        # 标准化字段名（将数据库字段名映射为统一字段名）
        # 兼容爬虫内容杂乱的字段结构
        normalized = []
        for row in rows:
            norm_row = {
                "id": row.get("id"),
                "title": row.get(mapping["title"], ""),
                "content": row.get(mapping["content"], ""),
                "source_url": row.get(mapping["source_url"], ""),
                "published_at": row.get(mapping["published_at"]),
                "author": row.get(mapping["author"]),
                "category": row.get(mapping["category"]),
                "spider_name": row.get(mapping["spider_name"]),
                "raw_data": row  # 保留原始数据
            }
            normalized.append(norm_row)

        return {
            "success": True,
            "data": normalized,
            "total": len(normalized)
        }

    async def update_status(
        self,
        record_id: int,
        new_status: str,
        error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        更新记录状态。

        Args:
            record_id: 记录 ID
            new_status: 新状态(processed/discarded/ready_to_publish/ready_to_rewrite/error)
            error_message: 错误信息(可选,当 new_status=error 时使用)

        Returns:
            包含 success 的字典
        """
        try:
            if self.db_type != "mysql":
                return {
                    "success": False,
                    "error": f"当前仅支持 MySQL 爬虫结果库，不支持: {self.db_type}"
                }
            return await self._update_mysql_status(record_id, new_status, error_message)
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _update_mysql_status(
        self,
        record_id: int,
        new_status: str,
        error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """更新 MySQL 记录状态"""
        conn = await self._get_mysql_conn()

        # 构造更新字段
        update_fields = [f"{self.status_field} = %s"]
        params = [new_status]

        if error_message:
            update_fields.append("error_message = %s")
            params.append(error_message)

        params.append(record_id)  # WHERE id = %s

        update_clause = ", ".join(update_fields)

        query = f"""
            UPDATE {self.table}
            SET {update_clause}, updated_at = NOW()
            WHERE id = %s
        """

        async with conn.cursor() as cursor:
            await cursor.execute(query, params)
            await conn.commit()

        return {"success": True}


# CrewAI Tool 包装函数
@tool
async def get_crawler_db_reader_tool(config: Dict[str, Any]) -> CrawlerDBReader:
    """
    获取爬虫数据库读取器工具。

    Args:
        config: 数据库配置字典

    Returns:
        CrawlerDBReader 实例
    """
    return CrawlerDBReader(config)


# 异步工具函数(供 Agent 直接调用)
async def read_crawler_pending(
    config: Dict[str, Any],
    limit: int = 10,
    min_id: Optional[int] = None,
    max_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    读取爬虫数据库中待处理内容的便捷函数。

    Args:
        config: 数据库配置
        limit: 最大读取记录数
        min_id: 最小 ID(分页)
        max_id: 最大 ID(分页)

    Returns:
        包含 success, data, total 的字典
    """
    reader = CrawlerDBReader(config)
    return await reader.read_pending(limit, min_id, max_id)


async def update_crawler_status(
    config: Dict[str, Any],
    record_id: int,
    new_status: str,
    error_message: Optional[str] = None
) -> Dict[str, Any]:
    """
    更新爬虫数据库记录状态的便捷函数。

    Args:
        config: 数据库配置
        record_id: 记录 ID
        new_status: 新状态
        error_message: 错误信息(可选)

    Returns:
        包含 success 的字典
    """
    reader = CrawlerDBReader(config)
    return await reader.update_status(record_id, new_status, error_message)


if __name__ == "__main__":
    # 测试代码
    import asyncio

    # MySQL 配置(示例)
    mysql_config = {
        "type": "mysql",
        "host": "localhost",
        "port": 3306,
        "database": "crawler_db",
        "table": "crawled_content",
        "user": "root",
        "password": "password",
        "status_field": "status",
        "pending_status": "pending"
    }

    async def test_mysql():
        result = await read_crawler_pending(mysql_config, limit=10)
        print(f"Read {result.get('total', 0)} items")
        for item in result.get("data", []):
            # 标准化后的字段包含：id, title, content, source_url, published_at, author, category, spider_name, raw_data
            print(f"  [{item['id']}] {item['title'][:50]}... (spider: {item.get('spider_name', 'unknown')})")

    # 运行测试
    # asyncio.run(test_mysql())
    print("Test code commented out. Use direct function calls in Agent.")
