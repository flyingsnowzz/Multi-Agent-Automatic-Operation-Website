#!/usr/bin/env python3
"""关键词分析 V2 — LLM 方案。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

try:
    from agents.seo_agent.tools.keyword_analyzer_v1 import STOP_WORDS
except Exception:
    STOP_WORDS = set()

EXTRA_STOP_WORDS = {
    "在科", "与中", "你必", "须做", "附避", "坑指", "预登", "信息", "深度",
    "解读", "指南", "这件", "必须", "进行", "通过", "一个", "一种",
}


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

1. **关键词提取**：从文章中提取 8-10 个极短关键词（每个1-3汉字，如"蛋白质""囊泡""机制"；英文1-2单词）。必须拆成最细粒度的单词，不要组合词，不要短语。例如"沿海城市气候韧性"应拆为"沿海""气候""韧性""防灾"。
2. **关键词布局评估**：标题/首段/H2/密度
3. **优化建议**：3-5 条具体可行的建议

只输出一个 JSON 对象：
{{
  "keywords": ["短关键词1", "短关键词2", "短关键词3"],
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
        keywords = self._normalize_keywords([], target_keyword=target_keyword)
        return {
            "error": "no_api_key",
            "primary_keyword": keywords[0] if keywords else target_keyword,
            "keywords": keywords,
            "secondary_keywords": keywords[1:],
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

    def _split_keyword(self, keyword: Any) -> List[str]:
        text = str(keyword or "").strip()
        if not text:
            return []
        text = re.sub(r"[【】\[\]（）()《》“”\"'|+＋:：,，。；;！!？?、/\\\\]", " ", text)
        text = re.sub(r"\b\d{4}\b|\d+小时|\d+件事|\d+", " ", text)
        tokens: List[str] = []
        for match in re.finditer(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,})?|[\u4e00-\u9fff]+", text):
            part = match.group(0).strip()
            if not part:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]+", part):
                try:
                    import jieba
                    pieces = [t.strip() for t in jieba.lcut(part) if t.strip()]
                except Exception:
                    pieces = [part[i : i + 2] for i in range(0, len(part), 2)]
                for piece in pieces:
                    if len(piece) > 4:
                        tokens.extend(piece[i : i + 2] for i in range(0, len(piece), 2))
                    else:
                        tokens.append(piece)
            else:
                tokens.append(part)
        return tokens

    def _normalize_keywords(self, values: Any, target_keyword: str = "", limit: int = 10) -> List[str]:
        raw: List[Any] = []
        if isinstance(values, list):
            raw.extend(values)
        elif values:
            raw.append(values)
        if target_keyword:
            raw.append(target_keyword)

        out: List[str] = []
        seen = set()
        for item in raw:
            for token in self._split_keyword(item):
                token = token.strip().lower() if re.fullmatch(r"[A-Za-z\s]+", token.strip()) else token.strip()
                if not token:
                    continue
                if token in STOP_WORDS or token in EXTRA_STOP_WORDS:
                    continue
                if re.fullmatch(r"\d+", token):
                    continue
                if re.search(r"[\u4e00-\u9fff]", token):
                    if len(token) < 2 or len(token) > 4:
                        continue
                else:
                    if len(token) < 3:
                        continue
                if token in seen:
                    continue
                seen.add(token)
                out.append(token)
                if len(out) >= limit:
                    return out
        return out

    async def analyze(self, content, target_keyword=""):
        if not self.api_key:
            return self._fallback(target_keyword)

        hint = f"\n\n参考目标关键词：{target_keyword}" if target_keyword else ""
        prompt = KEYWORD_ANALYSIS_PROMPT.replace("{content}", self._truncate(content)) + hint

        try:
            raw = await self._call_llm(prompt)
            result = self._extract_json(raw)
        except Exception as e:
            import logging
            logging.getLogger("keyword_v2").warning(f"V2 LLM call failed: {e}")
            return self._fallback(target_keyword)

        dist = result.get("distribution") or {}
        assessment = result.get("assessment") or {}
        keywords = self._normalize_keywords(
            result.get("keywords") or result.get("secondary_keywords") or [],
            target_keyword=target_keyword,
        )
        return {
            "primary_keyword": keywords[0] if keywords else (result.get("primary_keyword") or target_keyword),
            "keywords": keywords,
            "secondary_keywords": keywords[1:],
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
        }
