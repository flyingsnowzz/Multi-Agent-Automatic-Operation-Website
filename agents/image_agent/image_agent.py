#!/usr/bin/env python3
"""
ImageAgent - 配图设计师

负责为文章生成或选择合适的配图，包括：
- 封面图生成（OpenAI DALL-E / Coze Site）
- 文中插图生成
- SEO 友好 Alt 文本生成
- 基于 DeepSeek 的内容分析与配图提示词生成
- 提示词到图片的完整 Pipeline
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

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
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            expr = value[2:-1]
            if ":-" in expr:
                key, default = expr.split(":-", 1)
                return os.environ.get(key, default)
            return os.environ.get(expr, "")
        return value
    if isinstance(value, dict):
        return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_env_resolve(v) for v in value]
    return value


@dataclass
class ImageResult:
    """标准化 ImageAgent 输出结构"""
    featured_image_url: str = ""
    featured_alt: str = ""
    featured_prompt: str = ""
    inline_images: List[Dict[str, Any]] = field(default_factory=list)
    license: Dict[str, str] = field(default_factory=lambda: {"source": "planned", "provider": "openai"})

    def to_dict(self) -> Dict[str, Any]:
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
    """

    def __init__(self, config_path: str = "agents/image_agent/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def get_image_generator(self) -> ImageGenerator:
        """获取 OpenAI DALL-E 图片生成器实例"""
        return ImageGenerator(config_path=self.config_path)

    def get_coze_provider(self) -> CozeImageProvider:
        """获取 Coze Site 图片生成 Provider 实例"""
        return CozeImageProvider(config_path=self.config_path)

    def get_alt_text_generator(self) -> AltTextGenerator:
        """获取 Alt 文本生成器实例"""
        return AltTextGenerator()

    def get_prompt_client(self) -> DeepSeekPromptClient:
        """获取 DeepSeek 提示词生成客户端"""
        return DeepSeekPromptClient(PromptLLMConfig.from_env())

    async def generate_featured_image(
        self,
        prompt: str,
        visual_style: str = "professional",
        size: str = "1024x1024",
        quality: str = "standard",
    ) -> Dict[str, Any]:
        """生成封面图"""
        generator = self.get_image_generator()
        try:
            style = ImageStyle(visual_style) if visual_style else ImageStyle.PROFESSIONAL
            return await generator.generate(
                prompt=prompt,
                visual_style=style,
                size=size,
                quality=quality,
            )
        finally:
            await generator.close()

    async def generate_alt_text(
        self,
        image_description: str,
        context: str = "",
        keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """为图片生成 Alt 文本"""
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
        """从数据库读取已审核文章，调用 DeepSeek 生成配图提示词"""
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
        """从数据库读取提示词，调用 Coze 生图"""
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
        """
        完整配图生产流水线：
        1. 从 DB 读取已审核文章
        2. 调用 DeepSeek 分析内容生成配图提示词
        3. 调用 Coze Site 生成实际图片
        """
        prompt_result = await self.generate_prompts_from_db(
            limit=prompt_limit,
            min_quality=min_quality,
        )
        if not prompt_result.get("success") and prompt_result.get("generated", 0) == 0:
            return {
                "success": False,
                "stage": "prompt_generation",
                "prompt_result": prompt_result,
                "image_result": None,
            }

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
    async def test():
        agent = ImageAgent()
        print("=" * 60)
        print("ImageAgent 测试")

        alt_result = await agent.generate_alt_text(
            image_description="商务人士在现代化办公室使用笔记本电脑",
            context="EMBA 选择指南",
            keywords=["EMBA"],
        )
        print(f"\nAlt 文本: {alt_result.get('alt_text', 'N/A')}")

        if os.environ.get("OPENAI_API_KEY"):
            gen_result = await agent.generate_featured_image(
                prompt="A modern business school campus at sunset",
            )
            print(f"\n生图结果: success={gen_result.get('success')}")
        else:
            print("\nOPENAI_API_KEY 未配置，跳过生图测试")

        print("=" * 60)

    asyncio.run(test())
