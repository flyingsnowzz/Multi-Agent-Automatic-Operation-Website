#!/usr/bin/env python3
"""Image provider factory for publish-time cover generation.

设计说明：
    这是图片生成的「工厂层」，根据配置/环境变量选择具体生图 Provider。
    目前支持三类 Provider：
      - coze     : Coze Site Bot（JWT 鉴权）
      - openai   : OpenAI DALL-E / gpt-image-1（或兼容端点）
      - seedance : Seedance/Seedream（OpenAI 兼容的第三方端点）

    发布时只需调用 get_image_provider() 即可拿到对应 Provider 实例，
    无需关心内部差异。所有 Provider 都遵循相同的 generate(prompt, n) 接口。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import yaml

from agents.image_agent.tools.coze_image_provider import CozeImageProvider
from agents.image_agent.tools.image_generator import ImageGenerator, ImageStyle


def _deep_env_resolve(value: Any) -> Any:
    """
    递归解析配置中形如 ${VAR} 或 ${VAR:-default} 的环境变量占位符。

    与其它模块的同名函数功能一致，独立定义避免循环依赖。
    """
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            expr = value[2:-1]
            if ":-" in expr:
                # ${VAR:-default} 形式：支持默认值
                key, default = expr.split(":-", 1)
                return os.environ.get(key, default)
            # ${VAR} 形式：无默认值
            return os.environ.get(expr, "")
        return value
    if isinstance(value, dict):
        return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_env_resolve(v) for v in value]
    return value


def _load_config(config_path: str) -> Dict[str, Any]:
    """读取 config.yaml 并解析环境变量占位符"""
    if not config_path or not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return _deep_env_resolve(yaml.safe_load(f) or {})


class OpenAICompatibleImageProvider:
    """Small adapter over ImageGenerator for OpenAI-compatible image APIs.

    适配器模式：把 ImageGenerator（OpenAI 原生封装）适配成统一的
    generate(prompt, n) → {success, images, provider} 接口。
    用于 openai 和 seedance 两类兼容端点。
    """

    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str,
        base_url: str,
        model: str,
        size: str,
        quality: str,
        cache_dir: str,
        config_path: str,
    ):
        self.provider_name = provider_name
        self.model = model
        self.size = size
        self.quality = quality
        # 复用 ImageGenerator 的底层能力（HTTP 调用、b64 保存等）
        self.generator = ImageGenerator(
            api_key=api_key,
            api_base=base_url,
            config_path=config_path,
            cache_dir=cache_dir,
        )

    async def generate(self, prompt: str, n: int = 1) -> Dict[str, Any]:
        """生成图片，统一返回格式并附加 provider 标识"""
        result = await self.generator.generate(
            prompt=prompt,
            visual_style=ImageStyle.PROFESSIONAL,
            size=self.size,
            quality=self.quality,
            n=n,
            model=self.model,
            response_format="b64_json",
        )
        # 附加 provider 标识，便于上层区分来源
        result["provider"] = self.provider_name
        return result

    async def close(self):
        """关闭底层 HTTP 客户端"""
        await self.generator.close()


def get_image_provider(
    *,
    provider: Optional[str] = None,
    config_path: str = "agents/image_agent/config.yaml",
):
    """图片 Provider 工厂方法

    根据优先级确定使用哪个 Provider：
      显式入参 provider > 环境变量 IMAGE_PROVIDER > 配置文件 > 默认 coze

    Args:
        provider: 显式指定 Provider 名称（coze/openai/seedance）
        config_path: 配置文件路径

    Returns:
        CozeImageProvider 或 OpenAICompatibleImageProvider 实例

    Raises:
        ValueError: 不支持的 provider 名称
    """
    config = _load_config(config_path)
    image_cfg = (config.get("image_generation") or {}) if isinstance(config, dict) else {}
    # 确定 Provider：入参 > 环境变量 > 配置 > 默认 coze
    selected = (
        provider
        or os.environ.get("IMAGE_PROVIDER")
        or image_cfg.get("provider")
        or "coze"
    )
    selected = str(selected).strip().lower()

    # 1. Coze Site Provider
    if selected == "coze":
        return CozeImageProvider(config_path=config_path)

    # 2. OpenAI / ChatGPT / gpt-image 系列
    if selected in {"openai", "chatgpt", "gpt-image"}:
        cfg = image_cfg.get("openai") or {}
        return OpenAICompatibleImageProvider(
            provider_name="openai",
            # API Key：环境变量 > 配置
            api_key=os.environ.get("IMAGE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("IMAGE_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("IMAGE_OPENAI_MODEL") or cfg.get("model") or "gpt-image-1",
            size=os.environ.get("IMAGE_OPENAI_SIZE") or cfg.get("size") or "1024x1024",
            quality=os.environ.get("IMAGE_OPENAI_QUALITY") or cfg.get("quality") or "standard",
            cache_dir=os.environ.get("IMAGE_OPENAI_CACHE_DIR") or "output/images/openai_cache",
            config_path=config_path,
        )

    # 3. Seedance / Seedream / 火山引擎（OpenAI 兼容端点）
    if selected in {"seedance", "seedream", "volcengine"}:
        cfg = image_cfg.get("seedance") or {}
        return OpenAICompatibleImageProvider(
            provider_name="seedance",
            api_key=os.environ.get("SEEDANCE_API_KEY") or cfg.get("api_key") or "",
            base_url=os.environ.get("SEEDANCE_BASE_URL") or cfg.get("base_url") or "",
            model=os.environ.get("SEEDANCE_MODEL") or cfg.get("model") or "seedream-3-0-t2i-250415",
            size=os.environ.get("SEEDANCE_SIZE") or cfg.get("size") or "1024x1024",
            quality=os.environ.get("SEEDANCE_QUALITY") or cfg.get("quality") or "standard",
            cache_dir=os.environ.get("SEEDANCE_CACHE_DIR") or "output/images/seedance_cache",
            config_path=config_path,
        )

    # 不支持的 Provider 名称
    raise ValueError(f"unsupported_image_provider:{selected}")
