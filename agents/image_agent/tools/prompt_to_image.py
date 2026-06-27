#!/usr/bin/env python3
"""
Prompt-to-Image Pipeline:
从 article_image_prompts 读取已生成的配图提示词，调用 Coze Site 生图，
下载到本地缓存，将结果写回 DB。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from agents.image_agent.tools.coze_image_provider import CozeImageProvider
from agents.scoring_agent.tools.article_score_writer import validate_identifier

DEFAULT_DATABASE = "research_article_data"
DEFAULT_PROMPT_TABLE = "article_image_prompts"


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _clean_db_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class PromptToImagePipeline:
    """读取配图提示词 → Coze 生图 → 下载 → 写回 DB"""

    def __init__(
        self,
        db_config: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
        cache_dir: str = "output/images/coze_cache",
    ):
        self.db_config = dict(db_config or {})
        self.host = self.db_config.get("host", "localhost")
        self.port = int(self.db_config.get("port", 3306))
        self.database = validate_identifier(self.db_config.get("database", DEFAULT_DATABASE))
        self.user = self.db_config.get("user", "root")
        self.password = self.db_config.get("password", "")
        self.prompt_table = validate_identifier(
            self.db_config.get("prompt_table", DEFAULT_PROMPT_TABLE)
        )
        self.jwt_token = jwt_token or os.environ.get("COZE_JWT_TOKEN", "")
        self.cache_dir = cache_dir
        self._conn = None

    async def _get_conn(self):
        if self._conn is None:
            import aiomysql
            self._conn = await aiomysql.connect(
                host=self.host, port=self.port, user=self.user,
                password=self.password, db=self.database,
                charset="utf8mb4", autocommit=False,
            )
        return self._conn

    async def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def ensure_generated_images_column(self) -> bool:
        """确保表中有 generated_images_json 列"""
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
            pass  # 列已存在
        return True

    async def fetch_prompts_needing_images(
        self, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """查找已生成提示词但尚未生图的记录"""
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
        """批量写回 generated_images_json"""
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
    """
    从 DB 读取配图提示词，调用 Coze 生图。

    Args:
        only_primary: True=只生成 primary_recommendation，False=生成所有风格
    """
    pipeline = PromptToImagePipeline(db_config=db_config, jwt_token=jwt_token)
    await pipeline.ensure_generated_images_column()

    if not pipeline.jwt_token:
        return {"success": False, "error": "COZE_JWT_TOKEN not configured"}

    sem = asyncio.Semaphore(max(1, int(concurrency)))
    provider = None

    try:
        rows = await pipeline.fetch_prompts_needing_images(limit=limit)

        async def process(row: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    prompts_json = row.get("image_prompts_json")
                    if isinstance(prompts_json, str):
                        prompts_json = json.loads(prompts_json)
                    if not isinstance(prompts_json, dict):
                        raise ValueError("invalid_prompts_json")

                    # 选择要生成图片的 prompt(s)
                    prompts_to_generate: List[Dict[str, Any]] = []
                    if only_primary:
                        rec = prompts_json.get("primary_recommendation")
                        if rec and rec.get("prompt"):
                            prompts_to_generate.append({
                                "slug": rec.get("slug", "recommended"),
                                "prompt": rec["prompt"],
                                "label": "primary",
                            })
                    else:
                        for s in (prompts_json.get("styles") or []):
                            if s.get("prompt"):
                                prompts_to_generate.append({
                                    "slug": s.get("slug", "unknown"),
                                    "prompt": s["prompt"],
                                    "label": s.get("slug", "style"),
                                })

                    if not prompts_to_generate:
                        raise ValueError("no_prompts_found")

                    # 逐个生图
                    images_result: List[Dict[str, Any]] = []
                    nonlocal provider
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
                            "prompt": p["prompt"][:200],
                        }
                        if gen.get("success") and gen.get("images"):
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
                    return {
                        "id": row["id"],
                        "generated_images_json": {
                            "images": [],
                            "error": str(exc),
                        },
                        "title": row.get("generated_title"),
                        "status": "failed",
                    }

        results = await asyncio.gather(*(process(r) for r in rows))

        write_result = await pipeline.write_generated_images(results)

        succeeded = sum(1 for r in results if r.get("status") == "generated")
        failed = len(results) - succeeded
        return {
            "success": failed == 0,
            "total": len(rows),
            "generated": succeeded,
            "failed": failed,
            "write_result": write_result,
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
        if provider:
            await provider.close()
        await pipeline.close()


# ---------------------------------------------------------------------------
# CrewAI Tool
# ---------------------------------------------------------------------------
def get_prompt_to_image_tool():
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
