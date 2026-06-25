#!/usr/bin/env python3
"""
Meta 标签生成工具 — LLM 方案。

调用 LLM 阅读文章，生成：
- SEO 友好的 Meta Title（30-60 字符）
- 吸引点击的 Meta Description（120-160 字符）
- Open Graph 标签
- Twitter Card 标签
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import yaml


def _get_api_key() -> Optional[str]:
    for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return None


META_PROMPT = """你是一位 SEO Meta 标签优化专家。请为以下文章生成优化的 Meta 标签。

## 文章信息

标题：{title}
主关键词：{primary_keyword}

文章摘要（前 1000 字）：
{content_preview}

## 生成要求

1. **Meta Title**：
   - 长度 30-60 字符（中文约 15-30 字）
   - 包含主关键词，尽量靠前
   - 自然可读，不要堆砌
   - 末尾加品牌名 "{brand}"

2. **Meta Description**：
   - 长度 120-160 字符（中文约 60-80 字）
   - 包含主关键词
   - 概括文章核心价值，激发点击欲望
   - 不要照搬文章第一段

3. **生成理由**：简要说明各标签的设计思路

## 输出格式

只输出一个 JSON 对象：

{{
  "meta_title": "优化后的标题（含品牌名）",
  "meta_description": "优化后的描述",
  "title_length": 字符数,
  "description_length": 字符数,
  "reasoning": {{
    "title": "标题设计思路",
    "description": "描述设计思路"
  }}
}}"""


class MetaGeneratorLLM:
    """LLM Meta 标签生成器。"""

    def __init__(
        self,
        brand_name: str = "",
        model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        config_path: str = "agents/seo_agent/config.yaml",
    ):
        self.brand_name = brand_name
        self.model = model
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.api_key = api_key or _get_api_key()
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _truncate(self, text: str, max_chars: int = 1500) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n\n...（已截断）"

    async def _call_llm(self, prompt: str) -> str:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai 未安装，请运行: pip install openai")

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        resp = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一位 SEO Meta 标签优化专家。只输出 JSON，不要任何额外文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=800,
        )
        return resp.choices[0].message.content or ""

    def _extract_json(self, text: str) -> Dict[str, Any]:
        s = text.strip()
        if "```" in s:
            m = re.search(r"\{[\s\S]*\}", s)
            if m:
                s = m.group(0)
        return json.loads(s)

    async def generate(
        self,
        title: str,
        content: str,
        primary_keyword: str,
    ) -> Dict[str, Any]:
        """生成 Meta 标签。

        Args:
            title: 文章标题
            content: 文章正文
            primary_keyword: 主关键词
        """
        if not self.api_key:
            return {
                "error": "no_api_key",
                "meta_title": title,
                "meta_description": "",
            }

        brand = self.brand_name or "TechAI Insight"
        prompt = META_PROMPT.format(
            title=title,
            primary_keyword=primary_keyword,
            content_preview=self._truncate(content),
            brand=brand,
        )

        try:
            raw = await self._call_llm(prompt)
            result = self._extract_json(raw)
        except Exception as e:
            return {
                "error": str(e),
                "meta_title": title,
                "meta_description": "",
            }

        return {
            "meta_title": result.get("meta_title") or title,
            "meta_description": result.get("meta_description") or "",
            "title_length": int(result.get("title_length") or len(result.get("meta_title") or title)),
            "description_length": int(result.get("description_length") or len(result.get("meta_description") or "")),
            "reasoning": result.get("reasoning") or {},
            "model_used": self.model,
        }
