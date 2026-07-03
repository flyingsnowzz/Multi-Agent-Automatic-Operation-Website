#!/usr/bin/env python3
"""
Prompt-to-Image Pipeline:
从 article_image_prompts 读取已生成的配图提示词，调用 Coze Site 生图，
下载到本地缓存，将结果写回 DB。

设计说明：
    这是配图流水线的第二阶段（提示词 → 实际图片），流程为：
      数据库(article_image_prompts, 状态=generated 且无图片)
        → 读取 image_prompts_json 中的 prompt
        → 调 Coze Site 生图
        → 下载到本地缓存
        → 写回 generated_images_json 列

    与第一阶段（image_prompt_generator）的关系：
      第一阶段负责「想」：用 DeepSeek 分析文章 → 生成提示词
      第二阶段负责「做」：拿提示词 → 调生图 API → 产出实际图片
    两阶段解耦，可独立运行、独立重试。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from agents.image_agent.tools.coze_image_provider import CozeImageProvider
# 复用 scoring_agent 的标识符校验，防止 SQL 注入
from agents.scoring_agent.tools.article_score_writer import validate_identifier

DEFAULT_DATABASE = "research_article_data"
DEFAULT_PROMPT_TABLE = "article_image_prompts"


def _json_dumps(value: Any) -> Optional[str]:
    """安全序列化 JSON，None 透传"""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _clean_db_value(value: Any) -> Any:
    """清洗数据库返回值：Decimal→float，datetime→ISO 字符串"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class PromptToImagePipeline:
    """读取配图提示词 → Coze 生图 → 下载 → 写回 DB

    该类封装了第二阶段的完整数据库交互逻辑：
      - 确保表中有 generated_images_json 列（自动迁移）
      - 查询待生图的记录
      - 写回生图结果
    实际生图调用委托给 CozeImageProvider。
    """

    def __init__(
        self,
        db_config: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
        cache_dir: str = "output/images/coze_cache",
    ):
        self.db_config = dict(db_config or {})
        self.host = self.db_config.get("host", "localhost")
        self.port = int(self.db_config.get("port", 3306))
        # 表名/库名用 validate_identifier 校验，防止 SQL 注入
        self.database = validate_identifier(self.db_config.get("database", DEFAULT_DATABASE))
        self.user = self.db_config.get("user", "root")
        self.password = self.db_config.get("password", "")
        self.prompt_table = validate_identifier(
            self.db_config.get("prompt_table", DEFAULT_PROMPT_TABLE)
        )
        # Coze JWT Token：构造参数 > 环境变量
        self.jwt_token = jwt_token or os.environ.get("COZE_JWT_TOKEN", "")
        self.cache_dir = cache_dir
        self._conn = None  # 惰性连接

    async def _get_conn(self):
        """获取数据库连接（惰性创建，复用同一连接）"""
        if self._conn is None:
            import aiomysql
            self._conn = await aiomysql.connect(
                host=self.host, port=self.port, user=self.user,
                password=self.password, db=self.database,
                charset="utf8mb4", autocommit=False,
            )
        return self._conn

    async def close(self):
        """关闭数据库连接"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def ensure_generated_images_column(self) -> bool:
        """确保表中有 generated_images_json 列（自动迁移）

        第一阶段建表时没有这列，第二阶段首次运行时自动添加。
        若列已存在则 ALTER 会报错，此处用 try/except 忽略。
        """
        conn = await self._get_conn()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"ALTER TABLE `{self.prompt_table}` "
                    "ADD COLUMN generated_images_json JSON NULL "
                    "COMMENT 'Coze生成图片结果(含local_path/url/run_id)' "
                    "AFTER image_prompts_json"
                )
                await conn.commit()
        except Exception:
            # 列已存在时 ALTER 会失败，忽略即可
            pass
        return True

    async def fetch_prompts_needing_images(
        self, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """查找已生成提示词但尚未生图的记录

        筛选条件：
          - generation_status = 'generated'（提示词已成功生成）
          - generated_images_json 为 NULL 或 JSON NULL（尚未生图）
        """
        import aiomysql
        conn = await self._get_conn()
        query = f"""
            SELECT id, output_id, candidate_id,
                   generated_title, source_title,
                   image_prompts_json
            FROM `{self.prompt_table}`
            WHERE generation_status = 'generated'
              AND (generated_images_json IS NULL
                   OR JSON_TYPE(generated_images_json) = 'NULL')
            ORDER BY id ASC
            LIMIT %s
        """
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, (max(1, int(limit)),))
            rows = await cursor.fetchall()
        return [{k: _clean_db_value(v) for k, v in row.items()} for row in rows]

    async def write_generated_images(
        self, records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """批量写回 generated_images_json

        Args:
            records: 每项含 id 和 generated_images_json

        Returns:
            {success: True, updated: 影响行数}
        """
        if not records:
            return {"success": True, "updated": 0}
        conn = await self._get_conn()
        query = f"""
            UPDATE `{self.prompt_table}`
            SET generated_images_json = CAST(%s AS JSON)
            WHERE id = %s
        """
        params = [
            (_json_dumps(r.get("generated_images_json")), r.get("id"))
            for r in records
        ]
        async with conn.cursor() as cursor:
            await cursor.executemany(query, params)
            await conn.commit()
            return {"success": True, "updated": cursor.rowcount}


async def generate_images_from_prompts(
    db_config: Optional[Dict[str, Any]] = None,
    jwt_token: Optional[str] = None,
    *,
    limit: int = 5,
    concurrency: int = 1,
    only_primary: bool = True,
) -> Dict[str, Any]:
    """从 DB 读取配图提示词，调用 Coze 生图

    完整流程：
      1. 确保表有 generated_images_json 列
      2. 查询待生图记录
      3. 逐条解析 image_prompts_json，取出 prompt(s)
      4. 调 CozeImageProvider 生图 + 下载
      5. 写回 generated_images_json

    Args:
        db_config: 数据库配置
        jwt_token: Coze JWT Token
        limit: 最多处理文章数
        concurrency: 并发数（建议 1，Coze 单请求较重）
        only_primary: True=只生成 primary_recommendation，False=生成所有风格

    Returns:
        {success, total, generated, failed, write_result, samples}
    """
    pipeline = PromptToImagePipeline(db_config=db_config, jwt_token=jwt_token)
    # 自动迁移：确保表中有 generated_images_json 列
    await pipeline.ensure_generated_images_column()

    # JWT 未配置直接返回失败
    if not pipeline.jwt_token:
        return {"success": False, "error": "COZE_JWT_TOKEN not configured"}

    # 信号量限流
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    provider = None  # 惰性创建 CozeImageProvider，所有任务复用同一个

    try:
        # 1. 查询待生图记录
        rows = await pipeline.fetch_prompts_needing_images(limit=limit)

        # 2. 逐条处理
        async def process(row: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    # 解析 image_prompts_json（DB 中可能是字符串或已解析的 dict）
                    prompts_json = row.get("image_prompts_json")
                    if isinstance(prompts_json, str):
                        prompts_json = json.loads(prompts_json)
                    if not isinstance(prompts_json, dict):
                        raise ValueError("invalid_prompts_json")

                    # 3. 选择要生成图片的 prompt(s)
                    prompts_to_generate: List[Dict[str, Any]] = []
                    if only_primary:
                        # 仅生成主推荐风格的图片
                        rec = prompts_json.get("primary_recommendation")
                        if rec and rec.get("prompt"):
                            prompts_to_generate.append({
                                "slug": rec.get("slug", "recommended"),
                                "prompt": rec["prompt"],
                                "label": "primary",
                            })
                    else:
                        # 生成所有风格的图片
                        for s in (prompts_json.get("styles") or []):
                            if s.get("prompt"):
                                prompts_to_generate.append({
                                    "slug": s.get("slug", "unknown"),
                                    "prompt": s["prompt"],
                                    "label": s.get("slug", "style"),
                                })

                    if not prompts_to_generate:
                        raise ValueError("no_prompts_found")

                    # 4. 逐个 prompt 调 Coze 生图
                    images_result: List[Dict[str, Any]] = []
                    nonlocal provider
                    # 惰性初始化 CozeImageProvider（所有任务复用）
                    if provider is None:
                        provider = CozeImageProvider(
                            jwt_token=pipeline.jwt_token,
                            cache_dir=pipeline.cache_dir,
                        )

                    for p in prompts_to_generate:
                        gen = await provider.generate(prompt=p["prompt"], n=1)
                        img_info = {
                            "style_slug": p["slug"],
                            "label": p["label"],
                            "prompt": p["prompt"][:200],  # 截断存储
                        }
                        if gen.get("success") and gen.get("images"):
                            # 取第一张图（Coze 单次返回一张）
                            img0 = gen["images"][0]
                            img_info.update({
                                "coze_url": img0.get("url", ""),
                                "local_path": img0.get("local_path", ""),
                                "run_id": img0.get("run_id", ""),
                                "status": "generated",
                            })
                        else:
                            img_info["status"] = "failed"
                            img_info["error"] = gen.get("error", "unknown")
                        images_result.append(img_info)

                    return {
                        "id": row["id"],
                        "generated_images_json": {
                            "images": images_result,
                            "generated_at": datetime.now().isoformat(),
                        },
                        "title": row.get("generated_title"),
                        "status": "generated",
                    }
                except Exception as exc:
                    # 失败也写回，记录错误信息
                    return {
                        "id": row["id"],
                        "generated_images_json": {
                            "images": [],
                            "error": str(exc),
                        },
                        "title": row.get("generated_title"),
                        "status": "failed",
                    }

        # 并发执行所有记录处理
        results = await asyncio.gather(*(process(r) for r in rows))

        # 5. 批量写回 DB
        write_result = await pipeline.write_generated_images(results)

        # 统计结果
        succeeded = sum(1 for r in results if r.get("status") == "generated")
        failed = len(results) - succeeded
        return {
            "success": failed == 0,
            "total": len(rows),
            "generated": succeeded,
            "failed": failed,
            "write_result": write_result,
            # 返回前 3 个成功样本，便于人工抽检
            "samples": [
                {
                    "title": r.get("title", ""),
                    "images": [
                        {
                            "slug": img.get("style_slug"),
                            "status": img.get("status"),
                            "local_path": img.get("local_path", ""),
                        }
                        for img in (r.get("generated_images_json") or {}).get("images", [])
                    ],
                }
                for r in results[:3] if r.get("status") == "generated"
            ],
        }
    finally:
        # 确保资源释放
        if provider:
            await provider.close()
        await pipeline.close()


# ---------------------------------------------------------------------------
# CrewAI Tool
# ---------------------------------------------------------------------------
def get_prompt_to_image_tool():
    """返回 CrewAI 可用的 Tool

    将 generate_images_from_prompts 包装成 CrewAI @tool。
    """
    from crewai.tools import tool

    @tool("prompt_to_image_generator")
    def prompt_to_image_tool(limit: int = 5, only_primary: bool = True) -> str:
        """
        从 article_image_prompts 读取配图提示词，调用 Coze 生成实际图片。
        Args:
            limit: 最多处理文章数
            only_primary: True=只生成推荐风格，False=生成全部风格
        """
        async def _run():
            return await generate_images_from_prompts(
                limit=int(limit) if limit else 5,
                only_primary=bool(only_primary),
                concurrency=1,
            )
        result = asyncio.run(_run())
        return json.dumps(result, ensure_ascii=False, indent=2)

    return prompt_to_image_tool


# ---------------------------------------------------------------------------
# CLI 测试
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def test():
        print("=" * 60)
        print("Prompt→Image Pipeline 测试")
        print("=" * 60)
        result = await generate_images_from_prompts(limit=2, only_primary=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(test())
