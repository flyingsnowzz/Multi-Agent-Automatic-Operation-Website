#!/usr/bin/env python3
"""Rewrite Worker — Research + Write → Quality 复评 → ≥75 推发布"""

import asyncio, json, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from scripts.redis_pipeline import (get_redis, setup_streams, STREAM_REWRITE,
    STREAM_PUBLISH, GROUP_REWRITE, ack_message, recover_pending)
import redis.asyncio as redis

CONSUMER = f"rewrite-{os.getpid()}"


def strip_html(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


async def fill_article_content(item):
    if item.get("content") or item.get("description") or not item.get("article_id"):
        return item
    try:
        import aiomysql
        pool = await aiomysql.create_pool(
            host=os.environ["MYSQL_HOST"], port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            db=os.environ["MYSQL_DATABASE"], charset="utf8mb4", minsize=1, maxsize=1)
        async with pool.acquire() as c:
            async with c.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT title, description, original_url FROM crawler_news_main WHERE id=%s LIMIT 1",
                    (item.get("article_id"),))
                row = await cur.fetchone()
                full_content = ""
                if row:
                    for idx in range(5):
                        await cur.execute(
                            f"SELECT content FROM crawler_news_{idx} WHERE news_id=%s LIMIT 1",
                            (item.get("article_id"),),
                        )
                        content_row = await cur.fetchone()
                        if content_row and content_row.get("content"):
                            full_content = content_row.get("content") or ""
                            break
        pool.close(); await pool.wait_closed()
        if row:
            item["title"] = item.get("title") or row.get("title", "")
            item["description"] = row.get("description", "") or ""
            item["content"] = (item["description"] + "\n" + full_content).strip()
            item["source_url"] = item.get("source_url") or row.get("original_url", "")
    except Exception as e:
        print(f"[{CONSUMER}] 正文回填失败: {e}")
    return item


