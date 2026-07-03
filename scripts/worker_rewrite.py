#!/usr/bin/env python3
import asyncio, json, os, re, sys, logging, time
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker.rewrite")
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_REWRITE,
    STREAM_PUBLISH, GROUP_REWRITE, ack_message, recover_pending,
    handle_failure, read_group_messages)
from scripts.prompt_db_logger import log_agent_prompt
from scripts.pipeline_text import article_source_content, clean_article_text
import redis.asyncio as redis

CONSUMER = f"rewrite-{os.getpid()}"
REWRITE_QUALITY_THRESHOLD = float(os.environ.get("REWRITE_QUALITY_THRESHOLD", "70"))


def _clean_source_text(text: str, limit: int = 3000) -> str:
    return clean_article_text(text, limit=limit)


def _build_fallback_writer_prompt(*, title: str, source_content: str, quality_score) -> str:
    """Last-resort prompt used when ResearchAgent cannot produce a writer prompt."""
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

## 输出格式
{{
  "article": {{
    "title": "...",
    "meta_description": "...",
    "content_md": "..."
  }}
}}
"""


async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_REWRITE, GROUP_REWRITE, CONSUMER)
    logger.info("started")

    while True:
        try:
            msgs = await read_group_messages(
                r,
                group=GROUP_REWRITE,
                consumer=CONSUMER,
                stream=STREAM_REWRITE,
                count=1,
                block=5000,
            )
        except redis.ResponseError:
            await asyncio.sleep(5); continue
        if not msgs: continue

        for stream, entries in msgs:
            for msg_id, fields in entries:
                try: item = json.loads(fields.get("data", "{}"))
                except Exception as exc:
                    await handle_failure(
                        r,
                        stream=STREAM_REWRITE,
                        group=GROUP_REWRITE,
                        msg_id=msg_id,
                        item={"raw_data": fields.get("data", "")},
                        stage="rewrite_parse",
                        error=str(exc),
                        max_retries=0,
                    )
                    continue
                title = item.get("title", "")
                debug_outputs = {
                    "consumer": CONSUMER,
                    "redis_msg_id": msg_id,
                    "source_stream": stream,
                    "title": title,
                }
                try:
                    from agents.research_agent import ResearchAgent
                    from agents.writer_agent import WriterAgent
                    from agents.quality_agent import QualityAgent
                    # Rewrite always rebuilds source_content from the Redis
                    # payload. Prompt audit logs are for debugging only and are
                    # not required for generation.
                    source_content = article_source_content(item, limit=3000)
                    item["source_content"] = source_content
                    item["content"] = source_content
                    topic = {
                        "title": title,
                        "primary_keyword": title[:20],
                        "source_content": source_content,
                        "source_title": title,
                        "source_summary": item.get("description", "")[:500],
                        "source_url": item.get("source_url", ""),
                        "content_type": "news",
                        "search_intent": "informational",
                        "article_overall_score": item.get("ai_score"),
                        "quality_score": item.get("quality_score"),
                    }
                    debug_outputs["topic"] = topic
                    research_mode = os.environ.get("RESEARCH_AGENT_MODE", "live")
                    stage_start = time.perf_counter()
                    res = await ResearchAgent().execute_direct(topic=topic, mode=research_mode)
                    research_elapsed = time.perf_counter() - stage_start
                    debug_outputs["research_output"] = {
                        "keys": list(res.keys()) if isinstance(res, dict) else [],
                        "has_writer_prompt": bool(
                            isinstance(res, dict)
                            and isinstance(res.get("writer_prompt"), dict)
                            and str(res.get("writer_prompt", {}).get("prompt_text") or "").strip()
                        ),
                    }
                    research_prompt = ""
                    if isinstance(res, dict):
                        wp = res.get("writer_prompt") or {}
                        if isinstance(wp, dict):
                            research_prompt = str(wp.get("prompt_text") or "")
                    writer_prompt_for_generation = research_prompt.strip()
                    if not writer_prompt_for_generation:
                        # ResearchAgent should normally return writer_prompt.
                        # If it does not, keep the pipeline usable with a strict
                        # source-based fallback so WriterAgent still receives
                        # the original article body.
                        writer_prompt_for_generation = _build_fallback_writer_prompt(
                            title=title,
                            source_content=source_content,
                            quality_score=item.get("quality_score"),
                        )
                        logger.warning(
                            "id=%s research writer_prompt missing, using source-based fallback prompt",
                            item.get("article_id"),
                        )
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="rewrite",
                        agent_name="ResearchAgent",
                        prompt_type="writer_prompt_from_research_brief",
                        prompt_text=research_prompt or (json.dumps(res, ensure_ascii=False)[:20000] if res else ""),
                        input_payload={"topic": topic, "mode": research_mode},
                        output_payload={"keys": list(res.keys()) if isinstance(res, dict) else [], "has_writer_prompt": bool(research_prompt)},
                        model_name=os.environ.get("RESEARCH_AGENT_MODEL", ""),
                    )
                    outline = (res or {}).get("outline")
                    materials = res if isinstance(res, dict) else {}
                    if "research_brief" not in materials:
                        materials = dict(materials or {})
                        materials["research_brief"] = {
                            "source_snapshot": {
                                "source_title": title,
                                "source_summary": item.get("description", "")[:500],
                                "source_content": source_content[:3000],
                            },
                            "source_highlights": [item.get("description", "")[:200]],
                            "key_facts": [{"fact": item.get("description", "")[:300]}],
                            "rewrite_constraints": ["保持原文事实准确", "不要新增原文没有的事实"],
                            "risk_points": [],
                            "suggested_sections": [],
                            "writer_outline": outline if isinstance(outline, dict) else {"sections": []},
                        }
                    writer = WriterAgent()
                    writer._load_prompt = lambda: writer_prompt_for_generation
                    stage_start = time.perf_counter()
                    write = await writer.execute(topic=topic, outline=outline, materials=materials, dry_run=True)
                    writer_elapsed = time.perf_counter() - stage_start
                    debug_outputs["writer_output"] = {
                        "keys": list(write.keys()) if isinstance(write, dict) else [],
                        "warnings": write.get("warnings") if isinstance(write, dict) else None,
                        "generated_title": ((write.get("article") or {}).get("title") if isinstance(write, dict) else None),
                    }
                    writer_prompt = write.get("prompt") if isinstance(write, dict) else ""
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="rewrite",
                        agent_name="WriterAgent",
                        prompt_type="rendered_writer_prompt",
                        prompt_text=writer_prompt,
                        input_payload={"topic": topic, "outline": outline},
                        output_payload={
                            "generated_title": ((write.get("article") or {}).get("title") if isinstance(write, dict) else None),
                            "warnings": write.get("warnings") if isinstance(write, dict) else None,
                        },
                        model_name=os.environ.get("WRITER_AGENT_MODEL", ""),
                    )
                    if isinstance(write, dict):
                        art = write.get("article") or {}
                        content = art.get("content_md") or art.get("content") or ""
                        new_title = art.get("title") or title
                    else:
                        content = ""; new_title = title
                    if len(content) < 100:
                        logger.info("id=%s content too short", item['article_id'])
                        await handle_failure(
                            r,
                            stream=STREAM_REWRITE,
                            group=GROUP_REWRITE,
                            msg_id=msg_id,
                            item=item,
                            stage="rewrite_short_content",
                            error="generated_content_too_short",
                            max_retries=0,
                        )
                        continue

                    stage_start = time.perf_counter()
                    qr2 = await QualityAgent().score_article({
                        "title": new_title, "content": content[:3000], "source_url": "", "if_ai_generated": True,
                    })
                    rewrite_quality_elapsed = time.perf_counter() - stage_start
                    q2 = round(float(qr2.get("quality_score", 0)), 1)
                    debug_outputs["quality_output"] = {
                        "quality_score": q2,
                        "grade": qr2.get("grade"),
                        "route": qr2.get("route"),
                        "reasons": qr2.get("reasons") or qr2.get("dimension_reasons"),
                    }
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="rewrite",
                        agent_name="QualityAgent",
                        prompt_type="rewrite_quality_result",
                        prompt_text=None,
                        input_payload={
                            "title": new_title,
                            "content_chars": len(content or ""),
                            "if_ai_generated": True,
                        },
                        output_payload={
                            "quality_score": q2,
                            "quality_reasons": qr2.get("reasons") or qr2.get("dimension_reasons"),
                            "quality_suggestions": qr2.get("suggestions"),
                            "raw_result": qr2,
                        },
                        model_name=os.environ.get("QUALITY_AGENT_MODEL", ""),
                    )
                    try:
                        import aiomysql
                        pool = await aiomysql.create_pool(
                            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                            db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=1)
                        async with pool.acquire() as c:
                            async with c.cursor() as cur:
                                await cur.execute(
                                    "UPDATE pipeline_audit SET rewrite_quality_after=%s, "
                                    "generated_title=%s, generated_content_md=%s, "
                                    "edited_title=NULL, edited_content_md=NULL "
                                    "WHERE article_id=%s",
                                    (q2, new_title, content, item.get("article_id")))
                            await c.commit()
                        pool.close(); await pool.wait_closed()
                    except Exception:
                        logger.exception("rewrite attempt audit write error")
                    if q2 >= REWRITE_QUALITY_THRESHOLD:
                        # Only rewrites that pass the second quality gate spend
                        # editor/SEO/image/CMS work. Failed rewrites keep their
                        # generated_title/content for review but stop here.
                        from agents.editor_agent import EditorAgent

                        stage_start = time.perf_counter()
                        edit = await EditorAgent().execute(
                            article={"title": new_title, "content_md": content},
                            dry_run=False,
                        )
                        editor_elapsed = time.perf_counter() - stage_start
                        if not isinstance(edit, dict):
                            raise RuntimeError("editor_empty_result")
                        edited_title = edit.get("title") or edit.get("edited_title") or new_title
                        edited_content = edit.get("content_md") or edit.get("content") or content
                        if not str(edited_title or "").strip() or len(str(edited_content or "")) < 100:
                            raise RuntimeError("editor_invalid_result")
                        debug_outputs["editor_output"] = {
                            "keys": list(edit.keys()),
                            "edited_title": edited_title,
                            "edited_content_chars": len(edited_content or ""),
                        }
                        await log_agent_prompt(
                            article_id=item.get("article_id"),
                            stage="rewrite",
                            agent_name="EditorAgent",
                            prompt_type="editor_result",
                            prompt_text=None,
                            input_payload={"title": new_title, "content_chars": len(content or "")},
                            output_payload=debug_outputs["editor_output"],
                            model_name=os.environ.get("EDITOR_LLM_MODEL", ""),
                        )

                        item["title"] = edited_title
                        item["generated_title"] = new_title
                        item["content_md"] = edited_content
                        item["generated_content_md"] = content
                        item["edited_title"] = edited_title
                        item["edited_content_md"] = edited_content
                        item["quality_after"] = q2
                        import aiomysql
                        try:
                            pool = await aiomysql.create_pool(
                                host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                                user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                                db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=3)
                            async with pool.acquire() as c:
                                async with c.cursor() as cur:
                                    await cur.execute(
                                        "UPDATE pipeline_audit SET rewrite_quality_after=%s, "
                                        "generated_title=%s, generated_content_md=%s, "
                                        "edited_title=%s, edited_content_md=%s "
                                        "WHERE article_id=%s",
                                        (
                                            q2,
                                            new_title,
                                            content,
                                            edited_title,
                                            edited_content,
                                            item.get("article_id"),
                                        ))
                                await c.commit()
                            pool2 = await aiomysql.create_pool(
                                host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                                user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                                db=os.environ.get("RESEARCH_MYSQL_DATABASE", "research_article_data"), charset="utf8mb4", minsize=1, maxsize=1)
                            async with pool2.acquire() as c2:
                                async with c2.cursor() as cur2:
                                    await cur2.execute(
                                        "INSERT INTO writer_article_outputs "
                                        "(candidate_id, source_article_id, original_url, source_title, article_score, "
                                        "writer_prompt, writer_model, generated_title, generated_meta_description, "
                                        "generated_content_md, generated_article_json, quality_checks, generation_status, generated_at) "
                                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'generated',NOW()) "
                                        "ON DUPLICATE KEY UPDATE generated_title=VALUES(generated_title), "
                                        "generated_content_md=VALUES(generated_content_md), "
                                        "generated_article_json=VALUES(generated_article_json), "
                                        "quality_checks=VALUES(quality_checks), generation_status='generated', "
                                        "generated_at=VALUES(generated_at)",
                                        (
                                            item.get("article_id"),
                                            item.get("article_id"),
                                            item.get("source_url", ""),
                                            title,
                                            item.get("ai_score", 0),
                                            "",
                                            os.environ.get("WRITER_AGENT_MODEL", ""),
                                            new_title,
                                            "",
                                            content,
                                            json.dumps(write, ensure_ascii=False) if isinstance(write, dict) else None,
                                            json.dumps({"quality_score": q2}, ensure_ascii=False),
                                        ))
                                await c2.commit()
                            pool2.close(); await pool2.wait_closed()
                            pool.close(); await pool.wait_closed()
                        except Exception:
                            logger.exception("audit write error")
                        await r.xadd(STREAM_PUBLISH, {"data": json.dumps(item, ensure_ascii=False)})
                        logger.info(
                            "id=%s Q=%.1f ✓ timings research=%.1fs writer=%.1fs quality=%.1fs editor=%.1fs",
                            item["article_id"],
                            q2,
                            research_elapsed,
                            writer_elapsed,
                            rewrite_quality_elapsed,
                            editor_elapsed,
                        )
                    else:
                        try:
                            import aiomysql
                            pool = await aiomysql.create_pool(
                                host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT","3306")),
                                user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                                db=os.environ.get("MYSQL_DATABASE", "multi_agent_cms"), charset="utf8mb4", minsize=1, maxsize=1)
                            async with pool.acquire() as c:
                                async with c.cursor() as cur:
                                    await cur.execute(
                                        "UPDATE pipeline_audit SET "
                                        "edited_title=NULL, edited_content_md=NULL, "
                                        "seo_meta_title=NULL, seo_meta_description=NULL, seo_keywords=NULL, "
                                        "cms_status='rewrite_blocked', cms_article_id=NULL, cms_article_url=NULL, "
                                        "image_local_path=NULL "
                                        "WHERE article_id=%s",
                                        (item.get("article_id"),),
                                    )
                                await c.commit()
                            pool.close(); await pool.wait_closed()
                        except Exception:
                            logger.exception("rewrite blocked audit cleanup error")
                        logger.info(
                            "id=%s Q=%.1f ✗ timings research=%.1fs writer=%.1fs quality=%.1fs",
                            item["article_id"],
                            q2,
                            research_elapsed,
                            writer_elapsed,
                            rewrite_quality_elapsed,
                        )
                        await handle_failure(
                            r,
                            stream=STREAM_REWRITE,
                            group=GROUP_REWRITE,
                            msg_id=msg_id,
                            item={**item, "quality_after": q2},
                            stage="rewrite_quality_gate",
                            error=f"quality_after_below_threshold:{q2}",
                            max_retries=0,
                        )
                        continue
                except Exception as exc:
                    logger.exception("rewrite error")
                    await log_agent_prompt(
                        article_id=item.get("article_id"),
                        stage="rewrite",
                        agent_name="RewriteWorker",
                        prompt_type="rewrite_exception_io",
                        prompt_text=None,
                        input_payload={"item": item},
                        output_payload=debug_outputs,
                        model_name=os.environ.get("WRITER_AGENT_MODEL", ""),
                        status="error",
                        error_message=str(exc),
                    )
                    await handle_failure(
                        r,
                        stream=STREAM_REWRITE,
                        group=GROUP_REWRITE,
                        msg_id=msg_id,
                        item=item,
                        stage="rewrite",
                        error=str(exc),
                    )
                    continue
                await ack_message(r, STREAM_REWRITE, GROUP_REWRITE, msg_id)

if __name__ == "__main__":
    asyncio.run(main())
