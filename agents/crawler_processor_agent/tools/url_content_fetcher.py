#!/usr/bin/env python3
"""
URL 原文抓取工具。
从 crawler_news_main.original_url 抓取网页原文，提取正文文本。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    success: bool
    content: str
    status_code: Optional[int]
    error: Optional[str]


class URLContentFetcher:
    """从 original_url 抓取原文正文。"""

    def __init__(self, timeout: int = 15, max_content_chars: int = 10000):
        self.timeout = timeout
        self.max_content_chars = max_content_chars

    async def fetch(self, url: str) -> FetchResult:
        """抓取单个 URL 的正文文本。

        Returns:
            FetchResult with success/content/error.
        """
        if not url or not url.startswith("http"):
            return FetchResult(url=url, success=False, content="", status_code=None, error="invalid_url")

        try:
            import httpx
        except ImportError:
            # Fallback to urllib
            return await self._fetch_urllib(url)

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; MultiAgentBot/1.0)"
                })
                if resp.status_code >= 400:
                    return FetchResult(url=url, success=False, content="", status_code=resp.status_code,
                                       error=f"HTTP {resp.status_code}")

                html = resp.text
                text = self._extract_text(html)
                return FetchResult(url=url, success=True, content=text[:self.max_content_chars],
                                   status_code=resp.status_code, error=None)
        except Exception as e:
            return FetchResult(url=url, success=False, content="", status_code=None, error=str(e))

    async def _fetch_urllib(self, url: str) -> FetchResult:
        """urllib 降级方案。"""
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MultiAgentBot/1.0)"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                text = self._extract_text(html)
                return FetchResult(url=url, success=True, content=text[:self.max_content_chars],
                                   status_code=resp.status, error=None)
        except urllib.error.HTTPError as e:
            return FetchResult(url=url, success=False, content="", status_code=e.code, error=f"HTTP {e.code}")
        except Exception as e:
            return FetchResult(url=url, success=False, content="", status_code=None, error=str(e))

    def _extract_text(self, html: str) -> str:
        """从 HTML 中提取正文文本。"""
        # Remove scripts, styles, comments
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

        # Strip all tags
        text = re.sub(r'<[^>]+>', ' ', html)

        # Decode HTML entities
        import html as html_mod
        text = html_mod.unescape(text)

        # Normalize whitespace
        text = re.sub(r'[\n\r\t]+', '\n', text)
        text = re.sub(r' {2,}', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()


async def fetch_article_content(
    url: str,
    timeout: int = 15,
) -> Tuple[str, Optional[str]]:
    """便捷函数：抓取文章内容。

    Returns:
        (content, error). error 为 None 表示成功。
    """
    fetcher = URLContentFetcher(timeout=timeout)
    result = await fetcher.fetch(url)
    if result.success:
        return result.content, None
    return "", result.error or "fetch_failed"
