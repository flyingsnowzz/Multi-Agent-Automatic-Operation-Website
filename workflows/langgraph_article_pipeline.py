"""Formal LangGraph article pipeline.

Beginner mental model:
    The production runner reads article ids in batches, computes batch-normalized
    scoring, then sends each article through this graph. Every node reads and
    writes the same ArticleGraphState instead of passing queue messages between
    separate workers.

Safety boundary:
    Importing this file does not start the feeder or publish to CMS. The graph
    only runs when scripts/run_langgraph_batch.py, scripts/run_langgraph_pipeline.py,
    or a test explicitly calls run_article_graph().

Current architecture:
    LangGraph is the only article pipeline scheduler in this repository.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

from dotenv import load_dotenv

from scripts.db_config import crawler_table_config
from scripts.pipeline_text import article_source_content, clean_article_text
from scripts.publish_common import (
    cover_decision,
    fetch_existing_cover,
    is_forwarded_article,
    normalize_forwarded_content_md,
    slugify,
    validate_cover_ready,
    validate_publish_prerequisites,
)
from scripts.prompt_db_logger import log_agent_prompt


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Default routing gates are only fallbacks when .env has no value. Production
# tuning should happen through .env / .env.example instead of route functions.
DEFAULT_AI_SCORE_THRESHOLD = 75
DEFAULT_QUALITY_PASS_THRESHOLD = 70
DEFAULT_REWRITE_QUALITY_THRESHOLD = 70


class ArticleGraphState(TypedDict, total=False):
    """Shared state passed between LangGraph nodes.

    This replaces cross-worker message handoff. Every Agent reads the same
    source_content snapshot from this state instead of each Agent fetching the
    source again.
    """

    # Source identity and crawler metadata. These fields come from
    # crawler_news_main and should remain close to the original source article.
    article_id: Any
    id: Any
    title: str
    description: str
    source_url: str
    original_url: str
    source_image: str
    image: str
    cover_image: str
    raw_content: str
    source_content: str
    content: str

    # First-stage scoring. run_langgraph_batch.py usually fills ai_score before
    # the graph starts so scoring_node can preserve batch-normalized scoring.
    ai_score: float
    scoring_mode: str
    quality_score: float
    quality_route: str

    # Rewrite branch outputs. These fields only exist when the original article
    # did not pass the quality gate and the graph entered Research/Writer/Editor.
    research_result: Dict[str, Any]
    research_prompt: str
    writer_prompt: str
    generated_title: str
    generated_content_md: str
    generated_meta_description: str
    rewrite_quality_after: float
    quality_after: float
    edited_title: str
    edited_content_md: str

    # Late publish-stage outputs. These are written only when run_late_stages is
    # true and the article has not been blocked by an earlier gate.
    seo_meta_title: str
    seo_meta_description: str
    seo_keywords: List[str]
    image_prompt: str
    image_url: str
    image_local_path: str
    featured_image: str
    cms_result: Dict[str, Any]
    cms_status: str
    cms_article_id: str
    cms_article_url: str

    # Runner control flags and terminal diagnostics. Nodes should set
    # stop_reason instead of raising for expected business stops.
    publish_dry_run: bool
    defer_cms_publish: bool
    persist_audit: bool
    audit_persisted: bool
    run_late_stages: bool
    stop_reason: str
    errors: List[str]
    warnings: List[str]


def _env_float(name: str, default: float) -> float:
    """Read a float threshold from the environment with a safe fallback."""

    # Environment thresholds are operator-tunable. Bad values should not crash
    # imports/tests; fall back to the documented default.
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    """Read an operator boolean from .env using common true spellings."""

    # Operators edit .env by hand on servers, so accept the spellings people
    # naturally use and fall back safely when the key is absent.
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _image_error_should_fallback_to_source(error: Any) -> bool:
    """Return true for provider errors that mean no generated image exists."""

    text = str(error or "").strip().lower()
    return any(token in text for token in {"missing", "no_images", "no image", "empty_image", "empty image"})


def _append(state: ArticleGraphState, key: Literal["errors", "warnings"], value: str) -> None:
    """Append one error or warning message into graph state."""

    # Keep error/warning mutation consistent across nodes. LangGraph state is a
    # dict, so always copy the current list before appending.
    values = list(state.get(key) or [])
    values.append(value)
    state[key] = values


def _image_provider_result_for_audit(result: Dict[str, Any]) -> Dict[str, Any]:
    """Keep image provider output useful for logs without dumping huge payloads."""

    # Provider responses can include the original prompt or large raw payloads.
    # Keep the fields that explain success/failure and where the image landed.
    audited: Dict[str, Any] = {}
    for key in ("success", "provider", "error", "total"):
        if key in result:
            audited[key] = result.get(key)
    if result.get("images"):
        audited["images"] = [
            {
                "url": image.get("url", ""),
                "local_path": image.get("local_path", ""),
                "run_id": image.get("run_id", ""),
                "index": image.get("index"),
            }
            for image in result.get("images", [])
            if isinstance(image, dict)
        ]
    return audited


def _source_content(state: ArticleGraphState, *, limit: int = 8000) -> str:
    """Normalize source text into state and return the cleaned value."""

    # The project has several possible source-content fields. Centralize the
    # extraction so every Agent sees the same cleaned text snapshot.
    content = article_source_content(state, limit=limit)
    state["source_content"] = content
    state["content"] = content
    return content


def _clean_source_text(text: str, limit: int = 3500) -> str:
    """Clean and shorten source text for prompt fallback construction."""

    # Prompt fallbacks need a shorter source excerpt than the graph state keeps.
    return clean_article_text(text, limit=limit)


def _audit_text(value: Any) -> str:
    """Return long text for JSONL audit, capped by PROMPT_AUDIT_TEXT_LIMIT."""

    text = str(value or "")
    try:
        limit = int(os.environ.get("PROMPT_AUDIT_TEXT_LIMIT", "12000"))
    except (TypeError, ValueError):
        limit = 12000
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _env_int(name: str, default: int) -> int:
    """Read an integer environment knob with a safe fallback."""

    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _image_prompt_context(state: ArticleGraphState, *, limit: int = 1200) -> str:
    """Build a compact article excerpt for image-prompt generation."""

    # Use rewritten/editorial content first because that is what the cover needs
    # to match. Fall back to source text when a manual state is incomplete.
    text = (
        state.get("edited_content_md")
        or state.get("generated_content_md")
        or state.get("content_md")
        or state.get("source_content")
        or state.get("content")
        or state.get("description")
        or ""
    )
    return _clean_source_text(str(text), limit=limit)


async def _generate_cover_prompt_with_llm(state: ArticleGraphState, *, fallback_prompt: str) -> Dict[str, Any]:
    """Use a cheap text LLM to turn article context into a stable image prompt."""

    if not _env_bool("IMAGE_PROMPT_LLM_ENABLED", True):
        return {"prompt": fallback_prompt, "used_llm": False, "reason": "disabled"}

    api_key = (
        os.environ.get("IMAGE_PROMPT_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    if not api_key:
        return {"prompt": fallback_prompt, "used_llm": False, "reason": "missing_api_key"}

    title = str(state.get("edited_title") or state.get("generated_title") or state.get("title") or "").strip()
    context = _image_prompt_context(state, limit=_env_int("IMAGE_PROMPT_CONTEXT_CHARS", 1200))
    base_url = os.environ.get("IMAGE_PROMPT_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com"
    model = os.environ.get("IMAGE_PROMPT_MODEL") or "deepseek-v4-flash"
    max_tokens = _env_int("IMAGE_PROMPT_MAX_TOKENS", 220)
    prompt = f"""你是新闻网站封面图提示词编辑。请根据文章生成一个可直接交给文生图模型的中文提示词。

