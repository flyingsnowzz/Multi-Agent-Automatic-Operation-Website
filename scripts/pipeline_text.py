#!/usr/bin/env python3
"""Text helpers shared by the LangGraph article pipeline.

Beginner mental model:
    Crawler content may contain HTML tags, scripts, line breaks, and messy
    whitespace. Agents work better when they receive clean plain text. These
    helpers make that cleaning consistent across workers.

The LangGraph runner and graph nodes both need the same source-text cleanup.
These helpers normalize HTML-ish crawler text into plain text and provide one
consistent way to recover the original source content from a payload/state
object.
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping, Optional


def clean_article_text(text: Any, *, limit: Optional[int] = None) -> str:
    """Normalize crawler HTML or messy text into compact plain article text."""
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
    """Extract and clean the best available source-body field from a payload."""
    # Different stages may name the body differently. Prefer source_content, then
    # content, then shorter fallback fields.
    # This priority matters: source_content is the canonical graph snapshot,
    # while description/summary may only be short crawler metadata.
    content = (
        item.get("source_content")
        or item.get("content")
        or item.get("description")
        or item.get("summary")
        or ""
    )
    return clean_article_text(content, limit=limit)
