#!/usr/bin/env python3
"""
Image Prompt Generator - 读取通过质量审核的文章，调用 DeepSeek 分析内容并生成配图提示词。
自动根据文章内容选择合适的视觉风格，返回 JSON 存入数据库。

设计说明：
    这是配图流水线的第一阶段（提示词生成），流程为：
      数据库(writer_article_outputs) → 质量筛选(>=85分) → DeepSeek 分析 → 生成多风格提示词 → 写回(article_image_prompts)

    DeepSeek 的职责：
      - 分析文章情绪、目标受众、场景关键词
      - 从 8 种预设视觉风格中选出 5-6 种最适合的
      - 为每种风格生成英文 AI 绘画提示词（80-150 词）
      - 给出主推荐风格 + 对应 prompt + 中文 alt_text

    并发控制：
      使用 asyncio.Semaphore 限制并发数（默认 2），避免 DeepSeek API 过载。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

# 复用 scoring_agent 的标识符校验工具，防止 SQL 注入（表名/库名不能参数化）
from agents.scoring_agent.tools.article_score_writer import validate_identifier

# 默认常量
DEFAULT_DATABASE = "research_article_data"           # 默认数据库名
DEFAULT_PROMPT_TABLE = "article_image_prompts"        # 存储配图提示词的表名
DEFAULT_MODEL = "deepseek-chat"                       # DeepSeek 默认模型
DEFAULT_BASE_URL = "https://api.deepseek.com"         # DeepSeek API 地址
DEFAULT_MIN_QUALITY_SCORE = 85.0                      # 文章最低质量分阈值


# ---------------------------------------------------------------------------
# 可选视觉风格
# DeepSeek 会从这个列表中为文章挑选 5-6 种最合适的风格
# ---------------------------------------------------------------------------
VISUAL_STYLES = [
    {"slug": "realistic", "zh": "写实摄影风", "desc": "逼真的摄影质感，自然光线，真实场景感"},
    {"slug": "cartoon", "zh": "卡通插画风", "desc": "活泼可爱的卡通插画，色彩明亮，线条圆润"},
    {"slug": "illustration", "zh": "扁平矢量风", "desc": "现代扁平矢量插画，简洁图形，高饱和度配色"},
    {"slug": "cinematic", "zh": "电影级写实风", "desc": "电影镜头感，景深明显，光影戏剧化"},
    {"slug": "minimalist", "zh": "极简抽象风", "desc": "极简构图，抽象几何元素，大量留白"},
    {"slug": "watercolor", "zh": "水彩手绘风", "desc": "清新水彩画，柔和过渡，文艺感"},
    {"slug": "3d_render", "zh": "3D渲染风", "desc": "3D建模质感，材质细腻，现代科技感"},
    {"slug": "chinese_ink", "zh": "中国水墨风", "desc": "中国传统水墨画风格，意境悠远"},
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _json_dumps(value: Any) -> Optional[str]:
    """安全序列化 JSON，None 透传"""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _clean_db_value(value: Any) -> Any:
    """清洗数据库返回值：Decimal→float，datetime→ISO 字符串，其余原样"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _extract_json(text: Any) -> Dict[str, Any]:
    """从 LLM 输出中提取 JSON 对象

    处理两种常见情况：
      1. 输出被 ``` 代码块包裹 → 提取第一个 { 到最后一个 } 之间内容
      2. 含控制字符 → 清除后解析
    """
    raw = str(text or "").strip()
    if "```" in raw:
        # 代码块包裹的情况，提取花括号范围
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 清除控制字符后重试
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
        return json.loads(cleaned)


def _text_summary(text: Any, max_chars: int = 2500) -> str:
    """截取文章正文前 max_chars 字符（控制发给 LLM 的 token 量）"""
    content = str(text or "")
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n…（后续内容已省略）"


