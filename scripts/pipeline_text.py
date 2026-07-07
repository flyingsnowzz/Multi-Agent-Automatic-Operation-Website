#!/usr/bin/env python3
"""Text helpers shared by Redis pipeline workers.

Beginner mental model:
    Crawler content may contain HTML tags, scripts, line breaks, and messy
    whitespace. Agents work better when they receive clean plain text. These
    helpers make that cleaning consistent across workers.

Workers pass article bodies through Redis messages. These helpers normalize
HTML-ish crawler text into plain text and provide one consistent way to recover
the original source content from a payload.
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping, Optional


def clean_article_text(text: Any, *, limit: Optional[int] = None) -> str:
    # Convert None/non-string values safely, then decode HTML entities such as
    # &nbsp; before removing tags.
    value = html.unescape(str(text or ""))
    # Remove script/style blocks completely; they are crawler noise, not article
    # text, and can confuse downstream agents.
    value = re.sub(r"<script[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    # Strip remaining HTML tags and collapse whitespace to one line.
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    # Some agents only need a capped excerpt. Limit is applied after cleaning so
    # the character budget is not wasted on tags.
    if limit and limit > 0:
        return value[:limit]
    return value


def article_source_content(item: Mapping[str, Any], *, limit: Optional[int] = None) -> str:
    # Different stages may name the body differently. Prefer source_content, then
    # content, then shorter fallback fields.
    content = (
        item.get("source_content")
        or item.get("content")
        or item.get("description")
        or item.get("summary")
        or ""
    )
    return clean_article_text(content, limit=limit)
