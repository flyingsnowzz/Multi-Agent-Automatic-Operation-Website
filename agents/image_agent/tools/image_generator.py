#!/usr/bin/env python3
"""
图片生成工具 - ImageAgent
调用DALL-E或Midjourney API生成配图

设计说明：
    本模块封装 OpenAI Images API（/v1/images/generations 等），支持：
      - 文生图（generations）：根据 prompt 生成图片
      - 图生变体（variations）：基于现有图生成变体（仅 dall-e-2）
      - 图编辑（edits）：蒙版局部修改（仅 dall-e-2）
    同时支持 OpenAI 兼容的第三方端点（如 Seedance/Seedream）。

    两套风格体系：
      - VisualStyle   : 业务视觉风格（professional/realistic/...），用于提示词增强
      - OpenAIImageStyle: OpenAI API 原生 style 参数（vivid/natural），仅部分模型支持
"""

import os
import json
import base64
import hashlib
import httpx
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum

import yaml


def _deep_env_resolve(value: Any) -> Any:
    """
    递归解析配置中形如 ${VAR} 或 ${VAR:-default} 的环境变量占位符。

    与 image_agent.py 中的同名函数功能一致，此处独立定义避免循环依赖。
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


class VisualStyle(str, Enum):
    """业务视觉风格枚举

    用于在提示词中追加风格修饰词，影响生成图片的整体观感。
    继承 str 使得枚举值可直接作为字符串使用。
    """
    REALISTIC = "realistic"          # 写实摄影
    ILLUSTRATION = "illustration"    # 数字插画
    ABSTRACT = "abstract"            # 抽象艺术
    PROFESSIONAL = "professional"    # 专业商务
    MINIMALIST = "minimalist"        # 极简风格
    PHOTOGRAPHIC = "photographic"    # 专业摄影
    INFORMATIVE = "informative"      # 信息图


class OpenAIImageStyle(str, Enum):
    """OpenAI Images API 原生 style 参数枚举

    仅 dall-e-3 支持，控制图片的整体色调倾向。
    """
    NATURAL = "natural"  # 自然风格，偏写实
    VIVID = "vivid"      # 鲜艳风格，色彩饱和度高


# 别名：ImageAgent 对外暴露 ImageStyle（与 VisualStyle 等价）
ImageStyle = VisualStyle


class ImageGenerator:
    """图片生成工具

    封装 OpenAI Images API 的异步调用，支持文生图/变体/编辑。
    通过 config.yaml 提供默认参数，运行时可用入参覆盖。

    生命周期：
        generator = ImageGenerator()
        result = await generator.generate(prompt="...")
        await generator.close()  # 必须调用以释放 httpx 客户端
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        config_path: str = "agents/image_agent/config.yaml",
        cache_dir: Optional[str] = None,
    ):
        # API Key：优先用显式入参，其次读环境变量（兼容多种命名）
        if api_key is None:
            self.api_key = os.environ.get("IMAGE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        else:
            self.api_key = api_key
        # API Base URL：支持第三方兼容端点
        self.api_base = api_base or os.environ.get("IMAGE_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        # 本地缓存目录：保存 b64 图片
        self.cache_dir = cache_dir or os.environ.get("IMAGE_CACHE_DIR", "output/images/openai_cache")
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        # 异步 HTTP 客户端（60s 超时，生图 API 通常较慢）
        self.http_client = httpx.AsyncClient(timeout=60.0)
        self.config_path = config_path
        self.config = self._load_config()
        # 从配置提取 OpenAI 默认参数（model/size/quality 等）
        self.defaults = self._extract_openai_defaults()

    def _load_config(self) -> Dict[str, Any]:
        """读取 config.yaml 并解析环境变量占位符"""
        if not self.config_path or not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _extract_openai_defaults(self) -> Dict[str, Any]:
        """从配置中提取 OpenAI 图片生成的默认参数

        依次从 image_generation.openai / image_generation.dalle 读取，
        保证关键字段都有兜底默认值。
        """
        cfg = (self.config or {}).get("image_generation") or {}
        # 兼容 openai / dalle 两种键名
        openai_cfg = cfg.get("openai") or cfg.get("dalle") or {}
        model = (openai_cfg.get("model") or "gpt-image-1").strip()
        size = (openai_cfg.get("size") or "1024x1024").strip()
        quality = (openai_cfg.get("quality") or "standard").strip()
        response_format = (openai_cfg.get("response_format") or "").strip()
        api_style = (openai_cfg.get("api_style") or openai_cfg.get("style") or "").strip()
        # response_format 默认值随模型变化：gpt-image-1 用 b64_json，其它用 url
        if not response_format:
            response_format = "b64_json" if model.startswith("gpt-image") else "url"
        # 视觉风格默认值：先读 image_generation.visual_style，再读 image_requirements
        visual_style = (cfg.get("visual_style") or "").strip()
        if not visual_style:
            req = (self.config or {}).get("image_requirements") or {}
            featured = (req.get("featured_image") or {}) if isinstance(req, dict) else {}
            visual_style = (featured.get("visual_style") or featured.get("style") or "professional").strip()
        return {
            "model": model,
            "size": size,
            "quality": quality,
            "response_format": response_format,
            "api_style": api_style,
            "visual_style": visual_style,
        }

    def _validate_size(self, size: str) -> Tuple[bool, str]:
        """校验尺寸格式（必须是 WxH 数字格式）"""
        if not isinstance(size, str) or not re.match(r"^\d+x\d+$", size):
            return False, "size_format"
        return True, ""

    def _validate_quality(self, quality: str, model: str) -> Tuple[bool, str]:
        """校验质量参数是否被当前模型支持"""
        if not isinstance(quality, str) or not quality.strip():
            return False, "quality_empty"
        quality = quality.strip()
        # dall-e-3 仅支持 standard / hd
        if model == "dall-e-3" and quality not in {"standard", "hd"}:
            return False, "quality_invalid_for_dalle_3"
        # gpt-image-1 等支持 low/medium/high
        if quality not in {"standard", "hd", "low", "medium", "high"}:
            return False, "quality_invalid"
        return True, ""

    def _normalize_openai_style(self, api_style: Optional[str]) -> Optional[OpenAIImageStyle]:
        """把字符串/枚举规范化为 OpenAIImageStyle 或 None"""
        if api_style is None:
            return None
        if isinstance(api_style, OpenAIImageStyle):
            return api_style
        s = str(api_style).strip().lower()
        if not s:
            return None
        try:
            return OpenAIImageStyle(s)
        except Exception:
            # 无法识别的值返回 None，不报错
            return None

    async def generate(
        self,
        prompt: str,
        visual_style: VisualStyle = VisualStyle.PROFESSIONAL,
        api_style: Optional[OpenAIImageStyle] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        n: int = 1,
        model: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成图片（文生图主方法）

        Args:
            prompt: 图片描述提示词
            visual_style: 业务视觉风格，用于增强提示词
            api_style: OpenAI 原生 style（vivid/natural），仅 dall-e-3 生效
            size: 尺寸，可选 1024x1024, 1792x1024, 1024x1792
            quality: 质量 standard/hd/low/medium/high
            n: 生成数量（dall-e-3 只能为 1）
            model: 模型名称，留空用配置默认值
            response_format: url/b64_json

        Returns:
            {
                success: bool,
                images: [{url, b64_json, local_path, revised_prompt, width, height}],
                model, prompt, response_format, api_style, visual_style
            } 或 {success: False, error: ...}
        """
        # 缺 API Key 直接返回失败
        if not self.api_key:
            return {
                "success": False,
                "error": "API key未配置",
                "images": []
            }

        # 参数回退到配置默认值
        model = (model or self.defaults.get("model") or "gpt-image-1").strip()
        size = (size or self.defaults.get("size") or "1024x1024").strip()
        quality = (quality or self.defaults.get("quality") or "standard").strip()
        response_format = (response_format or self.defaults.get("response_format") or "url").strip()

        # 参数校验
        ok, err = self._validate_size(size)
        if not ok:
            return {"success": False, "error": err, "images": []}
        ok, err = self._validate_quality(quality, model)
        if not ok:
            return {"success": False, "error": err, "images": []}
        if not isinstance(n, int) or n < 1:
            return {"success": False, "error": "n_invalid", "images": []}
        # dall-e-3 限制：每次只能生成 1 张
        if model == "dall-e-3" and n != 1:
            return {"success": False, "error": "dall_e_3_only_supports_n_1", "images": []}

        if response_format not in {"url", "b64_json"}:
            return {"success": False, "error": "response_format_invalid", "images": []}

        # 视觉风格归一化（字符串 → 枚举）
        default_visual = (self.defaults.get("visual_style") or "professional").strip().lower()
        if isinstance(visual_style, str):
            try:
                visual_style = VisualStyle(visual_style)
            except Exception:
                try:
                    visual_style = VisualStyle(default_visual)
                except Exception:
                    visual_style = VisualStyle.PROFESSIONAL

        # api_style 归一化
        if api_style is None:
            api_style = self._normalize_openai_style(self.defaults.get("api_style"))
        api_style = self._normalize_openai_style(api_style.value if isinstance(api_style, OpenAIImageStyle) else api_style)

        # 用风格修饰词增强提示词
        enhanced_prompt = self._enhance_prompt(prompt, visual_style)

        # 构造 OpenAI Images API 请求
        url = f"{self.api_base}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "prompt": enhanced_prompt,
            "n": n,
            "size": size,
            "quality": quality,
            "response_format": response_format,
            # style 参数仅 dall-e-3 支持
            "style": (api_style.value if (model == "dall-e-3" and api_style) else None),
        }

        # 移除值为 None 的字段，避免发送无效参数
        data = {k: v for k, v in data.items() if v is not None}

        try:
            response = await self.http_client.post(url, json=data, headers=headers)
            response.raise_for_status()
            result = response.json()

            # 解析返回的图片列表
            images = []
            for idx, item in enumerate(result.get("data", [])):
                url_val = item.get("url", "")
                b64_val = item.get("b64_json", "")
                # 若返回 base64，保存到本地缓存
                local_path = self._save_b64_image(b64_val, enhanced_prompt, idx) if b64_val else ""
                images.append({
                    "url": url_val,
                    "b64_json": b64_val,
                    "local_path": local_path,
                    "revised_prompt": item.get("revised_prompt", ""),
                    "width": size.split("x")[0],
                    "height": size.split("x")[1]
                })

            return {
                "success": True,
                "images": images,
                "model": model,
                "prompt": enhanced_prompt,
                "response_format": response_format,
                "api_style": api_style.value if api_style else None,
                "visual_style": visual_style.value if isinstance(visual_style, VisualStyle) else str(visual_style),
            }

        except httpx.HTTPStatusError as e:
            # HTTP 状态码错误（4xx/5xx）
            return {
                "success": False,
                "error": f"API错误: {e.response.status_code}",
                "details": e.response.text,
                "images": [],
            }
        except Exception as e:
            # 其它异常（网络、解析等）
            return {
                "success": False,
                "error": str(e),
                "images": []
            }

    def _enhance_prompt(self, prompt: str, visual_style: VisualStyle) -> str:
        """增强提示词

        根据视觉风格追加对应的英文修饰词（如 photorealistic, high detail），
        并统一追加分辨率要求。检测到中文 prompt 时追加 Chinese business context。
        """
        # 风格 → 修饰词映射
        style_modifiers = {
            VisualStyle.REALISTIC: "photorealistic, high detail, professional photography",
            VisualStyle.ILLUSTRATION: "digital illustration, clean lines, flat design",
            VisualStyle.ABSTRACT: "abstract art, geometric shapes, modern",
            VisualStyle.PROFESSIONAL: "professional, clean, modern business style",
            VisualStyle.MINIMALIST: "minimalist design, clean background, simple",
            VisualStyle.PHOTOGRAPHIC: "professional photograph, studio lighting, high resolution",
            VisualStyle.INFORMATIVE: "informative illustration, clear visual hierarchy, clean labels",
        }

        modifier = style_modifiers.get(visual_style, "")

        # 拼接原始 prompt + 风格修饰词
        enhanced = f"{prompt}, {modifier}" if modifier else prompt

        # 统一追加分辨率要求
        enhanced += ", 4K, high resolution, detailed"

        # 中文 prompt 追加业务上下文提示，帮助模型理解场景
        if any('\u4e00' <= c <= '\u9fff' for c in prompt):
            enhanced += ", Chinese business context"

        return enhanced

    def _save_b64_image(self, b64_value: str, prompt: str, index: int = 0) -> str:
        """将 base64 图片数据保存到本地缓存

        用 prompt+index+b64 前缀做 MD5 生成文件名，避免重复保存。
        """
        if not b64_value:
            return ""
        try:
            raw = base64.b64decode(b64_value)
            # 用内容哈希做文件名，同一图片不会重复下载
            digest = hashlib.md5((prompt + str(index) + b64_value[:64]).encode()).hexdigest()[:10]
            path = Path(self.cache_dir) / f"openai_{digest}.png"
            # 仅在文件不存在或为空时写入
            if not path.exists() or path.stat().st_size == 0:
                path.write_bytes(raw)
            return str(path.resolve())
        except Exception:
            return ""

    async def generate_variations(
        self,
        image_url: str,
        n: int = 2
    ) -> Dict[str, Any]:
        """基于现有图片生成变体

        注意：仅 dall-e-2 支持 variations 接口。

        Args:
            image_url: 原图URL
            n: 变体数量

        Returns:
            {success, images: [{url}]} 或 {success: False, error}
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "API key未配置",
                "images": []
            }

        model = (self.defaults.get("model") or "gpt-image-1").strip()
        # variations 仅 dall-e-2 支持
        if model != "dall-e-2":
            return {"success": False, "error": "variations_only_supported_by_dalle_2", "images": []}

        url = f"{self.api_base}/images/variations"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        try:
            # 先下载原图字节
            img = await self._fetch_image(image_url)
            # 用 multipart 上传原图
            response = await self.http_client.post(
                url,
                files={"image": ("image.png", img, "image/png")},
                data={"n": n, "size": "1024x1024"},
                headers=headers
            )
            response.raise_for_status()
            result = response.json()

            images = []
            for item in result.get("data", []):
                images.append({
                    "url": item.get("url", "")
                })

            return {
                "success": True,
                "images": images
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "images": []
            }

    async def _fetch_image(self, url: str) -> bytes:
        """下载图片为字节流（用于 variations/edits 需要上传原图的场景）"""
        response = await self.http_client.get(url)
        response.raise_for_status()
        return response.content

    async def edit_image(
        self,
        image_url: str,
        mask_url: str,
        prompt: str
    ) -> Dict[str, Any]:
        """编辑图片（局部修改）

        注意：仅 dall-e-2 支持 edits 接口。
        蒙版中透明区域（alpha=0）表示需要重新生成的区域。

        Args:
            image_url: 原图URL
            mask_url: 蒙版URL（透明区域为待编辑区）
            prompt: 编辑提示词

        Returns:
            {success, image: url} 或 {success: False, error}
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "API key未配置"
            }

        model = (self.defaults.get("model") or "gpt-image-1").strip()
        # edits 仅 dall-e-2 支持
        if model != "dall-e-2":
            return {"success": False, "error": "edits_only_supported_by_dalle_2"}

        url = f"{self.api_base}/images/edits"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        try:
            # 下载原图和蒙版
            img = await self._fetch_image(image_url)
            mask = await self._fetch_image(mask_url)
            # multipart 上传原图 + 蒙版 + 提示词
            response = await self.http_client.post(
                url,
                files={
                    "image": ("image.png", img, "image/png"),
                    "mask": ("mask.png", mask, "image/png"),
                },
                data={
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024"
                },
                headers=headers
            )
            response.raise_for_status()
            result = response.json()

            return {
                "success": True,
                "image": result["data"][0].get("url", "")
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def close(self):
        """关闭HTTP客户端，释放连接池资源"""
        await self.http_client.aclose()


# CrewAI Tool 包装
def get_image_generator_tool():
    """返回CrewAI可用的Tool

    将 ImageGenerator 包装成 CrewAI @tool，供 Agent 自动调用。
    内部用 asyncio.run 驱动异步方法。
    """
    from crewai.tools import tool

    @tool("image_generator")
    def image_generator_tool(
        prompt: str,
        visual_style: str = "professional",
        api_style: str = "",
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
        model: str = "",
        response_format: str = "",
    ) -> str:
        """
        生成文章配图。

        Args:
            prompt: 图片描述，如文章标题或主题
            visual_style: 业务视觉风格，可选 realistic/illustration/abstract/professional/minimalist/photographic/informative
            api_style: OpenAI 图片 API 风格，可选 vivid/natural（仅部分模型支持）
            size: 尺寸，可选 1024x1024/1792x1024/1024x1792
            quality: 质量 standard/hd
            n: 生成数量
            model: 模型名称，留空则使用配置文件默认值
            response_format: url/b64_json，留空则使用配置文件默认值

        Returns:
            JSON格式的生成结果
        """
        import asyncio

        async def _run() -> Dict[str, Any]:
            generator = ImageGenerator()
            try:
                # 归一化 api_style（CrewAI 传入的是字符串）
                api_style_val = api_style.strip().lower() if isinstance(api_style, str) else ""
                api_style_enum = OpenAIImageStyle(api_style_val) if api_style_val in {"vivid", "natural"} else None

                # 归一化 visual_style
                visual_style_val = visual_style.strip().lower() if isinstance(visual_style, str) else ""
                # 兼容：若传入的是 vivid/natural（属 api_style），自动归类
                if visual_style_val in {"vivid", "natural"} and not api_style_enum:
                    api_style_enum = OpenAIImageStyle(visual_style_val)
                    visual_style_val = generator.defaults.get("visual_style") or "professional"

                try:
                    visual_style_enum = VisualStyle(visual_style_val) if visual_style_val else VisualStyle.PROFESSIONAL
                except Exception:
                    return {"success": False, "error": "visual_style_invalid", "images": []}

                return await generator.generate(
                    prompt=prompt,
                    visual_style=visual_style_enum,
                    api_style=api_style_enum,
                    size=size,
                    quality=quality,
                    n=int(n) if isinstance(n, int) or (isinstance(n, str) and str(n).isdigit()) else 1,
                    model=model.strip() if isinstance(model, str) and model.strip() else None,
                    response_format=response_format.strip() if isinstance(response_format, str) and response_format.strip() else None,
                )
            finally:
                await generator.close()

        result = asyncio.run(_run())
        return json.dumps(result, ensure_ascii=False, indent=2)

    return image_generator_tool


if __name__ == "__main__":
    # 模块自测（需要 API key）
    import asyncio

    async def test():
        generator = ImageGenerator()

        result = await generator.generate(
            prompt="A business professional reading a book in modern office",
            visual_style=ImageStyle.PROFESSIONAL,
            size="1024x1024"
        )

        print(json.dumps(result, ensure_ascii=False, indent=2))

        await generator.close()

    asyncio.run(test())