def _build_style_table() -> str:
    """把 VISUAL_STYLES 渲染成 Markdown 表格，嵌入 LLM prompt 供选择"""
    lines = ["| slug | 中文名 | 风格描述 |", "|------|--------|----------|"]
    for s in VISUAL_STYLES:
        lines.append(f"| {s['slug']} | {s['zh']} | {s['desc']} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DeepSeek 配置
# ---------------------------------------------------------------------------
@dataclass
class PromptLLMConfig:
    """DeepSeek 提示词生成的配置（从环境变量读取）"""
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.8   # 较高温度增加风格多样性
    timeout: int = 90          # LLM 响应较慢，90s 超时

    @classmethod
    def from_env(cls) -> "PromptLLMConfig":
        """从环境变量构建配置

        API Key 读取优先级：IMAGE_PROMPT_API_KEY > DEEPSEEK_API_KEY > OPENAI_API_KEY
        """
        api_key = (
            os.getenv("IMAGE_PROMPT_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        model = (os.getenv("IMAGE_PROMPT_MODEL") or DEFAULT_MODEL).strip()
        base_url = (os.getenv("IMAGE_PROMPT_BASE_URL") or DEFAULT_BASE_URL).strip()
        return cls(api_key=api_key, model=model, base_url=base_url)

    @property
    def is_configured(self) -> bool:
        """是否已配置 API Key"""
        return bool(self.api_key)


class DeepSeekPromptClient:
    """调用 DeepSeek 分析文章并生成配图提示词

    核心方法 analyze_and_generate()：
      构造 prompt → 调 DeepSeek Chat API → 解析 JSON 返回
    """

    def __init__(self, config: Optional[PromptLLMConfig] = None):
        self.config = config or PromptLLMConfig.from_env()

    def _build_user_prompt(self, title: str, content: str, style_table: str) -> str:
        """构造发给 DeepSeek 的 user prompt

        要求 LLM 扮演视觉设计师，为文章设计封面配图：
          1. 分析文章情绪/受众/场景关键词
          2. 从风格表选 5-6 种，每种给 reason + 英文 prompt
          3. 给出 primary_recommendation（主推荐）含 prompt + alt_text
        输出要求纯 JSON，不含 markdown 标记。
        """
        return f"""你是一位资深视觉设计师和插画导演。请阅读下面文章，为它设计一张封面配图。

你需要在以下风格列表中，根据文章的主题、情绪、受众，**先选出最合适的 5-6 种风格**，并为每种风格生成对应的 AI 绘画提示词。

{style_table}

=== 文章标题 ===
{title}

=== 文章正文（截取前段） ===
{content}

=== 输出要求 ===
请严格输出一个纯 JSON 对象（不要包含 ``` 或解释文字）：

{{
  "content_analysis": {{
    "mood": "文章整体情绪（如：严肃/温馨/科技感/励志…）",
    "target_audience": "目标读者画像",
    "scene_keywords": ["场景关键词1", "场景关键词2", "…"]
  }},
  "styles": [
        ...至少输出 5-6 种风格,每种先写 analysis（这个风格与文章哪些特质匹配）,再写 reason 和 prompt
    {{
      "slug": "realistic 等上述表格中的 slug",
      "reason": "为什么选择这种风格（一句话）",
      "prompt": "对应的英文 AI 绘画提示词，具体描述画面元素/构图/光线/色彩，约 80-150 词。注意：不要包含任何文字/logo/水印/标题在画面中，不要出现人物面部特写（如需人物请用远景或背影），画面应适合作为文章封面配图"
    }}
  ],
  "primary_recommendation": {{
    "slug": "最推荐的风格 slug",
    "prompt": "该风格下最终的封面提示词。注意：不要包含任何文字/logo/水印在画面中，画面应适合作为文章封面配图",
    "alt_text": "SEO 友好的中文 alt 文本（≤ 125 字）"
  }}
}}
"""

    async def analyze_and_generate(self, title: str, content: str) -> Dict[str, Any]:
        """调用 DeepSeek 分析文章并生成配图提示词

        Args:
            title: 文章标题
            content: 文章正文（会被截断到 2500 字）

        Returns:
            DeepSeek 输出的 JSON 解析后的字典，结构见 _build_user_prompt

        Raises:
            RuntimeError: API Key 未配置时抛出
        """
        if not self.config.is_configured:
            raise RuntimeError("image_prompt_api_key_missing")

        # 使用 OpenAI SDK 调用 DeepSeek（DeepSeek 兼容 OpenAI 接口）
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )

        # system prompt 限定 LLM 只输出纯 JSON
        resp = await client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "你是视觉设计助手，只会输出纯 JSON。",
                },
                {
                    "role": "user",
                    "content": self._build_user_prompt(
                        title=title,
                        content=_text_summary(content),
                        style_table=_build_style_table(),
                    ),
                },
            ],
        )
        # 取首条回复，解析 JSON
        raw = resp.choices[0].message.content if resp.choices else "{}"
        return _extract_json(raw)


