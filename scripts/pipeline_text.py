#!/usr/bin/env python3
"""Text helpers shared by Redis pipeline workers."""

from __future__ import annotations

import html
import re
from typing import Any, Mapping, Optional


def clean_article_text(text: Any, *, limit: Optional[int] = None) -> str:
    value = html.unescape(str(text or ""))
    value = re.sub(r"<script[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if limit and limit > 0:
        return value[:limit]
    return value


def article_source_content(item: Mapping[str, Any], *, limit: Optional[int] = None) -> str:
    content = (
        item.get("source_content")
        or item.get("content")
        or item.get("description")
        or item.get("summary")
        or ""
    )
    return clean_article_text(content, limit=limit)
