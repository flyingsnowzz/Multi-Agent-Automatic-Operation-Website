#!/usr/bin/env python3
"""测试调度器：每 5 分钟跑 5 篇，5 次自动停止。
用法: python3 scripts/run_test_schedule.py
"""

import asyncio, json, os, re, sys, time, traceback
from pathlib import Path
from datetime import datetime
import signal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

ARTICLES_PER_RUN = 5
INTERVAL_SECONDS = 300  # 5 分钟
MAX_RUNS = 5

def strip_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

def safe_qs(val):
    try: return round(float(val), 1)
    except: return 0.0

async def do_quality(title, content):
    from agents.quality_agent import QualityAgent
    r = await QualityAgent().score_article({"title": title, "content": content, "source_url": ""})
    return safe_qs(r.get("quality_score", 0))

async def do_research_write(article):
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
    rp = res.get("writer_prompt", {}).get("prompt_text", "") if isinstance(res, dict) else ""
    if rp:
        wa._load_prompt = lambda: rp
    write = await wa.execute(topic=topic, outline=outline, materials=materials, brand_config={}, dry_run=True)
    ct, tt = desc, t
    if isinstance(write, dict):
        art = write.get("article") or {}
        ct = art.get("content_md") or art.get("content") or ct
        tt = art.get("title") or tt
    return tt, ct, rp

async def do_editor(title, content):
    try:
        from agents.editor_agent import EditorAgent
        r = await EditorAgent().execute(article={"title": title, "content_md": content}, dry_run=True)
        if isinstance(r, dict):
            return r.get("content_md") or r.get("content") or content
    except: pass
    return content

async def do_seo_image(article, content, title):
    from agents.seo_agent import SEOAgent
    from agents.image_agent.tools.coze_image_provider import CozeImageProvider
    result = {}
    try:
        s = await SEOAgent().execute(
            article={"title": title, "content_md": content, "meta_description": "", "slug": ""},
            topic={"title": title}, page_info={"slug": "", "category": "news"}, dry_run=True)
        if isinstance(s, dict):
            kw = s.get("keyword_result", {})
            result["seo"] = {"meta_title": s.get("meta_title", ""), "meta_desc": s.get("meta_description", ""), "pk": kw.get("primary_keyword", ""), "sk": kw.get("secondary_keywords", [])[:5]}
    except: pass
    try:
        cp = CozeImageProvider()
        img = await cp.generate(prompt=f"新闻配图: {title}", n=1)
        if img.get("success") and img.get("images"):
            result["image"] = img["images"][0].get("local_path", "")
    except: pass
    return result

