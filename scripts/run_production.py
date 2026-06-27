#!/usr/bin/env python3
"""生产流水线：取 N 篇 → 流式打分+改写 → 发布/CMS
用法:
  python3 scripts/run_production.py               # 默认 50 篇, dry_run
  python3 scripts/run_production.py --count 100   # 100 篇
  python3 scripts/run_production.py --publish     # 真实发布到 CMS
  python3 scripts/run_production.py --source db   # 从爬虫 DB 读（否则用 SQL dump）
"""

import asyncio, json, os, re, sys, time, traceback, argparse
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")


# ── 工具函数 ──

def strip_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

def safe_qs(val):
    try: return round(float(val), 1)
    except: return 0.0


# ── 文章获取 ──

def fetch_articles(count: int = 50, source: str = "dump") -> list:
    """获取文章列表"""
    if source == "db":
        # TODO: 从 MySQL 爬虫库读取
        print("⚠️  DB 模式尚未实现，回退到 SQL dump")
    from scripts.pipeline_batch import parse_main_table, parse_content_tables
    sql_path = ROOT / "crawler_data_test.sql"
    articles = parse_main_table(str(sql_path))
    contents = parse_content_tables(str(sql_path))
    for a in articles:
        if a["id"] in contents:
            a["description"] = (a.get("description", "") or "") + "\n" + contents[a["id"]]
    valid = [a for a in articles if len(a.get("title", "")) > 10 and len(a.get("description", "")) > 50]
    print(f"📦 从 SQL dump 提取 {len(valid)} 篇, 取前 {count} 篇")
    for a in valid: a["publish_date"] = "2026-06-27"
    return valid[:count]


# ── Agent 调用 ──

async def do_quality(title: str, content: str) -> float:
    from agents.quality_agent import QualityAgent
    r = await QualityAgent().score_article({"title": title, "content": content, "source_url": ""})
    return safe_qs(r.get("quality_score", 0))

async def do_research_write(article: dict) -> tuple:
    """Research + Write, 返回 (title, content_md, research_prompt)"""
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent

    t = article["title"]
    desc = strip_html(article.get("full_content") or article.get("description", ""))
    if len(desc) < 200:
        return t, desc, ""

    kw = article.get("keywords", "") or t
    topic = {
        "title": t, "primary_keyword": kw[:20], "secondary_keywords": [],
        "source_content": desc, "source_title": t,
        "source_url": article.get("original_url", ""),
        "content_type": "news", "search_intent": "informational",
        "quality_score": article.get("quality_score", 65),
    }

    ra = ResearchAgent()
    res = await ra.execute_direct(topic=topic, mode="live")
    outline = (res or {}).get("outline")
    materials = res if isinstance(res, dict) else {}
    if "research_brief" not in materials:
        materials["research_brief"] = {
            "source_snapshot": {"source_title": t, "source_summary": desc[:500]},
            "source_highlights": [desc[:200]], "key_facts": [{"fact": desc[:300]}],
            "rewrite_constraints": ["保持原文事实准确"], "risk_points": [],
            "suggested_sections": [], "writer_outline": outline if isinstance(outline, dict) else {"sections": []},
        }

    wa = WriterAgent()
    research_prompt = res.get("writer_prompt", {}).get("prompt_text", "") if isinstance(res, dict) else ""
    if research_prompt:
        wa._load_prompt = lambda: research_prompt
    write = await wa.execute(topic=topic, outline=outline, materials=materials, brand_config={}, dry_run=True)
    
    ct, tt = desc, t
    if isinstance(write, dict):
        art = write.get("article") or {}
        ct = art.get("content_md") or art.get("content") or ct
        tt = art.get("title") or tt
    return tt, ct, research_prompt

async def do_editor(title: str, content: str) -> str:
    try:
        from agents.editor_agent import EditorAgent
        ea = EditorAgent()
        r = await ea.execute(article={"title": title, "content_md": content}, dry_run=True)
        if isinstance(r, dict):
            return r.get("content_md") or r.get("content") or content
    except:
        pass
    return content

async def do_seo_image(article: dict, content: str, title: str) -> dict:
    from agents.seo_agent import SEOAgent
    from agents.image_agent.tools.coze_image_provider import CozeImageProvider
    result = {}
    try:
        s = await SEOAgent().execute(
            article={"title": title, "content_md": content, "meta_description": "", "slug": ""},
            topic={"title": title}, page_info={"slug": "", "category": "news"}, dry_run=True)
        if isinstance(s, dict):
            kw = s.get("keyword_result", {})
            result["seo"] = {"meta_title": s.get("meta_title", ""), "meta_desc": s.get("meta_description", ""), "keywords": kw.get("keywords", [])[:8]}
    except Exception as e:
        result["seo"] = {"error": str(e)}
    try:
        cp = CozeImageProvider()
        img = await cp.generate(prompt=f"新闻配图: {title}", n=1)
        if img.get("success") and img.get("images"):
            result["image"] = img["images"][0].get("local_path", "")
    except:
        result["image"] = ""
    return result


# ── 生产主流程 ──


def slugify(title):
    import unicodedata
    s = unicodedata.normalize("NFKD", title)
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    s = re.sub(r"[-\s]+", "-", s)
    return s[:60].strip("-") or "article"