async def main():
    r = await get_redis()
    await setup_streams(r)
    await recover_pending(r, STREAM_REWRITE, GROUP_REWRITE, CONSUMER)
    print(f"[{CONSUMER}] 启动")

    while True:
        try:
            msgs = await r.xreadgroup(GROUP_REWRITE, CONSUMER,
                                       {STREAM_REWRITE: ">"}, count=1, block=5000)
        except redis.ResponseError:
            await asyncio.sleep(5); continue

        if not msgs:
            continue

        for stream, entries in msgs:
            for msg_id, fields in entries:
                try:
                    item = json.loads(fields.get("data", "{}"))
                except Exception:
                    await ack_message(r, STREAM_REWRITE, GROUP_REWRITE, msg_id)
                    continue

                item = await fill_article_content(item)
                title = item.get("title", "")
                try:
                    from agents.research_agent import ResearchAgent
                    from agents.writer_agent import WriterAgent
                    from agents.quality_agent import QualityAgent

                    source_content = strip_html(item.get("full_content") or item.get("content") or item.get("description", ""))
                    if len(source_content) < 200:
                        print(f"[{CONSUMER}] id={item['article_id']} 原文过短，跳过改写")
                        await ack_message(r, STREAM_REWRITE, GROUP_REWRITE, msg_id)
                        continue

                    topic = {
                        "title": title,
                        "primary_keyword": (item.get("keywords") or title)[:20],
                        "secondary_keywords": [],
                        "source_content": source_content,
                        "source_title": title,
                        "source_url": item.get("source_url", ""),
                        "content_type": "news",
                        "search_intent": "informational",
                        "quality_score": item.get("quality_score", 65),
                    }
                    res = await ResearchAgent().execute_direct(topic=topic, mode="live")
                    outline = (res or {}).get("outline")
                    materials = res if isinstance(res, dict) else {}
                    if "research_brief" not in materials:
                        materials["research_brief"] = {
                            "source_snapshot": {"source_title": title, "source_summary": source_content[:500]},
                            "source_highlights": [source_content[:200]],
                            "key_facts": [{"fact": source_content[:300]}],
                            "rewrite_constraints": ["保持原文事实准确"],
                            "risk_points": [],
                            "suggested_sections": [],
                            "writer_outline": outline if isinstance(outline, dict) else {"sections": []},
                        }

                    wa = WriterAgent()
                    research_prompt = materials.get("writer_prompt", {}).get("prompt_text", "") if isinstance(materials.get("writer_prompt"), dict) else ""
                    if research_prompt:
                        wa._load_prompt = lambda: research_prompt
                    write = await wa.execute(topic=topic, outline=outline, materials=materials, brand_config={}, dry_run=True)
                    if isinstance(write, dict):
                        art = write.get("article") or {}
                        content = art.get("content_md") or art.get("content") or ""
                        new_title = art.get("title") or title
                    else:
                        content = ""; new_title = title

                    if len(content) < 100:
                        print(f"[{CONSUMER}] id={item['article_id']} 内容过短")
                        await ack_message(r, STREAM_REWRITE, GROUP_REWRITE, msg_id)
                        continue

                    qr2 = await QualityAgent().score_article({
                        "title": new_title, "content": content[:3000], "source_url": "",
                    })
                    q2 = round(float(qr2.get("quality_score", 0)), 1)
                    if q2 >= 75:
                        edited_title = new_title
                        edited_content = content
                        try:
                            from agents.editor_agent import EditorAgent
                            edit = await EditorAgent().execute(
                                article={"title": new_title, "content_md": content},
                                dry_run=False,
                            )
                            if isinstance(edit, dict):
                                edited_title = edit.get("title") or edit.get("edited_title") or edited_title
                                edited_content = edit.get("content_md") or edit.get("content") or edited_content
                        except Exception as edit_error:
                            print(f"[{CONSUMER}] id={item['article_id']} Editor 失败，使用 Writer 输出: {edit_error}")

                        item["title"] = edited_title
                        item["generated_title"] = new_title
                        item["content_md"] = edited_content
                        item["generated_content_md"] = content
                        item["edited_title"] = edited_title
                        item["edited_content_md"] = edited_content
                        item["quality_after"] = q2
                        # 写入审计表
                        import aiomysql, os as _os
                        try:
                            pool = await aiomysql.create_pool(host=_os.environ["MYSQL_HOST"], port=int(_os.environ.get("MYSQL_PORT","3306")),
                                user=_os.environ["MYSQL_USER"], password=_os.environ["MYSQL_PASSWORD"], db="multi_agent_cms",
                                charset="utf8mb4", minsize=1, maxsize=1)
                            async with pool.acquire() as _c:
                                async with _c.cursor() as _cur:
                                    await _cur.execute(
                                        "UPDATE pipeline_audit SET rewrite_quality_after=%s, research_prompt=%s, "
                                        "generated_title=%s, generated_content_md=%s, edited_title=%s, edited_content_md=%s "
                                        "WHERE article_id=%s",
                                        (q2, json.dumps(res, ensure_ascii=False)[:10000] if res else None,
                                         new_title, content, edited_title, edited_content, item.get("article_id")))
                                await _c.commit()
                            pool.close(); await pool.wait_closed()
                        except Exception: pass
                        await r.xadd(STREAM_PUBLISH, {"data": json.dumps(item, ensure_ascii=False)})
                        # 写入 writer_article_outputs
                        try:
                            pool2 = await aiomysql.create_pool(host=_os.environ["MYSQL_HOST"], port=int(_os.environ.get("MYSQL_PORT","3306")),
                                user=_os.environ["MYSQL_USER"], password=_os.environ["MYSQL_PASSWORD"], db="research_article_data",
                                charset="utf8mb4", minsize=1, maxsize=1)
                            async with pool2.acquire() as _c2:
                                async with _c2.cursor() as _cur2:
                                    await _cur2.execute(
                                        "INSERT INTO writer_article_outputs (candidate_id, source_article_id, original_url, source_title, article_score, writer_prompt, writer_model, generated_title, generated_meta_description, generated_content_md, generated_article_json, quality_checks, generation_status, generated_at) "
                                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'generated', NOW()) "
                                        "ON DUPLICATE KEY UPDATE generated_title=VALUES(generated_title), generated_content_md=VALUES(generated_content_md), generated_article_json=VALUES(generated_article_json), quality_checks=VALUES(quality_checks), generation_status='generated', generated_at=VALUES(generated_at)",
                                        (item.get("article_id"), item.get("article_id"), item.get("source_url", ""), title,
                                         item.get("ai_score", 0), json.dumps(res, ensure_ascii=False)[:10000] if res else "",
                                         os.environ.get("WRITER_AGENT_MODEL", ""), new_title, "", content,
                                         json.dumps(write, ensure_ascii=False) if isinstance(write, dict) else None,
                                         json.dumps(qr2, ensure_ascii=False)))
                                await _c2.commit()
                            pool2.close(); await pool2.wait_closed()
                        except Exception: pass
                        print(f"[{CONSUMER}] id={item['article_id']} Q={q2} ✓")
                    else:
                        print(f"[{CONSUMER}] id={item['article_id']} Q={q2} ✗")

                except Exception as e:
                    print(f"[{CONSUMER}] id={item.get('article_id')} 异常: {e}")

                await ack_message(r, STREAM_REWRITE, GROUP_REWRITE, msg_id)


if __name__ == "__main__":
    asyncio.run(main())