要求：
- 只输出提示词本身，不要解释，不要 JSON。
- 适合资讯/商学院/科技财经媒体封面，专业、真实、新闻感。
- 明确主体、场景、构图、光线、色彩和风格。
- 不要要求图片里出现文字、标题、水印、Logo、二维码。
- 不要出现无关动物、卡通、夸张科幻元素。
- 长度控制在 80-160 个中文字符。

文章标题：{title}

文章内容摘要：
{context}
""".strip()

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=float(os.environ.get("IMAGE_PROMPT_TEMPERATURE", "0.3")),
            max_tokens=max_tokens,
        )
        text = (response.choices[0].message.content or "").strip() if response.choices else ""
        text = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        text = re.sub(r"\s+", " ", text)
        usage = getattr(response, "usage", None)
        usage_payload = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        } if usage else {}
        if not text:
            return {
                "prompt": fallback_prompt,
                "used_llm": False,
                "reason": "empty_llm_output",
                "request_prompt": prompt,
                "usage": usage_payload,
                "model": model,
            }
        return {
            "prompt": text,
            "used_llm": True,
            "reason": "ok",
            "request_prompt": prompt,
            "usage": usage_payload,
            "model": model,
        }
    except Exception as exc:
        return {
            "prompt": fallback_prompt,
            "used_llm": False,
            "reason": f"llm_error:{exc}",
            "request_prompt": prompt,
            "usage": {},
            "model": model,
        }


def _content_html_from_markdown(content: str) -> str:
    """Convert agent Markdown/plain text into CMS-ready HTML."""

    text = str(content or "").strip()
    if not text:
        return ""
    try:
        from markdown import markdown as markdown_to_html

        return markdown_to_html(text, extensions=["extra", "sane_lists"])
    except Exception:
        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
        return "\n".join(f"<p>{p}</p>" for p in paragraphs) or text


def _extract_reference_section(content: Any) -> str:
    """Extract the Writer reference section so Editor cannot drop sources."""

    text = str(content or "").strip()
    if not text:
        return ""
    marker = re.search(r"(?m)^##\s*(参考来源|参考资料)\s*$", text)
    if not marker:
        return ""
    return text[marker.start() :].strip()


def _ensure_reference_section(content: str, source_content: Any) -> str:
    """Append the original reference section when an edit removed it."""

    text = str(content or "").strip()
    if re.search(r"(?m)^##\s*(参考来源|参考资料)\s*$", text):
        return text
    refs = _extract_reference_section(source_content)
    if not refs:
        return text
    return f"{text}\n\n{refs}".strip()


def _source_credit_md(*, label: str, title: Any, url: Any) -> str:
    """Build a compact source credit block for forwarded/reprinted articles."""

    clean_title = str(title or "").strip()
    clean_url = str(url or "").strip()
    if clean_title and clean_url:
        return f"> {label}：[{clean_title}]({clean_url})"
    if clean_url:
        return f"> {label}：{clean_url}"
    if clean_title:
        return f"> {label}：{clean_title}"
    return f"> {label}"


def _ensure_reprint_title(title: str) -> str:
    """Prefix forwarded article titles with a clear reprint marker."""

    text = str(title or "").strip()
    if not text:
        return "转载"
    if text.startswith(("转载｜", "转载 |", "转载：", "【转载】")):
        return text
    return f"转载｜{text}"


def _ensure_reprint_credit(content: str, *, source_title: Any, source_url: Any) -> str:
    """Append a forwarded/reprinted source credit when missing."""

    text = str(content or "").strip()
    if "转载来源" in text or "原文来源" in text:
        return text
    credit = _source_credit_md(label="转载来源", title=source_title, url=source_url)
    return f"{text}\n\n{credit}".strip()


def _build_fallback_writer_prompt(*, title: str, source_content: str, quality_score: Any) -> str:
    """Source-grounded fallback if ResearchAgent fails to return writer_prompt."""

    source = _clean_source_text(source_content, limit=3500)
    return f"""你是 WriterAgent。请把下面原文改写成一篇自然、可发布的中文新闻稿。

## 重要约束
- 必须严格基于原文，不要编造原文没有的人物、数据、引语、研究结论或现场细节。
- 不要写成教程、指南、SEO文章或概念解释文。
- 不要使用“核心概念”“适用场景”“组成要素”“实施步骤”“风险与误区”“参考来源”等模板化小节。
- 不要在正文开头重复输出 Markdown H1 标题。
- 正文控制在 900-1200 中文字左右；如果原文信息很少，可以更短，但要自然完整。
- 写作目标是降低 AI 味：减少路标句、规律短段、空泛升华和对称句。
- 输出只能是 JSON 对象，字段为 article.title、article.meta_description、article.content_md。

## 原标题
{title}

## 原质量分
{quality_score}