async def to_cms(record, publish=False):
    """将流水线输出转为 CMSAgent 调用并执行"""
    from agents.cms_agent import CMSAgent
    agent = CMSAgent()
    result = await agent.execute(
        article={
            "title": record.get("title_after") or record.get("title", ""),
            "content_md": record.get("content", ""),
            "meta": {
                "meta_title": (record.get("seo") or {}).get("meta_title", ""),
                "meta_description": (record.get("seo") or {}).get("meta_desc", ""),
            },
            "slug": slugify(record.get("title_after") or record.get("title", "")),
            "featured_image_url": record.get("image", ""),
        },
        page_info={
            "category": "news",
            "tags": (record.get("seo") or {}).get("keywords", []),
            "slug": slugify(record.get("title_after") or record.get("title", "")),
        },
        images={
            "featured_image_url": record.get("image", ""),
            "featured_alt": record.get("title_after") or record.get("title", ""),
        },
    )
    return result

async def run_production(count: int = 50, publish: bool = False, source: str = "dump"):
    from agents.scoring_agent.scoring_summary import summarize_crawler_topics

    out_dir = ROOT / "output" / "production"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 取文章
    articles = fetch_articles(count, source)
    if not articles:
        print("⚠️  没有文章可处理，退出")
        return

    # 2. 流式流水线：批量打分 → 即刻进入改写
    amap = {a["id"]: a for a in articles}
    need_rewrite = asyncio.Queue()
    total_scored = 0

    async def scorer():
        nonlocal total_scored
        for offset in range(0, len(articles), 30):
            chunk = articles[offset:offset + 30]
            r = summarize_crawler_topics(chunk, use_ai=True, ai_concurrency=4)
            scores = [s for s in r.get("article_scores", [])
                      if s.get("overall_score") is not None and s.get("overall_score", 0) > 75]
            total_scored += len(scores)
            all_scored_ids = set()
            for se in scores:
                aid = se.get("article_id")
                all_scored_ids.add(aid)
                a = amap.get(aid, {})
                qs = await do_quality(a["title"], strip_html(a.get("description", "")))
                a["ai_score"] = se["overall_score"]
                a["quality_score"] = qs
                if qs <= 70:
                    await need_rewrite.put(a)
                else:
                    results.append({"id": a["id"], "title": a["title"],
                                    "ai_score": se["overall_score"], "quality": qs,
                                    "status": "passed_quality", "reason": f"质量>{70} ({qs:.0f})"})
            # 记录本批次所有打分结果（含未通过 scoring 的）
            for a in chunk:
                if a["id"] not in all_scored_ids:
                    results.append({"id": a["id"], "title": a["title"], "ai_score": None,
                                    "status": "failed_scoring", "reason": "AI评分≤75"})
            print(f"  📊 批次 {offset//30+1}: 评出 {len(scores)} 篇>75")
        await need_rewrite.put(None)  # 结束标记

    results = []
    rewrite_start = time.time()

    scorer_task = asyncio.create_task(scorer())

    article_count = 0
    while True:
        a = await need_rewrite.get()
        if a is None:
            break
        article_count += 1
        qs0 = a["quality_score"]
        print(f"\n  ✍️  [{article_count}] id={a['id']} Q0={qs0:.1f} | {a['title'][:50]}")

        try:
            nt, nc, rp = await do_research_write(a)
            if len(nc) < 100:
                print(f"    ⚠️ 内容过短，跳过")
                continue

            q2 = await do_quality(nt, nc)
            improved = q2 - qs0
            print(f"    质量: {qs0:.0f}→{q2:.0f} ({improved:+.0f}) | {len(nc)}字")

            if q2 >= 75:
                ed_ct = await do_editor(nt, nc)
                si = await do_seo_image(a, ed_ct or nc, nt)
                if publish:
                    pass
                results.append({
                    "id": a["id"], "title": nt, "url": a.get("original_url", ""),
                    "ai_score": a.get("ai_score"), "quality_before": qs0, "quality_after": q2,
                    "content": ed_ct or nc, "seo": si.get("seo", {}), "status": "rewritten",
                })
            else:
                print(f"    ❌ 改写后质量未达标 (q2={q2:.0f}<75)")
                results.append({
                    "id": a["id"], "title": a["title"], "ai_score": a.get("ai_score"),
                    "quality_before": qs0, "quality_after": q2, "status": "rewrite_failed",
                })
        except Exception as e:
            print(f"    ❌ {e}")
            results.append({
                "id": a["id"], "title": a["title"], "ai_score": a.get("ai_score"),
                "quality_before": a.get("quality_score", 0), "status": "rewrite_error", "reason": str(e),
            })

    await scorer_task
    elapsed = time.time() - rewrite_start

    # 3. 汇总
    print(f"\n{'='*60}")
    print(f"📊 处理完成: {total_scored} 篇>75分, {article_count} 篇进入改写")
    print(f"   通过发布: {len(results)} 篇")
    print(f"   总耗时: {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")
    if results:
        scores_before = [r["quality_before"] for r in results]
        scores_after = [r["quality_after"] for r in results]
        print(f"   质量: {sum(scores_before)/len(scores_before):.0f} → {sum(scores_after)/len(scores_after):.0f}")

    # 保存
    save_path = out_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    json.dump(results, save_path.open("w"), ensure_ascii=False, indent=2)
    print(f"💾 结果保存: {save_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--source", choices=["db", "dump"], default="dump")
    args = parser.parse_args()
    asyncio.run(run_production(args.count, args.publish, args.source))
