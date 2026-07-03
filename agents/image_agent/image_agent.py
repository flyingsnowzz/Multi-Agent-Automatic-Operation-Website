#!/usr/bin/env python3
"""
ImageAgent - 配图设计师

负责为文章生成或选择合适的配图，包括：
- 封面图生成（OpenAI DALL-E / Coze Site）
- 文中插图生成
- SEO 友好 Alt 文本生成
- 基于 DeepSeek 的内容分析与配图提示词生成
- 提示词到图片的完整 Pipeline

架构说明：
    ImageAgent 是整个配图模块的对外入口（外观层 / Facade），它聚合了多个底层工具：
      - AltTextGenerator        : 生成 SEO 友好的图片替代文本
      - ImageGenerator          : 调用 OpenAI DALL-E / 兼容 API 生成图片
      - CozeImageProvider       : 调用 Coze Site 生成图片
      - DeepSeekPromptClient    : 用 DeepSeek 分析文章内容并生成配图提示词
      - PromptToImagePipeline   : 提示词 → 实际图片 的流水线
    调用方无需关心内部实现细节，只通过 ImageAgent 暴露的高层方法即可完成配图生产。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

# 引入各底层工具，ImageAgent 作为聚合层把它们组合起来
from agents.image_agent.tools.alt_text_generator import AltTextGenerator
from agents.image_agent.tools.coze_image_provider import CozeImageProvider
from agents.image_agent.tools.image_generator import (
    ImageGenerator,
    ImageStyle,
    OpenAIImageStyle,
)
from agents.image_agent.tools.image_prompt_generator import (
    DeepSeekPromptClient,
    PromptLLMConfig,
    generate_image_prompts_from_db,
)
from agents.image_agent.tools.prompt_to_image import (
    generate_images_from_prompts,
    PromptToImagePipeline,
)


def _deep_env_resolve(value: Any) -> Any:
    """
    递归解析配置中形如 ${VAR} 或 ${VAR:-default} 的环境变量占位符。

    支持三种数据结构的递归处理：
      - str : 若匹配 `${...}` 则从 os.environ 取值，支持 `:-` 指定默认值
      - dict: 对每个 value 递归解析
      - list: 对每个元素递归解析
    其它类型原样返回。

    设计目的：让 config.yaml 里可以写 ${DEEPSEEK_API_KEY} 这类占位符，
    运行时再从环境变量注入真实值，避免把密钥硬编码到配置文件。
    """
    if isinstance(value, str):
        # 仅处理完整包裹的 ${...} 形式
        if value.startswith("${") and value.endswith("}"):
            expr = value[2:-1]  # 去掉 ${ 和 }
            if ":-" in expr:
                # 形如 ${VAR:-default}：VAR 未设置时用 default
                key, default = expr.split(":-", 1)
                return os.environ.get(key, default)
            # 形如 ${VAR}：VAR 未设置时返回空字符串
            return os.environ.get(expr, "")
        return value
    if isinstance(value, dict):
        return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_env_resolve(v) for v in value]
    return value


@dataclass
class ImageResult:
    """标准化 ImageAgent 输出结构

    作为 Agent 的统一返回契约，便于上层（如 CrewAI / 发布流程）消费。
    所有字段都有默认值，保证缺字段时也能正常序列化。
    """
    featured_image_url: str = ""        # 封面图 URL
    featured_alt: str = ""              # 封面图 Alt 文本（SEO 用）
    featured_prompt: str = ""           # 封面图生成时使用的提示词
    inline_images: List[Dict[str, Any]] = field(default_factory=list)  # 文中插图列表
    license: Dict[str, str] = field(default_factory=lambda: {"source": "planned", "provider": "openai"})  # 版权/来源信息

    def to_dict(self) -> Dict[str, Any]:
        """转为可被 JSON 序列化的普通字典"""
        return {
            "featured_image_url": self.featured_image_url,
            "featured_alt": self.featured_alt,
            "featured_prompt": self.featured_prompt,
            "inline_images": self.inline_images,
            "license": self.license,
        }


class ImageAgent:
    """配图设计师 Agent

    支持两种模式:
    - plan_only: 仅输出配图方案（提示词 + Alt 文本），不实际调用生图 API
    - generate: 实际调用生图 API 生成图片并回填 URL

    使用方式：
        agent = ImageAgent()
        result = await agent.generate_featured_image(prompt="...")
    """

    def __init__(self, config_path: str = "agents/image_agent/config.yaml"):
        # 记录配置文件路径，子工具创建时也会用到同一份配置
        self.config_path = config_path
        # 加载配置并解析其中的环境变量占位符
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """读取 config.yaml，解析其中的 ${ENV_VAR} 占位符"""
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        # 将 ${VAR} 占位符替换为真实环境变量值
        return _deep_env_resolve(raw)

    # ------------------------------------------------------------------
    # 工厂方法：按需创建底层工具实例
    # 每个方法都是独立的工厂，避免在构造时就初始化全部工具（惰性创建）
    # ------------------------------------------------------------------
    def get_image_generator(self) -> ImageGenerator:
        """获取 OpenAI DALL-E 图片生成器实例"""
        return ImageGenerator(config_path=self.config_path)

    def get_coze_provider(self) -> CozeImageProvider:
        """获取 Coze Site 图片生成 Provider 实例"""
        return CozeImageProvider(config_path=self.config_path)

    def get_alt_text_generator(self) -> AltTextGenerator:
        """获取 Alt 文本生成器实例（无状态，直接 new）"""
        return AltTextGenerator()

    def get_prompt_client(self) -> DeepSeekPromptClient:
        """获取 DeepSeek 提示词生成客户端，配置从环境变量读取"""
        return DeepSeekPromptClient(PromptLLMConfig.from_env())

    # ------------------------------------------------------------------
    # 高层业务方法
    # ------------------------------------------------------------------
    async def generate_featured_image(
        self,
        prompt: str,
        visual_style: str = "professional",
        size: str = "1024x1024",
        quality: str = "standard",
    ) -> Dict[str, Any]:
        """生成封面图

        Args:
            prompt: 图片描述提示词
            visual_style: 视觉风格，对应 ImageStyle 枚举值
            size: 图片尺寸，如 1024x1024
            quality: 图片质量 standard / hd

        Returns:
            ImageGenerator.generate() 的返回结果（含 images 列表、prompt 等）
        """
        generator = self.get_image_generator()
        try:
            # 把字符串风格转为枚举，未指定时默认 PROFESSIONAL
            style = ImageStyle(visual_style) if visual_style else ImageStyle.PROFESSIONAL
            return await generator.generate(
                prompt=prompt,
                visual_style=style,
                size=size,
                quality=quality,
            )
        finally:
            # 确保 httpx 异步客户端被关闭，避免资源泄漏
            await generator.close()

    async def generate_alt_text(
        self,
        image_description: str,
        context: str = "",
        keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """为图片生成 SEO 友好的 Alt 文本

        Args:
            image_description: 图片内容描述
            context: 图片所在文章的上下文/主题
            keywords: 关键词列表，会尽量放进 Alt 文本以提升 SEO

        Returns:
            包含 alt_text / title / warnings / suggestions 的字典
        """
        gen = self.get_alt_text_generator()
        return gen.generate(
            image_description=image_description,
            context=context,
            keywords=keywords or [],
        )

    async def generate_prompts_from_db(
        self,
        limit: int = 10,
        min_quality: float = 85.0,
    ) -> Dict[str, Any]:
        """从数据库读取已审核文章，调用 DeepSeek 生成配图提示词

        流程：查 DB → 调 DeepSeek 分析文章 → 生成多风格提示词 → 写回 DB
        并发度固定为 2，避免对 DeepSeek API 造成过大压力。

        Args:
            limit: 最多处理文章数
            min_quality: 仅处理质量分 >= 该阈值的文章
        """
        return await generate_image_prompts_from_db(
            limit=limit,
            min_quality=min_quality,
            concurrency=2,
        )

    async def generate_images_from_prompts(
        self,
        limit: int = 5,
        only_primary: bool = True,
    ) -> Dict[str, Any]:
        """从数据库读取提示词，调用 Coze 生图

        流程：查 DB 中已生成提示词但未生图的记录 → 调 Coze 生图 → 下载到本地 → 写回 DB
        并发度固定为 1，因为 Coze Site 单次请求较重。

        Args:
            limit: 最多处理文章数
            only_primary: True=只生成推荐风格，False=生成全部风格
        """
        return await generate_images_from_prompts(
            limit=limit,
            only_primary=only_primary,
            concurrency=1,
        )

    async def full_pipeline(
        self,
        *,
        prompt_limit: int = 5,
        image_limit: int = 5,
        min_quality: float = 85.0,
    ) -> Dict[str, Any]:
        """完整配图生产流水线

        串联两个阶段：
        1. 从 DB 读取已审核文章 → 调 DeepSeek 分析内容生成配图提示词
        2. 从 DB 读取提示词 → 调 Coze Site 生成实际图片

        若第一阶段没有任何产出（提示词生成失败且 generated=0），
        则直接返回失败，不再进入第二阶段，节省 Coze 调用成本。

        Args:
            prompt_limit: 第一阶段处理文章数
            image_limit: 第二阶段生图数
            min_quality: 文章质量分阈值

        Returns:
            含 success / stage / prompt_result / image_result 的汇总字典
        """
        # 阶段一：生成配图提示词
        prompt_result = await self.generate_prompts_from_db(
            limit=prompt_limit,
            min_quality=min_quality,
        )
        # 若提示词阶段完全失败且没有产出，提前返回，避免无谓的生图调用
        if not prompt_result.get("success") and prompt_result.get("generated", 0) == 0:
            return {
                "success": False,
                "stage": "prompt_generation",
                "prompt_result": prompt_result,
                "image_result": None,
            }

        # 阶段二：基于提示词生成实际图片（只生成主推荐风格）
        image_result = await self.generate_images_from_prompts(
            limit=image_limit,
            only_primary=True,
        )
        return {
            "success": image_result.get("success", False),
            "stage": "full_pipeline",
            "prompt_result": prompt_result,
            "image_result": image_result,
        }


if __name__ == "__main__":
    # 模块自测：验证 Alt 文本生成 + 可选的封面图生成
    async def test():
        agent = ImageAgent()
        print("=" * 60)
        print("ImageAgent 测试")

        # 1. 测试 Alt 文本生成（不依赖外部生图 API）
        alt_result = await agent.generate_alt_text(
            image_description="商务人士在现代化办公室使用笔记本电脑",
            context="EMBA 选择指南",
            keywords=["EMBA"],
        )
        print(f"\nAlt 文本: {alt_result.get('alt_text', 'N/A')}")

        # 2. 测试封面图生成（需要 OPENAI_API_KEY 才会执行）
        if os.environ.get("OPENAI_API_KEY"):
            gen_result = await agent.generate_featured_image(
                prompt="A modern business school campus at sunset",
            )
            print(f"\n生图结果: success={gen_result.get('success')}")
        else:
            print("\nOPENAI_API_KEY 未配置，跳过生图测试")

        print("=" * 60)

    asyncio.run(test())