# ---------------------------------------------------------------------------
# 数据库读写
# ---------------------------------------------------------------------------
class ImagePromptDB:
    """配图提示词的数据库访问层

    负责建表、查询待处理文章、写入提示词结果。
    使用 aiomysql 异步驱动。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = dict(config or {})
        self.host = self.config.get("host", "localhost")
        self.port = int(self.config.get("port", 3306))
        # 表名/库名用 validate_identifier 校验，防止 SQL 注入（无法参数化）
        self.database = validate_identifier(self.config.get("database", DEFAULT_DATABASE))
        self.user = self.config.get("user", "root")
        self.password = self.config.get("password", "")
        self.prompt_table = validate_identifier(self.config.get("prompt_table", DEFAULT_PROMPT_TABLE))
        self._conn = None  # 惰性连接

    async def _get_conn(self):
        """获取数据库连接（惰性创建，复用同一连接）"""
        if self._conn is None:
            import aiomysql
            self._conn = await aiomysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db=self.database,
                charset="utf8mb4",
                autocommit=False,  # 手动事务，保证写入一致性
            )
        return self._conn

    async def close(self) -> None:
        """关闭数据库连接"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def ensure_prompt_table(self) -> bool:
        """建表（如果不存在）

        表结构存储：文章 ID、标题、正文、提示词 JSON、状态、错误信息。
        通过 candidate_id + output_id 与上游文章表关联。
        """
        conn = await self._get_conn()
        query = f"""
        CREATE TABLE IF NOT EXISTS `{self.prompt_table}` (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            candidate_id BIGINT NULL,           -- 候选题材 ID
            output_id BIGINT NULL,              -- 文章输出 ID（关联 writer_article_outputs）
            source_title VARCHAR(500) NULL,     -- 原始标题
            generated_title VARCHAR(500) NULL,   -- AI 生成的标题
            content_md LONGTEXT NULL,           -- 文章正文（Markdown）
            image_prompts_json JSON NULL,        -- DeepSeek 生成的提示词 JSON
            generation_status VARCHAR(30) DEFAULT 'pending',  -- pending/generated/failed
            error_message TEXT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_candidate_id (candidate_id),
            INDEX idx_status (generation_status),
            INDEX idx_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        async with conn.cursor() as cursor:
            await cursor.execute(query)
            await conn.commit()
        return True

    async def fetch_articles_needing_prompts(self, limit: int = 10, min_quality: float = DEFAULT_MIN_QUALITY_SCORE) -> List[Dict[str, Any]]:
        """查找已通过质量审核但尚无配图提示词的文章

        查询逻辑：
          1. 从 writer_article_outputs 取状态为 generated 的文章
          2. LEFT JOIN article_quality_scores 取质量分
          3. 筛选 quality_score >= min_quality
          4. NOT EXISTS 排除已生成提示词的文章（避免重复处理）
          5. 按质量分降序（优先处理高质量文章）
        """
        import aiomysql
        conn = await self._get_conn()
        limit = max(1, int(limit))
        query = f"""
            SELECT
                o.id           AS output_id,
                o.candidate_id,
                o.source_title,
                o.generated_title,
                o.generated_content_md AS content_md,
                COALESCE(q.quality_score, 0) AS quality_score
            FROM `writer_article_outputs` o
            LEFT JOIN `article_quality_scores` q
                ON q.candidate_id = o.candidate_id
                AND q.source_kind IN ('writer', 'writer_plain')
            WHERE o.generation_status = 'generated'
              AND o.generated_content_md IS NOT NULL
              AND o.generated_content_md <> ''
              AND COALESCE(q.quality_score, 0) >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM `{self.prompt_table}` p
                  WHERE p.output_id = o.id
                    AND p.generation_status = 'generated'
              )
            ORDER BY q.quality_score DESC
            LIMIT %s
        """
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # min_quality 和 limit 用参数化（值），表名已校验（标识符）
            await cursor.execute(query, (float(min_quality), limit))
            rows = await cursor.fetchall()
        # 清洗 Decimal/datetime 等类型
        return [{k: _clean_db_value(v) for k, v in row.items()} for row in rows]

    async def write_prompts(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量写入配图提示词（INSERT ON DUPLICATE KEY UPDATE）

        Args:
            rows: 每项含 candidate_id/output_id/source_title/generated_title/
                  content_md/image_prompts_json/generation_status/error_message

        Returns:
            {success: True, inserted: 影响行数}
        """
        if not rows:
            return {"success": True, "inserted": 0}
        conn = await self._get_conn()
        # ON DUPLICATE KEY UPDATE：若 output_id 已存在则更新（幂等写入）
        query = f"""
            INSERT INTO `{self.prompt_table}` (
                candidate_id,
                output_id,
                source_title,
                generated_title,
                content_md,
                image_prompts_json,
                generation_status,
                error_message
            ) VALUES (
                %s, %s, %s, %s, %s,
                CAST(%s AS JSON), %s, %s
            )
            ON DUPLICATE KEY UPDATE
                candidate_id = VALUES(candidate_id),
                source_title = VALUES(source_title),
                generated_title = VALUES(generated_title),
                content_md = VALUES(content_md),
                image_prompts_json = VALUES(image_prompts_json),
                generation_status = VALUES(generation_status),
                error_message = VALUES(error_message)
        """
        # 构造批量插入参数
        params = [
            (
                r.get("candidate_id"),
                r.get("output_id"),
                r.get("source_title"),
                r.get("generated_title"),
                r.get("content_md"),
                _json_dumps(r.get("image_prompts_json")),  # JSON 序列化为字符串
                r.get("generation_status", "pending"),
                r.get("error_message"),
            )
            for r in rows
        ]
        async with conn.cursor() as cursor:
            await cursor.executemany(query, params)
            await conn.commit()
            return {"success": True, "inserted": cursor.rowcount}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def generate_image_prompts_from_db(
    db_config: Optional[Dict[str, Any]] = None,
    llm_config: Optional[PromptLLMConfig] = None,
    *,
    limit: int = 10,
    min_quality: float = DEFAULT_MIN_QUALITY_SCORE,
    concurrency: int = 2,
) -> Dict[str, Any]:
    """读取已通过审核的文章，调用 DeepSeek 生成配图提示词，存入 DB

    完整流程：
      1. 建表（确保存在）
      2. 查询待处理文章
      3. 并发调用 DeepSeek 生成提示词（Semaphore 限流）
      4. 批量写回 DB
      5. 返回统计结果

    Args:
        db_config: 数据库配置
        llm_config: DeepSeek 配置
        limit: 最多处理文章数
        min_quality: 最低质量分
        concurrency: 并发数（建议 2，避免 API 限流）

    Returns:
        {success, total, generated, failed, write_result, failures}
    """
    db = ImagePromptDB(db_config)
    await db.ensure_prompt_table()

    client = DeepSeekPromptClient(llm_config)
    # 信号量限流，控制对 DeepSeek API 的并发请求
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    try:
        # 1. 查询待处理文章
        articles = await db.fetch_articles_needing_prompts(limit=limit, min_quality=min_quality)

        # 2. 并发处理每篇文章
        async def process(article: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    title = str(article.get("generated_title") or article.get("source_title") or "")
                    content = str(article.get("content_md") or "")
                    if not title or not content:
                        raise ValueError("empty_article")
                    # 调 DeepSeek 分析并生成提示词
                    result = await client.analyze_and_generate(title=title, content=content)
                    return {
                        "candidate_id": article.get("candidate_id"),
                        "output_id": article.get("output_id"),
                        "source_title": article.get("source_title"),
                        "generated_title": title,
                        "content_md": content[:10000],  # 截断存储，避免过长
                        "image_prompts_json": result,
                        "generation_status": "generated",
                        "error_message": None,
                    }
                except Exception as exc:
                    # 失败也写入 DB，状态标记 failed，便于后续排查
                    return {
                        "candidate_id": article.get("candidate_id"),
                        "output_id": article.get("output_id"),
                        "source_title": article.get("source_title"),
                        "generated_title": article.get("generated_title"),
                        "content_md": article.get("content_md", "")[:10000],
                        "image_prompts_json": None,
                        "generation_status": "failed",
                        "error_message": str(exc),
                    }

        # 并发执行所有文章处理
        results = await asyncio.gather(*(process(a) for a in articles))

        # 3. 批量写回 DB
        write_result = await db.write_prompts(results)

        # 4. 统计成功/失败
        succeeded = sum(1 for r in results if r.get("generation_status") == "generated")
        failed = len(results) - succeeded
        return {
            "success": failed == 0,
            "total": len(articles),
            "generated": succeeded,
            "failed": failed,
            "write_result": write_result,
            "failures": [
                {"output_id": r["output_id"], "error": r["error_message"]}
                for r in results
                if r["generation_status"] == "failed"
            ],
        }
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# CrewAI Tool 封装
# ---------------------------------------------------------------------------
def get_image_prompt_tool():
    """返回 CrewAI 可用的 Tool

    将 generate_image_prompts_from_db 包装成 CrewAI @tool。
    """
    from crewai.tools import tool

    @tool("image_prompt_generator")
    def image_prompt_tool(
        limit: int = 5,
        min_quality: float = 85.0,
    ) -> str:
        """
        从数据库读取通过质量审核的文章，调用 DeepSeek 分析内容，生成配图提示词并存回数据库。

        Args:
            limit: 最多处理文章数（默认 5）
            min_quality: 最低质量分阈值（默认 85）

        Returns:
            JSON 格式的执行结果
        """
        async def _run():
            return await generate_image_prompts_from_db(
                limit=int(limit) if limit else 5,
                min_quality=float(min_quality) if min_quality else 85.0,
                concurrency=2,
            )

        result = asyncio.run(_run())
        return json.dumps(result, ensure_ascii=False, indent=2)

    return image_prompt_tool


# ---------------------------------------------------------------------------
# 命令行测试入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def test():
        config = PromptLLMConfig.from_env()
        if not config.is_configured:
            print("❌ DEEPSEEK_API_KEY 未配置")
            return

        print("=" * 60)
        print("测试: 直接调用 DeepSeek 分析文章生成配图提示词")
        print("=" * 60)

        # 用一段示例文章测试（不依赖数据库）
        test_title = "女性科学家在人工智能领域的突破性贡献"
        test_content = """
近年来，越来越多的女性科学家在人工智能领域崭露头角。从李飞飞教授开创 ImageNet 数据集，
到吴恩达团队中多位女性研究者推动深度学习落地，女性力量正在重塑 AI 的格局。

然而，数据显示女性在 AI 领域的研究人员占比仍不足 20%。今年的 NeurIPS 大会上，
组委会首次设立了"Women in Machine Learning"专场，吸引了上千名参会者。

来自 MIT 的张教授表示："我们需要更多女孩从小接触编程和数学，打破性别刻板印象。"
她领导的团队最近在自然语言处理领域取得了重大突破。
"""

        client = DeepSeekPromptClient(config)
        try:
            result = await client.analyze_and_generate(
                title=test_title,
                content=test_content,
            )
            print("\n✅ DeepSeek 分析结果:")
            print(json.dumps(result, ensure_ascii=False, indent=2))

            # 打印风格选项摘要
            if result.get("styles"):
                print(f"\n📊 生成了 {len(result['styles'])} 种风格选项")
                for s in result["styles"]:
                    print(f"  - [{s['slug']}] {s.get('reason','')[:60]}...")
            # 打印主推荐
            if result.get("primary_recommendation"):
                rec = result["primary_recommendation"]
                print(f"\n⭐ 推荐风格: {rec['slug']}")
                print(f"   Prompt: {rec['prompt'][:100]}...")
        except Exception as e:
            print(f"\n❌ 错误: {e}")

    asyncio.run(test())
