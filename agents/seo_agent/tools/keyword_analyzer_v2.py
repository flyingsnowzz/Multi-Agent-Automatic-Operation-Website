#!/usr/bin/env python3
"""关键词分析 V2 — LLM 方案。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


def _get_api_key() -> Optional[str]:
    for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return None


KEYWORD_ANALYSIS_PROMPT = """你是一位 SEO 关键词分析专家。请阅读以下文章，完成关键词分析。

## 文章内容

{content}

## 分析要求

1. **主关键词识别**：文章最核心的 1 个关键词
2. **次关键词识别**：密切相关的 3-5 个次关键词
3. **长尾关键词**：2-3 个具体的长尾疑问词/短语
4. **LSI 语义词**：与主关键词语义相关的 5-8 个词
5. **关键词布局评估**：标题/首段/H2/密度
6. **优化建议**：3-5 条具体可行的建议

只输出一个 JSON 对象：
{{
  "primary_keyword": "主关键词",
  "secondary_keywords": ["次关键词1"],
  "long_tail_keywords": ["长尾词1"],
  "lsi_words": ["语义词1"],
  "keyword_density": 2.5,
  "distribution": {{
    "title": true,
    "first_paragraph": true,
    "h2_headings": ["含关键词的H2"],
    "last_section": true
  }},
  "assessment": {{
    "score": 85,
    "issues": ["问题"],
    "passed_checks": ["通过项"],
    "suggestions": ["建议"]
  }}
}}"""


class KeywordAnalyzerV2:
    """LLM 关键词分析器 — V2 方案。"""

    def __init__(self, model="gpt-4o-mini", base_url=None, api_key=None):
        self.model = model
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.api_key = api_key or _get_api_key()

    def _fallback(self, target_keyword=""):
        return {
            "error": "no_api_key",
            "primary_keyword": target_keyword,
            "secondary_keywords": [],
            "long_tail_keywords": [],
            "lsi_words": [],
            "keyword_density": 0,
            "distribution": {"title": False, "first_paragraph": False, "headings": [], "heading_count": 0, "last_section": False},
            "assessment": {"score": 0, "issues": ["no_api_key"], "passed_checks": [], "suggestions": []},
            "analyzer": "v2_llm",
        }

    def _truncate(self, text, max_chars=8000):
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n\n...（已截断）"

    async def _call_llm(self, prompt):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai 未安装: pip install openai")
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        resp = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "只输出 JSON，不要任何额外文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3, max_tokens=1500,
        )
        return resp.choices[0].message.content or ""

    def _extract_json(self, text):
        s = text.strip()
        if "```" in s:
            m = re.search(r"\{[\s\S]*\}", s)
            if m:
                s = m.group(0)
        return json.loads(s)

    async def analyze(self, content, target_keyword=""):
        if not self.api_key:
            return self._fallback(target_keyword)

        hint = f"\n\n参考目标关键词：{target_keyword}" if target_keyword else ""
        prompt = KEYWORD_ANALYSIS_PROMPT.format(content=self._truncate(content)) + hint

        try:
            raw = await self._call_llm(prompt)
            result = self._extract_json(raw)
        except Exception as e:
            return self._fallback(target_keyword)

        dist = result.get("distribution") or {}
        assessment = result.get("assessment") or {}
        return {
            "primary_keyword": result.get("primary_keyword") or target_keyword,
            "secondary_keywords": result.get("secondary_keywords") or [],
            "long_tail_keywords": result.get("long_tail_keywords") or [],
            "lsi_words": result.get("lsi_words") or [],
            "keyword_density": result.get("keyword_density"),
            "distribution": {
                "title": bool(dist.get("title")),
                "first_paragraph": bool(dist.get("first_paragraph")),
                "headings": dist.get("h2_headings") or [],
                "heading_count": len(dist.get("h2_headings") or []),
                "last_section": bool(dist.get("last_section")),
            },
            "assessment": {
                "score": int(assessment.get("score") or 0),
                "issues": assessment.get("issues") or [],
                "passed_checks": assessment.get("passed_checks") or [],
                "suggestions": assessment.get("suggestions") or [],
            },
            "analyzer": "v2_llm",
            "model_used": self.model,
        }
