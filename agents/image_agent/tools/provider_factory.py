#!/usr/bin/env python3
"""Image provider factory for publish-time cover generation."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import yaml

from agents.image_agent.tools.coze_image_provider import CozeImageProvider
from agents.image_agent.tools.image_generator import ImageGenerator, ImageStyle


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


def _load_config(config_path: str) -> Dict[str, Any]:
    if not config_path or not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return _deep_env_resolve(yaml.safe_load(f) or {})


class OpenAICompatibleImageProvider:
    """Small adapter over ImageGenerator for OpenAI-compatible image APIs."""

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
        self.generator = ImageGenerator(
            api_key=api_key,
            api_base=base_url,
            config_path=config_path,
            cache_dir=cache_dir,
        )

    async def generate(self, prompt: str, n: int = 1) -> Dict[str, Any]:
        result = await self.generator.generate(
            prompt=prompt,
            visual_style=ImageStyle.PROFESSIONAL,
            size=self.size,
            quality=self.quality,
            n=n,
            model=self.model,
            response_format="b64_json",
        )
        result["provider"] = self.provider_name
        return result

    async def close(self):
        await self.generator.close()


def get_image_provider(
    *,
    provider: Optional[str] = None,
    config_path: str = "agents/image_agent/config.yaml",
):
    config = _load_config(config_path)
    image_cfg = (config.get("image_generation") or {}) if isinstance(config, dict) else {}
    selected = (
        provider
        or os.environ.get("IMAGE_PROVIDER")
        or image_cfg.get("provider")
        or "coze"
    )
    selected = str(selected).strip().lower()

    if selected == "coze":
        return CozeImageProvider(config_path=config_path)

    if selected in {"openai", "chatgpt", "gpt-image"}:
        cfg = image_cfg.get("openai") or {}
        return OpenAICompatibleImageProvider(
            provider_name="openai",
            api_key=os.environ.get("IMAGE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("IMAGE_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("IMAGE_OPENAI_MODEL") or cfg.get("model") or "gpt-image-1",
            size=os.environ.get("IMAGE_OPENAI_SIZE") or cfg.get("size") or "1024x1024",
            quality=os.environ.get("IMAGE_OPENAI_QUALITY") or cfg.get("quality") or "standard",
            cache_dir=os.environ.get("IMAGE_OPENAI_CACHE_DIR") or "output/images/openai_cache",
            config_path=config_path,
        )

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

    raise ValueError(f"unsupported_image_provider:{selected}")