async def run_one_batch(run_id: int, out_dir: Path) -> list:
    from agents.scoring_agent.scoring_summary import summarize_crawler_topics

    # 从 SQL dump 取文章，每轮取不同区间
    from scripts.pipeline_batch import parse_main_table, parse_content_tables
    all_articles = []
    sql_path = ROOT / "crawler_data_test.sql"
    if sql_path.exists():
        all_articles = parse_main_table(str(sql_path))
        contents = parse_content_tables(str(sql_path))
        for a in all_articles:
            if a["id"] in contents:
                a["description"] = (a.get("description", "") or "") + "\n" + contents[a["id"]]
    valid = [a for a in all_articles if len(a.get("title", "")) > 10 and len(strip_html(a.get("description", ""))) > 500]

    # 按轮次取不同区间，避免重复处理相同文章
    start = (run_id - 1) * ARTICLES_PER_RUN * 2  # 多取一些因大部分会被筛掉
    chunk = valid[start:start + ARTICLES_PER_RUN * 2]
    for a in chunk: a["publish_date"] = "2026-06-27"
    amap = {a["id"]: a for a in chunk}

    print(f"  📦 区间 [{start}:{start+len(chunk)}] 共 {len(chunk)} 篇候选")
    
    records = []  # 记录所有文章的打分结果

    # 打分
    r = summarize_crawler_topics(chunk, use_ai=True, ai_concurrency=4)
    all_scores = r.get("article_scores", [])
    
    for se in all_scores:
        aid = se.get("article_id")
        a = amap.get(aid, {})
        ai_score = se.get("overall_score") or 0
        
        if ai_score is None or ai_score <= 75:
            records.append({
                "id": aid, "title": a.get("title", ""),
                "ai_score": ai_score, "status": "failed_scoring",
                "reason": f"AI评分≤75 ({ai_score:.0f})" if ai_score else "无评分"
            })
            continue

        qs = await do_quality(a["title"], strip_html(a.get("description", "")))
        a["ai_score"] = ai_score; a["quality_score"] = qs

        if qs > 70:
            records.append({
                "id": aid, "title": a.get("title", ""),
                "ai_score": ai_score, "quality": qs, "status": "passed_quality",
                "reason": f"质量>{70} ({qs:.0f})"
            })
            continue

        # quality ≤ 70 → 进入改写
        print(f"    🔄 id={aid} score={ai_score:.0f} quality={qs:.1f} | {a['title'][:40]}")
        try:
            nt, nc, rp = await do_research_write(a)
            if len(nc) < 100:
                records.append({
                    "id": aid, "title": a["title"], "ai_score": ai_score,
                    "quality_before": qs, "status": "rewrite_failed",
                    "reason": "生成内容过短"
                })
                continue

            q2 = await do_quality(nt, nc)
            improved = q2 - qs
            print(f"      质量: {qs:.0f}→{q2:.0f} ({improved:+.0f}) | {len(nc)}字")

            ed_ct = await do_editor(nt, nc)
            si = await do_seo_image(a, ed_ct or nc, nt)
            
            records.append({
                "id": aid, "title_before": a["title"], "title_after": nt,
                "ai_score": ai_score, "quality_before": qs, "quality_after": q2,
                "improvement": round(improved, 1), "word_count": len(nc),
                "content": ed_ct or nc, "seo": si.get("seo", {}),
                "status": "rewritten"
            })
            cms_r = await to_cms(records[-1], publish=False)
            records[-1]["cms"] = {"status": cms_r.get("status"), "slug": cms_r.get("slug")}
        except Exception as e:
            records.append({
                "id": aid, "title": a["title"], "ai_score": ai_score,
                "quality_before": qs, "status": "rewrite_error", "reason": str(e)
            })

    return records



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
            "tags": (record.get("seo") or {}).get("sk", []),
            "slug": slugify(record.get("title_after") or record.get("title", "")),
        },
        images={
            "featured_image_url": record.get("image", ""),
            "featured_alt": record.get("title_after") or record.get("title", ""),
        },
    )
    return result

async def main():
    out_dir = ROOT / "output" / "test_schedule"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    shutdown_flag = False
    def on_sigint(sig, frame):
        nonlocal shutdown_flag
        print("\n\n🛑 收到停止信号，当前轮结束后退出...")
        shutdown_flag = True
    signal.signal(signal.SIGINT, on_sigint)
    
    all_runs = []
    start_time = time.time()

    for run_id in range(1, MAX_RUNS + 1):
        round_start = time.time()
        print(f"\n{'='*60}")
        print(f"🔄 第 {run_id}/{MAX_RUNS} 轮 {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*60}")

        try:
            records = await run_one_batch(run_id, out_dir)
            rewritten = sum(1 for r in records if r["status"] == "rewritten")
            passed = sum(1 for r in records if r["status"] == "passed_quality")
            failed = sum(1 for r in records if r["status"] == "failed_scoring")
            print(f"  📊 结果: {len(records)} 篇打分 | {rewritten} 改写 | {passed} 通过 | {failed} 淘汰")
            all_runs.append({
                "run_id": run_id,
                "time": datetime.now().isoformat(),
                "total": len(records),
                "rewritten": rewritten,
                "passed_quality": passed,
                "failed_scoring": failed,
                "records": records,
            })
        except Exception as e:
            print(f"  ❌ 本轮异常: {e}")
            traceback.print_exc()

        # 每轮结束立即保存
        json.dump(all_runs, (out_dir / "all_runs.json").open("w"), ensure_ascii=False, indent=2)
        print(f"  💾 已保存 (共 {len(all_runs)} 轮)")

        if shutdown_flag:
            print(f"\n  🛑 用户中止，退出")
            break
        if run_id < MAX_RUNS:
            elapsed = time.time() - round_start
            wait = max(0, INTERVAL_SECONDS - elapsed)
            print(f"\n  ⏳ 本轮回合耗时 {elapsed:.0f}秒, 等待 {wait:.0f}秒 后下一轮... (Ctrl+C 可提前退出)")
            await asyncio.sleep(wait)

    total_time = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"✅ {MAX_RUNS} 轮全部完成，总耗时 {total_time/60:.0f} 分钟")
    print(f"💾 结果: {out_dir / 'all_runs.json'}")

    json.dump(all_runs, (out_dir / "all_runs.json").open("w"), ensure_ascii=False, indent=2)


asyncio.run(main())