## 原文
{source}
"""


async def _load_article_from_mysql(article_id: Any) -> Dict[str, Any]:
    """Read one crawler article for standalone graph runs.

    Important:
        The configured main crawler table only stores metadata and a short
        description. The full article body is sharded by news_id. The LangGraph
        runner must join that body text too, otherwise ScoringAgent only sees a
        summary and scores can be much lower.
    """

    import aiomysql

    tables = crawler_table_config()
    pool = await aiomysql.create_pool(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
        charset="utf8mb4",
        minsize=1,
        maxsize=1,
    )
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # First fetch the stable metadata row. The long article body is
                # not stored here, so this query alone is not enough for Agents.
                await cur.execute(
                    "SELECT id, title, description, original_url, image, publish_date "
                    f"FROM {tables.main_sql} WHERE id=%s LIMIT 1",
                    (article_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return {}
                row = dict(row)
                main_content = ""
                try:
                    # Some existing CMS tables keep the full body directly in
                    # the main article row. The standard crawler_news_main
                    # schema does not have this column, so keep it optional.
                    await cur.execute(
                        f"SELECT content FROM {tables.main_sql} WHERE id=%s LIMIT 1",
                        (article_id,),
                    )
                    main_body_row = await cur.fetchone()
                    if main_body_row and main_body_row.get("content"):
                        main_content = str(main_body_row.get("content") or "")
                except Exception:
                    main_content = ""
                shard_content = ""
                for idx in range(tables.shard_count):
                    # Crawler bodies are horizontally sharded by news_id. Try
                    # every shard and stop on the first body found.
                    await cur.execute(
                        f"SELECT content FROM {tables.shard_sql(idx)} WHERE news_id=%s LIMIT 1",
                        (article_id,),
                    )
                    body_row = await cur.fetchone()
                    if body_row and body_row.get("content"):
                        shard_content = str(body_row.get("content") or "")
                        break
                # Some deployments store the full body in the main table's
                # content column, while the demo crawler schema stores it in
                # numbered shard tables. Prefer shard content when present, but
                # fall back to main content so existing CMS schemas still work.
                body_content = shard_content or main_content
                raw_content = ((row.get("description") or "") + "\n" + body_content).strip()
                row["raw_content"] = raw_content
                row["content"] = clean_article_text(raw_content)
                return row
    finally:
        pool.close()
        await pool.wait_closed()


async def load_source_node(state: ArticleGraphState) -> ArticleGraphState:
    """Load the original article once and store it in source_content."""

    if state.get("stop_reason"):
        return state

    out: ArticleGraphState = dict(state)
    article_id = out.get("article_id") or out.get("id")
    if article_id and not (out.get("title") and article_source_content(out)):
        # Batch runner and single-article runner both arrive here. If the caller
        # passed only article_id, hydrate the graph state from MySQL.
        row = await _load_article_from_mysql(article_id)
        if not row:
            out["stop_reason"] = "source_article_not_found"
            _append(out, "errors", "source_article_not_found")
            return out
        out["id"] = row.get("id")
        out["article_id"] = article_id
        out["title"] = out.get("title") or row.get("title") or ""
        out["description"] = out.get("description") or row.get("description") or ""
        out["raw_content"] = out.get("raw_content") or row.get("raw_content") or ""
        out["content"] = out.get("content") or row.get("content") or ""
        out["source_content"] = out.get("source_content") or row.get("content") or ""
        out["source_url"] = out.get("source_url") or row.get("original_url") or ""
        out["original_url"] = out.get("original_url") or row.get("original_url") or ""
        out["source_image"] = out.get("source_image") or row.get("image") or ""
        out["image"] = out.get("image") or row.get("image") or ""
        out["publish_date"] = out.get("publish_date") or row.get("publish_date")

    source = _source_content(out)
    if not source:
        # Missing source content is a business stop, not an exception. Persisting
        # this as source_blocked later makes it visible in pipeline_audit.
        out["stop_reason"] = "source_content_missing"
        _append(out, "errors", "source_content_missing")
    return out


async def scoring_node(state: ArticleGraphState) -> ArticleGraphState:
    """Call ScoringAgent logic and decide whether article is worth continuing."""

    if state.get("stop_reason"):
        return state
    if state.get("ai_score") is not None:
        # Batch runners may precompute ai_score with the same batch-normalized
        # scoring behavior. In that case this node becomes
        # a pass-through so the graph can continue from the supplied score.
        return state
    from agents.scoring_agent.scoring_summary import summarize_crawler_topics

    out: ArticleGraphState = dict(state)
    result = await asyncio.to_thread(summarize_crawler_topics, [dict(out)], use_ai=True, ai_concurrency=1)
    scores = result.get("article_scores") or []
    score = scores[0] if scores else {}
    overall = score.get("overall_score")
    if overall is None:
        out["stop_reason"] = "ai_score_missing"
        _append(out, "errors", "ai_score_missing")
        return out
    out["ai_score"] = round(float(overall), 2)
    out["scoring_mode"] = "single_raw_not_batch"
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_scoring",
        agent_name="ScoringAgent",
        prompt_type="scoring_result",
        input_payload={"title": out.get("title"), "source_content_chars": len(out.get("source_content") or "")},
        output_payload={"ai_score": out["ai_score"], "raw": score},
    )
    return out


def route_after_scoring(state: ArticleGraphState) -> str:
    """Choose the next graph branch after ai_score is available."""

    # First gate: low-value articles stop before Quality/Rewrite/Image/CMS so
    # the pipeline does not spend expensive model/provider calls on them.
    if state.get("stop_reason"):
        return "stop"
    threshold = _env_float("AI_SCORE_THRESHOLD", DEFAULT_AI_SCORE_THRESHOLD)
    return "quality" if float(state.get("ai_score") or 0) >= threshold else "stop_low_score"


async def stop_low_score_node(state: ArticleGraphState) -> ArticleGraphState:
    """Turn a low ai_score decision into a terminal graph state."""

    out: ArticleGraphState = dict(state)
    out["stop_reason"] = out.get("stop_reason") or "ai_score_below_threshold"
    return out


async def quality_node(state: ArticleGraphState) -> ArticleGraphState:
    """Score original article quality."""

    if state.get("stop_reason"):
        return state
    from agents.quality_agent import QualityAgent

    out: ArticleGraphState = dict(state)
    source_content = _source_content(out, limit=8000)
    # QualityAgent only needs enough content to judge publishability. Keep the
    # prompt bounded so one unusually long crawler row cannot dominate token
    # cost or latency.
    qr = await QualityAgent().score_article(
        {
            "title": out.get("title", ""),
            "content": source_content[:3000],
            "source_url": out.get("source_url", ""),
        }
    )
    qs = round(float(qr.get("quality_score", 0)), 1)
    out["quality_score"] = qs
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_quality",
        agent_name="QualityAgent",
        prompt_type="quality_result",
        input_payload={
            "title": out.get("title"),
            "source_url": out.get("source_url", ""),
            "source_content_chars": len(source_content),
            "source_content": _audit_text(source_content),
        },
        output_payload={"quality_score": qs, "raw": qr},
        model_name=os.environ.get("QUALITY_AGENT_MODEL", ""),
    )
    return out


def route_after_quality(state: ArticleGraphState) -> str:
    """Route original articles to rewrite, late stages, or done."""

    # Quality score is a "can we publish the original?" gate:
    #   low/medium quality -> rewrite
    #   high quality       -> late stages directly
    # This is independent from ai_score, which answers "is the topic worth it?"
    if state.get("stop_reason"):
        return "stop"
    threshold = _env_float("QUALITY_PASS_THRESHOLD", DEFAULT_QUALITY_PASS_THRESHOLD)
    if float(state.get("quality_score") or 0) <= threshold:
        return "rewrite"
    return "seo" if state.get("run_late_stages", True) else "done"


async def research_node(state: ArticleGraphState) -> ArticleGraphState:
    """Build ResearchAgent brief and writer prompt from the same source_content."""

    if state.get("stop_reason"):
        return state
    from agents.research_agent import ResearchAgent

    out: ArticleGraphState = dict(state)
    source_content = _source_content(out, limit=3000)
    title = out.get("title", "")
    # ResearchAgent historically expects a topic-like payload. Build the small
    # topic object here from graph state instead of letting each Agent reread DB.
    topic = {
        "title": title,
        "primary_keyword": str(title)[:20],
        "source_content": source_content,
        "source_title": title,
        "source_summary": str(out.get("description") or "")[:500],
        "source_url": out.get("source_url", ""),
        "content_type": "news",
        "search_intent": "informational",
        "article_overall_score": out.get("ai_score"),
        "quality_score": out.get("quality_score"),
    }
    mode = os.environ.get("RESEARCH_AGENT_MODE", "live")
    res = await ResearchAgent().execute_direct(topic=topic, mode=mode)
    writer_prompt = ""
    if isinstance(res, dict):
        wp = res.get("writer_prompt") or {}
        if isinstance(wp, dict):
            writer_prompt = str(wp.get("prompt_text") or "").strip()
    if not writer_prompt:
        # Do not fail the entire article just because ResearchAgent omitted a
        # writer_prompt. The fallback prompt is source-grounded and lets the
        # rewrite branch continue while leaving an audit warning.
        writer_prompt = _build_fallback_writer_prompt(
            title=title,
            source_content=source_content,
            quality_score=out.get("quality_score"),
        )
        _append(out, "warnings", "research_writer_prompt_missing_fallback_used")
    out["research_result"] = res if isinstance(res, dict) else {}
    out["research_prompt"] = writer_prompt
    out["writer_prompt"] = writer_prompt
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_rewrite",
        agent_name="ResearchAgent",
        prompt_type="writer_prompt_from_research_brief",
        prompt_text=writer_prompt,
        input_payload={"topic": topic, "mode": mode},
        output_payload={"keys": list(res.keys()) if isinstance(res, dict) else []},
        model_name=os.environ.get("RESEARCH_AGENT_MODEL", ""),
    )
    return out


async def writer_node(state: ArticleGraphState) -> ArticleGraphState:
    """Generate rewritten article using ResearchAgent prompt and source_content."""

    if state.get("stop_reason"):
        return state
    from agents.writer_agent import WriterAgent

    out: ArticleGraphState = dict(state)
    source_content = _source_content(out, limit=3000)
    title = out.get("title", "")
    topic = {
        "title": title,
        "primary_keyword": str(title)[:20],
        "source_content": source_content,
        "source_title": title,
        "source_summary": str(out.get("description") or "")[:500],
        "source_url": out.get("source_url", ""),
        "content_type": "news",
        "search_intent": "informational",
        "article_overall_score": out.get("ai_score"),
        "quality_score": out.get("quality_score"),
    }
    materials = out.get("research_result") or {}
    if "research_brief" not in materials:
        materials = dict(materials)
        materials["research_brief"] = {
            "source_snapshot": {
                "source_title": title,
                "source_summary": str(out.get("description") or "")[:500],
                "source_content": source_content[:3000],
            },
            "source_highlights": [str(out.get("description") or "")[:200]],
            "key_facts": [{"fact": str(out.get("description") or "")[:300]}],
            "rewrite_constraints": ["保持原文事实准确", "不要新增原文没有的事实"],
            "risk_points": [],
            "writer_outline": {"sections": []},
        }
    writer = WriterAgent()
    writer._load_prompt = lambda: str(out.get("writer_prompt") or "")
    write = await writer.execute(topic=topic, outline=materials.get("outline"), materials=materials, dry_run=True)
    art = (write or {}).get("article") if isinstance(write, dict) else {}
    content = str((art or {}).get("content_md") or (art or {}).get("content") or "")
    new_title = str((art or {}).get("title") or title)
    if len(content) < 100:
        await log_agent_prompt(
            article_id=out.get("article_id"),
            stage="langgraph_rewrite",
            agent_name="WriterAgent",
            prompt_type="writer_short_result",
            prompt_text=write.get("prompt") if isinstance(write, dict) else "",
            input_payload={"topic": topic},
            output_payload={
                "generated_title": new_title,
                "content_chars": len(content),
                "content_excerpt": content[:300],
                "warnings": (write or {}).get("warnings") if isinstance(write, dict) else [],
                "quality_checks": (write or {}).get("quality_checks") if isinstance(write, dict) else {},
                "statistics": (write or {}).get("statistics") if isinstance(write, dict) else {},
            },
            model_name=os.environ.get("WRITER_AGENT_MODEL", ""),
        )
        fallback_prompt = _build_fallback_writer_prompt(
            title=title,
            source_content=source_content,
            quality_score=out.get("quality_score"),
        )
        fallback_writer = WriterAgent()
        fallback_writer._load_prompt = lambda: fallback_prompt
        write = await fallback_writer.execute(topic=topic, outline=None, materials=materials, dry_run=True)
        art = (write or {}).get("article") if isinstance(write, dict) else {}
        content = str((art or {}).get("content_md") or (art or {}).get("content") or "")
        new_title = str((art or {}).get("title") or title)
        if len(content) < 100:
            await log_agent_prompt(
                article_id=out.get("article_id"),
                stage="langgraph_rewrite",
                agent_name="WriterAgent",
                prompt_type="writer_fallback_short_result",
                prompt_text=write.get("prompt") if isinstance(write, dict) else fallback_prompt,
                input_payload={"topic": topic},
                output_payload={
                    "generated_title": new_title,
                    "content_chars": len(content),
                    "content_excerpt": content[:300],
                    "warnings": (write or {}).get("warnings") if isinstance(write, dict) else [],
                    "quality_checks": (write or {}).get("quality_checks") if isinstance(write, dict) else {},
                    "statistics": (write or {}).get("statistics") if isinstance(write, dict) else {},
                },
                model_name=os.environ.get("WRITER_AGENT_MODEL", ""),
            )
            out["stop_reason"] = "generated_content_too_short"
            _append(out, "errors", "generated_content_too_short")
            return out
        _append(out, "warnings", "writer_short_result_fallback_used")
    out["generated_title"] = new_title
    out["generated_content_md"] = content
    out["generated_meta_description"] = str((art or {}).get("meta_description") or "")
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_rewrite",
        agent_name="WriterAgent",
        prompt_type="rendered_writer_prompt",
        prompt_text=write.get("prompt") if isinstance(write, dict) else "",
        input_payload={"topic": topic},
        output_payload={
            "generated_title": new_title,
            "generated_meta_description": out.get("generated_meta_description"),
            "content_chars": len(content),
            "generated_content_md": _audit_text(content),
            "warnings": (write or {}).get("warnings") if isinstance(write, dict) else [],
            "quality_checks": (write or {}).get("quality_checks") if isinstance(write, dict) else {},
            "statistics": (write or {}).get("statistics") if isinstance(write, dict) else {},
        },
        model_name=os.environ.get("WRITER_AGENT_MODEL", ""),
    )
    return out


async def rewrite_quality_node(state: ArticleGraphState) -> ArticleGraphState:
    """Second quality gate for generated content."""

    if state.get("stop_reason"):
        return state
    from agents.quality_agent import QualityAgent

    out: ArticleGraphState = dict(state)
    qr = await QualityAgent().score_article(
        {
            "title": out.get("generated_title", out.get("title", "")),
            "content": str(out.get("generated_content_md") or "")[:3000],
            "source_url": out.get("source_url", ""),
            "if_ai_generated": True,
        }
    )
    q2 = round(float(qr.get("quality_score", 0)), 1)
    out["rewrite_quality_after"] = q2
    out["quality_after"] = q2
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_rewrite",
        agent_name="QualityAgent",
        prompt_type="rewrite_quality_result",
        input_payload={
            "title": out.get("generated_title", out.get("title", "")),
            "generated_content_chars": len(str(out.get("generated_content_md") or "")),
            "generated_content_md": _audit_text(out.get("generated_content_md")),
        },
        output_payload={"quality_score": q2, "raw": qr},
        model_name=os.environ.get("QUALITY_AGENT_MODEL", ""),
    )
    return out


def route_after_rewrite_quality(state: ArticleGraphState) -> str:
    """Route rewritten drafts based on their second quality score."""

    if state.get("stop_reason"):
        return "stop"
    threshold = _env_float("REWRITE_QUALITY_THRESHOLD", DEFAULT_REWRITE_QUALITY_THRESHOLD)
    if float(state.get("rewrite_quality_after") or 0) < threshold:
        return "rewrite_blocked"
    return "edit"


async def rewrite_blocked_node(state: ArticleGraphState) -> ArticleGraphState:
    """Mark a rewrite branch as blocked after failing the rewrite quality gate."""

    out: ArticleGraphState = dict(state)
    out["stop_reason"] = "rewrite_quality_below_threshold"
    return out


async def editor_node(state: ArticleGraphState) -> ArticleGraphState:
    """Edit only rewritten drafts that passed the second quality gate."""

    if state.get("stop_reason"):
        return state
    from agents.editor_agent import EditorAgent

    out: ArticleGraphState = dict(state)
    edit = await EditorAgent().execute(
        article={"title": out.get("generated_title"), "content_md": out.get("generated_content_md")},
        dry_run=False,
    )
    if not isinstance(edit, dict):
        out["stop_reason"] = "editor_empty_result"
        _append(out, "errors", "editor_empty_result")
        return out
    edited_title = str(edit.get("title") or edit.get("edited_title") or out.get("generated_title") or "")
    edited_content = str(edit.get("content_md") or edit.get("content") or out.get("generated_content_md") or "")
    edited_content = _ensure_reference_section(edited_content, out.get("generated_content_md"))
    if not edited_title.strip() or len(edited_content) < 100:
        out["stop_reason"] = "editor_invalid_result"
        _append(out, "errors", "editor_invalid_result")
        return out
    out["title"] = edited_title
    out["content"] = edited_content
    out["content_md"] = edited_content
    out["edited_title"] = edited_title
    out["edited_content_md"] = edited_content
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_rewrite",
        agent_name="EditorAgent",
        prompt_type="editor_result",
        input_payload={
            "generated_title": out.get("generated_title"),
            "generated_content_chars": len(str(out.get("generated_content_md") or "")),
            "generated_content_md": _audit_text(out.get("generated_content_md")),
        },
        output_payload={
            "edited_title": edited_title,
            "edited_content_chars": len(edited_content),
            "edited_content_md": _audit_text(edited_content),
            "raw": edit,
        },
        model_name=os.environ.get("EDITOR_LLM_MODEL", ""),
    )
    return out


async def seo_node(state: ArticleGraphState) -> ArticleGraphState:
    """Generate SEO metadata for direct or rewritten publishable articles."""

    if state.get("stop_reason"):
        return state
    from agents.seo_agent import SEOAgent

    out: ArticleGraphState = dict(state)
    title = str(out.get("edited_title") or out.get("title") or "")
    content = str(out.get("edited_content_md") or out.get("content_md") or out.get("content") or out.get("description") or "")
    validate_publish_prerequisites(out, title=title, content=content)
    seo = await SEOAgent().execute(
        keyword_mode="v2",
        article={"title": title, "content_md": content, "meta_description": "", "slug": ""},
        topic=out,
        page_info={"slug": slugify(title), "category": "news"},
        dry_run=True,
    )
    keywords = seo.get("keyword_result", {}).get("keywords", []) if isinstance(seo, dict) else []
    out["seo_meta_title"] = seo.get("meta_title", "") if isinstance(seo, dict) else ""
    out["seo_meta_description"] = seo.get("meta_description", "") if isinstance(seo, dict) else ""
    out["seo_keywords"] = keywords
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_publish",
        agent_name="SEOAgent",
        prompt_type="seo_article_context",
        input_payload={
            "title": title,
            "content_chars": len(content),
            "content_md": _audit_text(content),
            "slug": slugify(title),
        },
        output_payload={
            "meta_title": out.get("seo_meta_title"),
            "meta_description": out.get("seo_meta_description"),
            "keywords": keywords,
        },
        model_name=os.environ.get("SEO_AGENT_MODEL", ""),
    )
    return out


async def image_node(state: ArticleGraphState) -> ArticleGraphState:
    """Ensure cover image exists. Rewritten articles generate; forwarded reuse."""

    if state.get("stop_reason"):
        return state
    out: ArticleGraphState = dict(state)
    title = str(out.get("title") or "")
    existing_cover = await fetch_existing_cover(out.get("article_id"))
    source_image = str(out.get("source_image") or out.get("image") or out.get("cover_image") or "").strip()
    # cover_decision is shared by LangGraph image/CMS logic. Keeping the rule
    # centralized prevents late-stage nodes from disagreeing about when to reuse
    # the source cover or an already-generated cover.
    cover = cover_decision(out, existing_cover=existing_cover, source_image=source_image, title=title)
    image_url = cover["image_url"]
    image_local_path = cover["image_local_path"]
    image_prompt = cover["image_prompt"]
    provider_name = ""
    provider_result: Dict[str, Any] = {}
    fallback_to_source = False
    if cover["should_generate"]:
        prompt_generation = await _generate_cover_prompt_with_llm(out, fallback_prompt=image_prompt)
        image_prompt = str(prompt_generation.get("prompt") or image_prompt)
        if prompt_generation.get("reason") not in {"ok", "disabled"}:
            _append(out, "warnings", f"image_prompt_llm_fallback:{prompt_generation.get('reason')}")
        await log_agent_prompt(
            article_id=out.get("article_id"),
            stage="langgraph_image_prompt",
            agent_name="ImagePromptAgent",
            prompt_type="cover_prompt_generation",
            prompt_text=prompt_generation.get("request_prompt"),
            input_payload={
                "title": title,
                "fallback_prompt": cover["image_prompt"],
                "context_chars": len(_image_prompt_context(out)),
                "model": prompt_generation.get("model"),
            },
            output_payload={
                "generated_prompt": image_prompt,
                "used_llm": prompt_generation.get("used_llm"),
                "reason": prompt_generation.get("reason"),
                "usage": prompt_generation.get("usage") or {},
            },
            model_name=str(prompt_generation.get("model") or os.environ.get("IMAGE_PROMPT_MODEL") or ""),
            status="ok" if prompt_generation.get("reason") in {"ok", "disabled"} else "warning",
            error_message=None if prompt_generation.get("reason") in {"ok", "disabled"} else str(prompt_generation.get("reason")),
        )
        # Provider creation happens only inside this branch, so forwarded
        # articles and reruns with existing generated covers never spend image
        # generation quota by accident.
        from agents.image_agent.tools.provider_factory import get_image_provider

        provider = get_image_provider()
        provider_name = provider.__class__.__name__
        try:
            img = await provider.generate(prompt=image_prompt, n=1)
            provider_result = img if isinstance(img, dict) else {"success": False, "error": str(img)}
        finally:
            # Providers may hold HTTP sessions. Close best-effort so a long
            # unattended process does not slowly leak connections.
            close = getattr(provider, "close", None)
            if close:
                await close()
        image_item = (provider_result.get("images") or [{}])[0] if provider_result.get("success") and provider_result.get("images") else {}
        image_url = image_item.get("url", "")
        image_local_path = image_item.get("local_path", "")
        if not image_url and not image_local_path:
            error = provider_result.get("error") or "no_images_generated"
            should_fallback = _env_bool("IMAGE_FALLBACK_TO_SOURCE_ON_GENERATION_FAILURE", True) or _image_error_should_fallback_to_source(error)
            if source_image and should_fallback:
                # Rewritten articles prefer a generated cover, but production
                # should not stall when the image provider is unavailable and
                # the crawler already has a usable source cover.
                image_url = source_image
                image_prompt = f"{image_prompt}\n\n[生成失败，已回退复用原文封面：{error}]"
                fallback_to_source = True
                _append(out, "warnings", f"image_generation_failed_fallback_to_source:{error}")
            else:
                out["stop_reason"] = f"image_generation_failed:{error}"
                _append(out, "errors", out["stop_reason"])
                await log_agent_prompt(
                    article_id=out.get("article_id"),
                    stage="langgraph_image",
                    agent_name="ImageAgent",
                    prompt_type="cover_image_prompt",
                    prompt_text=image_prompt,
                    input_payload={
                        "title": title,
                        "is_forwarded": cover["is_forwarded"],
                        "cover_reason": cover["reason"],
                        "reuse_existing_cover": False,
                        "source_image": source_image,
                        "existing_cover": existing_cover,
                        "provider": provider_name,
                        "should_generate": cover["should_generate"],
                    },
                    output_payload={
                        "error": error,
                        "provider_result": _image_provider_result_for_audit(provider_result),
                        "fallback_to_source": False,
                        "final_image_url": "",
                        "final_image_local_path": "",
                    },
                    model_name=os.environ.get("COZE_IMAGE_MODEL", ""),
                    status="error",
                    error_message=str(error),
                )
                return out
    featured_image = image_local_path or image_url
    # validate_cover_ready is intentionally after provider/reuse decision and
    # before CMS. It is the hard stop that prevents publishing rewritten content
    # without a generated cover.
    validate_cover_ready(out, cover, featured_image=featured_image)
    out["image_prompt"] = image_prompt
    out["image_url"] = image_url
    out["image_local_path"] = image_local_path
    out["featured_image"] = featured_image
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_image",
        agent_name="ImageAgent",
        prompt_type="cover_image_prompt",
        prompt_text=image_prompt,
        input_payload={
            "title": title,
            "is_forwarded": cover["is_forwarded"],
            "cover_reason": cover["reason"],
            "reuse_existing_cover": bool(image_url or image_local_path),
            "source_image": source_image,
            "existing_cover": existing_cover,
            "provider": provider_name,
            "should_generate": cover["should_generate"],
        },
        output_payload={
            "provider_result": _image_provider_result_for_audit(provider_result),
            "fallback_to_source": fallback_to_source,
            "final_image_url": image_url,
            "final_image_local_path": image_local_path,
            "featured_image": featured_image,
        },
        model_name=os.environ.get("COZE_IMAGE_MODEL", ""),
        status="warning" if fallback_to_source else "ok",
        error_message=(provider_result.get("error") if fallback_to_source else None),
    )
    return out


async def cms_node(state: ArticleGraphState) -> ArticleGraphState:
    """Final CMS payload generation/publish. Default remains dry-run."""

    if state.get("stop_reason"):
        return state
    from agents.cms_agent import CMSAgent

    out: ArticleGraphState = dict(state)
    # Prefer edited/rewrite output when present; otherwise high-quality original
    # articles can flow through with their original title/content.
    title = str(out.get("edited_title") or out.get("title") or "")
    content = str(out.get("edited_content_md") or out.get("content_md") or out.get("content") or "")
    if is_forwarded_article(out):
        title = _ensure_reprint_title(title)
        content = normalize_forwarded_content_md(out.get("raw_content") or content)
        content = _ensure_reprint_credit(
            content,
            source_title=out.get("title") or out.get("source_title") or "",
            source_url=out.get("source_url") or out.get("original_url") or "",
        )
    content_html = _content_html_from_markdown(content)
    page_info = {
        "slug": slugify(title),
        "category": "news",
        "meta_title": out.get("seo_meta_title", ""),
        "meta_description": out.get("seo_meta_description", ""),
        "keywords": out.get("seo_keywords") or [],
    }
    featured_image = out.get("featured_image") or out.get("image_local_path") or out.get("image_url")
    images = {
        # CMSAgent expects featured_image_url/cover_url/cover_image_url. Keep the
        # older featured_image key too for history/debug output.
        "featured_image_url": featured_image,
        "cover_url": featured_image,
        "featured_image": featured_image,
        "image_url": out.get("image_url", ""),
        "image_local_path": out.get("image_local_path", ""),
    }
    article = {
        "title": title,
        "content_html": content_html,
        "content_md": content,
        "meta_description": out.get("seo_meta_description", ""),
        "source": {"article_id": out.get("article_id"), "url": out.get("source_url", "")},
    }
    if out.get("defer_cms_publish"):
        # Schedule preparation should create a publish-ready audit snapshot, not
        # run CMS hard validation. The pending dispatcher will call CMSAgent at
        # the actual publish slot.
        result = {
            "status": "dry_run",
            "article_id": None,
            "article_url": None,
            "payload": {
                "article": article,
                "page_info": page_info,
                "images": images,
            },
        }
    else:
        # publish_dry_run defaults to True all the way from the runner. A real
        # CMS publish therefore needs both --publish and CMS_ENABLE_REAL_PUBLISH
        # in the CMS client/config layer.
        result = await CMSAgent(dry_run=bool(out.get("publish_dry_run", True))).execute(
            article=article,
            page_info=page_info,
            images=images,
        )
    out["cms_result"] = result if isinstance(result, dict) else {}
    out["cms_status"] = str(out["cms_result"].get("status") or "")
    out["cms_article_id"] = str(out["cms_result"].get("article_id") or "")
    out["cms_article_url"] = str(out["cms_result"].get("article_url") or "")
    await log_agent_prompt(
        article_id=out.get("article_id"),
        stage="langgraph_cms",
        agent_name="CMSAgent",
        prompt_type="cms_payload_result",
        input_payload={
            "article": {**article, "content_md": _audit_text(article.get("content_md"))},
            "page_info": page_info,
            "images": images,
            "dry_run": bool(out.get("publish_dry_run", True)),
        },
        output_payload={"cms_result": out["cms_result"]},
        model_name="",
    )
    return out


def _audit_status_for_state(state: ArticleGraphState) -> str:
    """Convert a terminal graph state into one compact pipeline_audit status."""

    stop_reason = str(state.get("stop_reason") or "")
    # Order matters: blocked states should win over partial downstream fields.
    # Example: an article may have ai_score but still be source_blocked if the
    # full body was missing in this run.
    if stop_reason in {"source_content_missing", "source_content_too_short", "source_article_not_found"}:
        return "source_blocked"
    if stop_reason == "ai_score_below_threshold":
        return "ai_score_blocked"
    if stop_reason in {
        "rewrite_quality_below_threshold",
        "generated_content_too_short",
        "editor_empty_result",
        "editor_invalid_result",
    }:
        return "rewrite_blocked"
    if stop_reason.startswith("image_generation_failed"):
        return "image_blocked"
    if stop_reason:
        return "blocked"
    if state.get("cms_status"):
        # CMSAgent returns statuses such as dry_run / published / failed. Keep
        # the exact value so the audit table matches the final publish outcome.
        if state.get("defer_cms_publish") and str(state.get("cms_status") or "") == "dry_run":
            return "pending"
        return str(state.get("cms_status") or "")
    if state.get("image_url") or state.get("image_local_path"):
        return "image_ready"
    if state.get("seo_meta_title") or state.get("seo_meta_description"):
        return "seo_ready"
    if state.get("edited_title") or state.get("edited_content_md"):
        return "editor_ready"
    if state.get("rewrite_quality_after") is not None:
        return "rewrite_ready"
    if state.get("quality_score") is not None:
        return "quality_passed"
    if state.get("ai_score") is not None:
        return "scored"
    return ""


async def save_audit_node(state: ArticleGraphState) -> ArticleGraphState:
    """Persist the final graph result into pipeline_audit when explicitly enabled.

    The standalone LangGraph runner is safe by default. It only writes MySQL when
    the caller passes persist_audit=True, which the CLI exposes as
    --persist-audit. When enabled, this node writes the current graph result as
    the source of truth for that article, so stale downstream fields from older
    experiments are cleared if the current run stopped early.
    """

    out: ArticleGraphState = dict(state)
    if not out.get("persist_audit"):
        return out

    article_id = out.get("article_id") or out.get("id")
    if not article_id:
        _append(out, "warnings", "audit_not_persisted_missing_article_id")
        return out

    import aiomysql

    stop_reason = str(out.get("stop_reason") or "")
    status = _audit_status_for_state(out)
    if status in {"source_blocked", "ai_score_blocked"} or stop_reason == "ai_score_missing":
        # Pre-scoring rejects are intentionally not persisted to pipeline_audit.
        # source_blocked rows never entered ScoringAgent, and low-score rows
        # never entered Quality/Rewrite/Image/CMS. Keeping them out of audit
        # makes pipeline_audit mean "actually entered the article pipeline".
        # ai_score_missing means the scoring provider/parser did not produce a
        # usable score for this article. That is retryable and should not leave
        # an empty blocked row in audit.
        #
        # In production, run_langgraph_batch.py can still advance the feed
        # cursor and mark low-score rows used, so they do not keep reappearing.
        out["cms_status"] = status
        out["audit_persisted"] = False
        _append(out, "warnings", f"audit_skipped_{stop_reason or status}")
        return out

    image_url = str(out.get("image_url") or out.get("source_image") or out.get("image") or "")
    image_local_path = str(out.get("image_local_path") or "")
    if status in {"source_blocked", "ai_score_blocked", "rewrite_blocked", "image_blocked", "blocked"}:
        # If the current run ended before a valid cover decision, do not leave a
        # stale image from an older successful run attached to a blocked audit
        # row. image_blocked should not look like it has a publishable cover.
        image_url = ""
        image_local_path = ""

    generated_title = out.get("generated_title")
    generated_content = out.get("generated_content_md")
    edited_title = out.get("edited_title")
    edited_content = out.get("edited_content_md")
    seo_meta_title = out.get("seo_meta_title")
    seo_meta_description = out.get("seo_meta_description")
    seo_keywords = out.get("seo_keywords")
    cms_article_id = out.get("cms_article_id") or (out.get("cms_result") or {}).get("article_id")
    cms_article_url = out.get("cms_article_url") or (out.get("cms_result") or {}).get("article_url")

    if status == "pending" and is_forwarded_article(out):
        # Direct/forwarded articles do not pass through Writer/Editor, but the
        # pending queue releases from pipeline_audit later. Store a publish-ready
        # snapshot so release can call CMS without rerunning the graph.
        generated_title = _ensure_reprint_title(str(generated_title or out.get("title") or ""))
        forwarded_content = normalize_forwarded_content_md(
            out.get("raw_content") or out.get("content") or out.get("source_content") or out.get("description") or ""
        )
        generated_content = generated_content or _ensure_reprint_credit(
            forwarded_content,
            source_title=out.get("title") or "",
            source_url=out.get("source_url") or out.get("original_url") or "",
        )

    if status in {"source_blocked", "ai_score_blocked"}:
        # These states happen before rewriting and late stages, so clear every
        # downstream field. Otherwise an old successful run could make a blocked
        # article look partly publish-ready in pipeline_audit.
        generated_title = None
        generated_content = None
        edited_title = None
        edited_content = None
        seo_meta_title = None
        seo_meta_description = None
        seo_keywords = None
        cms_article_id = None
        cms_article_url = None
    elif status == "rewrite_blocked":
        # A rewrite-blocked article may still have generated draft text for
        # debugging, but it must not keep edited/SEO/CMS fields from any earlier
        # run because those imply publish readiness.
        edited_title = None
        edited_content = None
        seo_meta_title = None
        seo_meta_description = None
        seo_keywords = None
        cms_article_id = None
        cms_article_url = None

    tables = crawler_table_config()
    pool = await aiomysql.create_pool(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"),
        charset="utf8mb4",
        minsize=1,
        maxsize=1,
    )
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Keep INSERT and UPDATE values identical: the current graph
                # run is the source of truth for this article's audit row.
                audit_values = (
                    article_id,
                    out.get("ai_score"),
                    out.get("quality_score"),
                    out.get("rewrite_quality_after"),
                    generated_title,
                    generated_content,
                    edited_title,
                    edited_content,
                    image_url,
                    image_local_path,
                    seo_meta_title,
                    seo_meta_description,
                    json.dumps(seo_keywords or [], ensure_ascii=False) if seo_keywords is not None else None,
                    status,
                    str(cms_article_id or "") or None,
                    str(cms_article_url or "") or None,
                )
                await cur.execute(
                    f"""
                    INSERT INTO {tables.audit_sql} (
                        article_id, ai_score, quality_score, rewrite_quality_after,
                        generated_title, generated_content_md,
                        edited_title, edited_content_md,
                        image_url, image_local_path,
                        seo_meta_title, seo_meta_description, seo_keywords,
                        cms_status, cms_article_id, cms_article_url
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        ai_score=%s,
                        quality_score=%s,
                        rewrite_quality_after=%s,
                        generated_title=%s,
                        generated_content_md=%s,
                        edited_title=%s,
                        edited_content_md=%s,
                        image_url=%s,
                        image_local_path=%s,
                        seo_meta_title=%s,
                        seo_meta_description=%s,
                        seo_keywords=%s,
                        cms_status=%s,
                        cms_article_id=%s,
                        cms_article_url=%s
                    """,
                    audit_values + audit_values[1:],
                )
            await conn.commit()
    finally:
        pool.close()
        await pool.wait_closed()

    out["cms_status"] = status
    out["audit_persisted"] = True
    return out


def _require_langgraph():
    """Import LangGraph lazily and raise a helpful install error if missing."""

    try:
        from langgraph.graph import END, StateGraph
    except Exception as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "langgraph_missing: install dependencies with `pip install -r requirements.txt` "
            "inside the project virtualenv before running the standalone graph"
        ) from exc
    return StateGraph, END


def build_article_graph():
    """Compile the standalone article graph."""

    StateGraph, END = _require_langgraph()
    graph = StateGraph(ArticleGraphState)
    # Nodes are deliberately named after business stages, not implementation
    # classes. This makes the graph readable even when a node calls multiple
    # Agents internally, such as rewrite_quality/editor.
    graph.add_node("load_source", load_source_node)
    graph.add_node("scoring", scoring_node)
    graph.add_node("stop_low_score", stop_low_score_node)
    graph.add_node("quality", quality_node)
    graph.add_node("research", research_node)
    graph.add_node("writer", writer_node)
    graph.add_node("rewrite_quality", rewrite_quality_node)
    graph.add_node("rewrite_blocked", rewrite_blocked_node)
    graph.add_node("editor", editor_node)
    graph.add_node("seo", seo_node)
    graph.add_node("image", image_node)
    graph.add_node("cms", cms_node)
    graph.add_node("save_audit", save_audit_node)

    def finish_route(s: ArticleGraphState) -> str:
        """Send terminal graph states either to audit persistence or END."""

        # Most terminal branches either persist audit or end immediately. This
        # one helper keeps that decision consistent across low-score, blocked,
        # CMS, and explicit save_audit paths.
        return "save" if s.get("persist_audit") else "end"

    graph.set_entry_point("load_source")
    # Load source first for every run, even when the caller provided partial
    # state. That guarantees graph nodes use the same MySQL/shard body fallback
    # as the production batch runner.
    graph.add_edge("load_source", "scoring")
    # From here down, every conditional edge is a business decision:
    # score gate, quality gate, rewrite gate, and late-stage publish gates.
    graph.add_conditional_edges("scoring", route_after_scoring, {"quality": "quality", "stop_low_score": "stop_low_score", "stop": "save_audit"})
    graph.add_conditional_edges("quality", route_after_quality, {"rewrite": "research", "seo": "seo", "done": "save_audit", "stop": "save_audit"})
    graph.add_edge("research", "writer")
    graph.add_edge("writer", "rewrite_quality")
    graph.add_conditional_edges("rewrite_quality", route_after_rewrite_quality, {"edit": "editor", "rewrite_blocked": "rewrite_blocked", "stop": "save_audit"})
    graph.add_conditional_edges("editor", lambda s: "seo" if s.get("run_late_stages", True) and not s.get("stop_reason") else "done", {"seo": "seo", "done": "save_audit"})
    graph.add_conditional_edges("seo", lambda s: "image" if s.get("run_late_stages", True) and not s.get("stop_reason") else "done", {"image": "image", "done": "save_audit"})
    graph.add_conditional_edges("image", lambda s: "cms" if s.get("run_late_stages", True) and not s.get("stop_reason") else "done", {"cms": "cms", "done": "save_audit"})
    graph.add_conditional_edges("cms", finish_route, {"save": "save_audit", "end": END})
    graph.add_conditional_edges("save_audit", finish_route, {"save": END, "end": END})
    graph.add_conditional_edges("stop_low_score", finish_route, {"save": "save_audit", "end": END})
    graph.add_conditional_edges("rewrite_blocked", finish_route, {"save": "save_audit", "end": END})
    return graph.compile()


async def run_article_graph(initial_state: Dict[str, Any]) -> ArticleGraphState:
    """Run one article through the standalone LangGraph pipeline."""

    state: ArticleGraphState = {
        "publish_dry_run": True,
        "persist_audit": False,
        "run_late_stages": True,
        **dict(initial_state or {}),
    }
    app = build_article_graph()
    return await app.ainvoke(state)


def summarize_graph_result(state: ArticleGraphState) -> Dict[str, Any]:
    """Compact output for CLI/debugging."""

    return {
        "article_id": state.get("article_id"),
        "title": state.get("title"),
        "ai_score": state.get("ai_score"),
        "scoring_mode": state.get("scoring_mode"),
        "quality_score": state.get("quality_score"),
        "rewrite_quality_after": state.get("rewrite_quality_after"),
        "generated_title": state.get("generated_title"),
        "edited_title": state.get("edited_title"),
        "seo_meta_title": state.get("seo_meta_title"),
        "image_url": state.get("image_url"),
        "image_local_path": state.get("image_local_path"),
        "cms_status": state.get("cms_status"),
        "cms_article_id": state.get("cms_article_id"),
        "cms_article_url": state.get("cms_article_url"),
        "audit_persisted": state.get("audit_persisted", False),
        "stop_reason": state.get("stop_reason"),
        "errors": state.get("errors") or [],
        "warnings": state.get("warnings") or [],
    }
