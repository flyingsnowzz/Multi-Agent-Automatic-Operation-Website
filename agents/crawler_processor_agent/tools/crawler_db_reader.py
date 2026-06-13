"""
Crawler Database Reader Tool

从爬虫数据库读取待处理内容(status=pending)。
支持 MySQL、MongoDB 等多种数据库类型。
"""

import os
import asyncio
import re
from typing import Dict, List, Any, Optional
from crewai.tools import tool


class CrawlerDBReader:
    """爬虫数据库读取器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化爬虫数据库读取器。

        Args:
            config: 数据库配置，包含：
                - type: 数据库类型（mysql/mongodb/other）
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
        self.table = config.get("table", "") or config.get("collection", "")
        self.collection = config.get("collection", "") or self.table
        self.user = config.get("user", "")
        self.password = config.get("password", "")
        self.status_field = config.get("status_field", "status")
        self.pending_status = config.get("pending_status", "pending")
        self.field_mapping = config.get("field_mapping", {})
        self.charset = config.get("charset", "utf8mb4")

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
        self._client = None
        self._aiomysql = None

    def _validate_identifier(self, name: str) -> bool:
        if not isinstance(name, str) or not name:
            return False
        return re.fullmatch(r"[A-Za-z0-9_]+", name) is not None

    def _quote_mysql_ident(self, name: str) -> str:
        return f"`{name}`"

    async def _get_mysql_conn(self):
        """获取 MySQL 连接"""
        if self._conn is None:
            try:
                import aiomysql
            except Exception as e:
                raise RuntimeError("aiomysql_missing") from e
            self._aiomysql = aiomysql
            self._conn = await aiomysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db=self.database,
                charset=self.charset or "utf8mb4"
            )
        return self._conn

    async def _get_mongo_client(self):
        """获取 MongoDB 客户端"""
        if self._client is None:
            from motor.motor_asyncio import AsyncIOMotorClient
            uri = f"mongodb://{self.host}:{self.port}"
            self._client = AsyncIOMotorClient(uri)
        return self._client

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
            if self.db_type == "mysql":
                return await self._read_mysql_pending(limit, min_id, max_id)
            elif self.db_type == "mongodb":
                return await self._read_mongodb_pending(limit, min_id, max_id)
            else:
                return {
                    "success": False,
                    "error": f"不支持的数据库类型: {self.db_type}"
                }
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
        if not self._validate_identifier(self.table) or not self._validate_identifier(self.status_field):
            return {"success": False, "error": "invalid_identifier"}

        conn = await self._get_mysql_conn()
        aiomysql = self._aiomysql
        if aiomysql is None:
            try:
                import aiomysql as _aiomysql
            except Exception as e:
                return {"success": False, "error": "aiomysql_missing", "details": str(e)}
            aiomysql = _aiomysql

        # 合并字段映射
        mapping = {**self._default_mapping, **self.field_mapping}

        # 构造查询条件
        status_field = self._quote_mysql_ident(self.status_field)
        table = self._quote_mysql_ident(self.table)
        conditions = [f"{status_field} = %s"]
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
            FROM {table}
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

    async def _read_mongodb_pending(
        self,
        limit: int = 10,
        min_id: Optional[int] = None,
        max_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """读取 MongoDB 中 status=pending 的文档"""
        if not self.collection:
            return {"success": False, "error": "collection_missing"}
        client = await self._get_mongo_client()
        db = client[self.database]
        collection = db[self.collection]

        mapping = {**self._default_mapping, **self.field_mapping}

        def _to_object_id(v: Any) -> Any:
            if v is None:
                return None
            try:
                from bson import ObjectId
            except Exception:
                return None
            if isinstance(v, ObjectId):
                return v
            if isinstance(v, str):
                try:
                    return ObjectId(v)
                except Exception:
                    return None
            return None

        # 构造查询条件
        query = {self.status_field: self.pending_status}

        if min_id is not None:
            oid = _to_object_id(min_id)
            if oid is not None:
                query["_id"] = {"$gte": oid}

        if max_id is not None:
            oid = _to_object_id(max_id)
            if oid is not None:
                if "_id" not in query:
                    query["_id"] = {}
                query["_id"]["$lte"] = oid

        # 查询
        cursor = collection.find(query).sort("_id", 1).limit(limit)
        docs = await cursor.to_list(length=limit)

        normalized: List[Dict[str, Any]] = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            doc_id = doc.get("_id")
            norm_row = {
                "id": str(doc_id) if doc_id is not None else None,
                "title": doc.get(mapping["title"], ""),
                "content": doc.get(mapping["content"], ""),
                "source_url": doc.get(mapping["source_url"], ""),
                "published_at": doc.get(mapping["published_at"]),
                "author": doc.get(mapping["author"]),
                "category": doc.get(mapping["category"]),
                "spider_name": doc.get(mapping["spider_name"]),
                "raw_data": doc,
            }
            normalized.append(norm_row)

        return {
            "success": True,
            "data": normalized,
            "total": len(normalized)
        }

    async def _ensure_mysql_schema(self, conn):
        """确保 MySQL 数据库表存在 routing_payload 字段"""
        try:
            table = self._quote_mysql_ident(self.table)
            async with conn.cursor() as cursor:
                # 检查是否存在 routing_payload 字段
                query_check = f"SHOW COLUMNS FROM {table} LIKE 'routing_payload'"
                await cursor.execute(query_check)
                row = await cursor.fetchone()
                if not row:
                    try:
                        # 优先尝试 JSON 类型
                        await cursor.execute(f"ALTER TABLE {table} ADD COLUMN `routing_payload` JSON")
                        await conn.commit()
                    except Exception:
                        try:
                            # 降级尝试 TEXT 类型
                            await cursor.execute(f"ALTER TABLE {table} ADD COLUMN `routing_payload` TEXT")
                            await conn.commit()
                        except Exception:
                            pass
        except Exception:
            pass

    async def update_status(
        self,
        record_id: Any,
        new_status: str,
        error_message: Optional[str] = None,
        routing_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        更新记录状态。

        Args:
            record_id: 记录 ID
            new_status: 新状态(processed/discarded/ready_to_publish/ready_to_rewrite/error)
            error_message: 错误信息(可选,当 new_status=error 时使用)
            routing_payload: 分流负载元数据 JSON(可选)

        Returns:
            包含 success 的字典
        """
        try:
            if self.db_type == "mysql":
                return await self._update_mysql_status(record_id, new_status, error_message, routing_payload)
            elif self.db_type == "mongodb":
                return await self._update_mongodb_status(record_id, new_status, error_message, routing_payload)
            else:
                return {
                    "success": False,
                    "error": f"不支持的数据库类型: {self.db_type}"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _update_mysql_status(
        self,
        record_id: Any,
        new_status: str,
        error_message: Optional[str] = None,
        routing_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """更新 MySQL 记录状态"""
        if not self._validate_identifier(self.table) or not self._validate_identifier(self.status_field):
            return {"success": False, "error": "invalid_identifier"}

        conn = await self._get_mysql_conn()

        if routing_payload is not None:
            await self._ensure_mysql_schema(conn)

        # 构造更新字段
        status_field = self._quote_mysql_ident(self.status_field)
        table = self._quote_mysql_ident(self.table)
        update_fields = [f"{status_field} = %s"]
        params = [new_status]

        if error_message:
            update_fields.append("error_message = %s")
            params.append(error_message)

        if routing_payload is not None:
            import json
            update_fields.append("`routing_payload` = %s")
            params.append(json.dumps(routing_payload, ensure_ascii=False))

        params.append(record_id)  # WHERE id = %s

        update_clause = ", ".join(update_fields)

        query = f"""
            UPDATE {table}
            SET {update_clause}, updated_at = NOW()
            WHERE id = %s
        """

        async with conn.cursor() as cursor:
            await cursor.execute(query, params)
            await conn.commit()

        return {"success": True}

    async def _update_mongodb_status(
        self,
        record_id: Any,
        new_status: str,
        error_message: Optional[str] = None,
        routing_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """更新 MongoDB 文档状态"""
        if not self.collection:
            return {"success": False, "error": "collection_missing"}
        client = await self._get_mongo_client()
        db = client[self.database]
        collection = db[self.collection]

        def _to_object_id(v: Any) -> Any:
            try:
                from bson import ObjectId
            except Exception:
                return v
            if isinstance(v, ObjectId):
                return v
            if isinstance(v, str):
                try:
                    return ObjectId(v)
                except Exception:
                    return v
            return v

        update_doc: Dict[str, Any] = {"$set": {self.status_field: new_status}, "$currentDate": {"updated_at": True}}

        if error_message:
            update_doc["$set"]["error_message"] = error_message

        if routing_payload is not None:
            update_doc["$set"]["routing_payload"] = routing_payload

        await collection.update_one(
            {"_id": _to_object_id(record_id)},
            update_doc
        )

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
    record_id: Any,
    new_status: str,
    error_message: Optional[str] = None,
    routing_payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    更新爬虫数据库记录状态的便捷函数。

    Args:
        config: 数据库配置
        record_id: 记录 ID
        new_status: 新状态
        error_message: 错误信息(可选)
        routing_payload: 分流负载元数据 JSON(可选)

    Returns:
        包含 success 的字典
    """
    reader = CrawlerDBReader(config)
    return await reader.update_status(record_id, new_status, error_message, routing_payload)
