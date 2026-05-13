#!/usr/bin/env python3
"""
图片生成工具 - ImageAgent
调用DALL-E或Midjourney API生成配图
"""

import os
import json
import httpx
from typing import Dict, List, Any, Optional
from enum import Enum


class ImageStyle(str, Enum):
    """图片风格"""
    REALISTIC = "realistic"
    ILLUSTRATION = "illustration"
    ABSTRACT = "abstract"
    PROFESSIONAL = "professional"
    MINIMALIST = "minimalist"
    PHOTOGRAPHIC = "photographic"


class ImageGenerator:
    """图片生成工具"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.api_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.http_client = httpx.AsyncClient(timeout=60.0)
    
    async def generate(
        self,
        prompt: str,
        style: ImageStyle = ImageStyle.PROFESSIONAL,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1
    ) -> Dict[str, Any]:
        """
        生成图片
        
        Args:
            prompt: 图片描述提示词
            style: 风格
            size: 尺寸，可选 1024x1024, 1792x1024, 1024x1792
            quality: 质量 standard/hd
            n: 生成数量
            
        Returns:
            生成结果
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "API key未配置",
                "images": []
            }
        
        # 增强prompt
        enhanced_prompt = self._enhance_prompt(prompt, style)
        
        # 调用OpenAI DALL-E API
        url = f"{self.api_base}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "dall-e-3",
            "prompt": enhanced_prompt,
            "n": n,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "style": style.value if style in [ImageStyle.NATURAL, ImageStyle.VIVID] else None
        }
        
        # 移除None值
        data = {k: v for k, v in data.items() if v is not None}
        
        try:
            response = await self.http_client.post(url, json=data, headers=headers)
            response.raise_for_status()
            result = response.json()
            
            images = []
            for item in result.get("data", []):
                images.append({
                    "url": item.get("url", ""),
                    "revised_prompt": item.get("revised_prompt", ""),
                    "width": size.split("x")[0],
                    "height": size.split("x")[1]
                })
            
            return {
                "success": True,
                "images": images,
                "model": "dall-e-3",
                "prompt": enhanced_prompt
            }
            
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "error": f"API错误: {e.response.status_code}",
                "details": e.response.text
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "images": []
            }
    
    def _enhance_prompt(self, prompt: str, style: ImageStyle) -> str:
        """增强提示词"""
        # 风格增强词
        style_modifiers = {
            ImageStyle.REALISTIC: "photorealistic, high detail, professional photography",
            ImageStyle.ILLUSTRATION: "digital illustration, clean lines, flat design",
            ImageStyle.ABSTRACT: "abstract art, geometric shapes, modern",
            ImageStyle.PROFESSIONAL: "professional, clean, modern business style",
            ImageStyle.MINIMALIST: "minimalist design, clean background, simple",
            ImageStyle.PHOTOGRAPHIC: "professional photograph, studio lighting, high resolution"
        }
        
        modifier = style_modifiers.get(style, "")
        
        # 构建增强提示词
        enhanced = f"{prompt}, {modifier}" if modifier else prompt
        
        # 添加分辨率和质量要求
        enhanced += ", 4K, high resolution, detailed"
        
        # 添加中文处理的说明
        if any('\u4e00' <= c <= '\u9fff' for c in prompt):
            enhanced += ", Chinese business context"
        
        return enhanced
    
    async def generate_variations(
        self,
        image_url: str,
        n: int = 2
    ) -> Dict[str, Any]:
        """
        基于现有图片生成变体
        
        Args:
            image_url: 原图URL
            n: 变体数量
            
        Returns:
            变体结果
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "API key未配置",
                "images": []
            }
        
        url = f"{self.api_base}/images/variations"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        try:
            response = await self.http_client.post(
                url,
                files={"image": (await self._fetch_image(image_url))},
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
        """获取图片数据"""
        response = await self.http_client.get(url)
        response.raise_for_status()
        return response.content
    
    async def edit_image(
        self,
        image_url: str,
        mask_url: str,
        prompt: str
    ) -> Dict[str, Any]:
        """
        编辑图片（局部修改）
        
        Args:
            image_url: 原图URL
            mask_url: 蒙版URL
            prompt: 编辑提示词
            
        Returns:
            编辑结果
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "API key未配置"
            }
        
        url = f"{self.api_base}/images/edits"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        try:
            response = await self.http_client.post(
                url,
                files={
                    "image": (await self._fetch_image(image_url)),
                    "mask": (await self._fetch_image(mask_url))
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
        """关闭HTTP客户端"""
        await self.http_client.aclose()


# CrewAI Tool 包装
def get_image_generator_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("image_generator")
    def image_generator_tool(
        prompt: str,
        style: str = "professional",
        size: str = "1024x1024",
        quality: str = "standard"
    ) -> str:
        """
        生成文章配图。
        
        Args:
            prompt: 图片描述，如文章标题或主题
            style: 风格，可选 realistic/illustration/abstract/professional/minimalist/photographic
            size: 尺寸，可选 1024x1024/1792x1024/1024x1792
            quality: 质量 standard/hd
            
        Returns:
            JSON格式的生成结果
        """
        import asyncio
        
        generator = ImageGenerator()
        
        try:
            result = asyncio.run(generator.generate(
                prompt=prompt,
                style=ImageStyle(style),
                size=size,
                quality=quality
            ))
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            asyncio.run(generator.close())
    
    return image_generator_tool


if __name__ == "__main__":
    # 测试（需要API key）
    import asyncio
    
    async def test():
        generator = ImageGenerator()
        
        result = await generator.generate(
            prompt="A business professional reading a book in modern office",
            style=ImageStyle.PROFESSIONAL,
            size="1024x1024"
        )
        
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        await generator.close()
    
    asyncio.run(test())
