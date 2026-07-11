#!/usr/bin/env python3
"""ImageAgent - 配图设计师

Beginner mental model:
    This agent keeps direct image helpers. The formal LangGraph pipeline uses
    workflows/langgraph_article_pipeline.py plus provider_factory.py for
    per-article cover handling.

负责为文章生成或选择合适的配图，包括：
- 封面图生成（OpenAI DALL-E / Coze Site）
- 文中插图生成
- SEO 友好 Alt 文本生成
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


def _deep_env_resolve(value: Any) -> Any:
    # Expand ${ENV} / ${ENV:-default} in config.yaml. Image provider keys stay
    # in .env instead of this Python file.
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
        # ImageAgent is a convenience facade for manual/tests. The production
        # LangGraph image node selects providers through
        # agents.image_agent.tools.provider_factory.
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        # Load image config and expand environment placeholders. Secrets stay in
        # .env instead of being hard-coded in config.yaml.
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def get_image_generator(self) -> ImageGenerator:
        """获取 OpenAI DALL-E 图片生成器实例"""
        # Manual/test path for OpenAI-style image generation.
        return ImageGenerator(config_path=self.config_path)

    def get_coze_provider(self) -> CozeImageProvider:
        """获取 Coze Site 图片生成 Provider 实例"""
        # Manual/test path for Coze. The LangGraph image node uses provider_factory
        # so IMAGE_PROVIDER can switch providers.
        return CozeImageProvider(config_path=self.config_path)

    def get_alt_text_generator(self) -> AltTextGenerator:
        """获取 Alt 文本生成器实例"""
        return AltTextGenerator()

    async def generate_featured_image(
        self,
        prompt: str,
        visual_style: str = "professional",
        size: str = "1024x1024",
        quality: str = "standard",
    ) -> Dict[str, Any]:
        """生成封面图"""
        # This path uses the OpenAI-style ImageGenerator. The LangGraph image node
        # may instead select Coze/Seedance/OpenAI through provider_factory.
        generator = self.get_image_generator()
        try:
            # visual_style is converted into the ImageStyle enum so invalid
            # values fail early instead of reaching the provider request.
            style = ImageStyle(visual_style) if visual_style else ImageStyle.PROFESSIONAL
            return await generator.generate(
                prompt=prompt,
                visual_style=style,
                size=size,
                quality=quality,
            )
        finally:
            # Always close HTTP clients/sessions held by the generator.
            await generator.close()

    async def generate_alt_text(
        self,
        image_description: str,
        context: str = "",
        keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """为图片生成 Alt 文本"""
        # Alt text generation is local/deterministic here. It does not call the
        # image provider and can be used independently for existing images.
        gen = self.get_alt_text_generator()
        return gen.generate(
            image_description=image_description,
            context=context,
            keywords=keywords or [],
        )


if __name__ == "__main__":
    async def test():
        agent = ImageAgent()
        print("=" * 60)
        print("ImageAgent 测试")

        alt_result = await agent.generate_alt_text(
            image_description="商务人士在现代化办公室使用笔记本电脑",
            context="科技新闻配图",
            keywords=["科技新闻"],
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
