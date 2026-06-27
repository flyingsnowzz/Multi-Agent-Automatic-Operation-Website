#!/usr/bin/env python3
"""完整 Pipeline: 打分→质量(≤70)→调研→写作→质量复评→Editor→配图+SEO
输出: 含AI分/质量/Editor分/原文地址/Research prompt/完整文章"""

import asyncio, json, os, re, sys, time, traceback, math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

OUT = ROOT / "output" / "final_test"
OUT.mkdir(parents=True, exist_ok=True)

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()



# ── 执行日志 ──
from scripts.execution_logger import ExecutionLogger
_logger = ExecutionLogger()

def _log_agent(agent, inp, out, error=None, **kw):
    """简化的日志调用，dict 值自动截断避免过大"""
    def _trim(v, n=200):
        s = str(v)
        return s[:n] if len(s) > n else v
    _logger.agent_call(agent, {k: _trim(v) for k,v in inp.items()},
                       {k: _trim(v) for k,v in out.items()}, error=error, **kw)
def save_json(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def safe_qs(val):
    try: return round(float(val), 1)
    except: return 0.0

async def do_quality(title, content):
    from agents.quality_agent import QualityAgent
    r = await QualityAgent().score_article({"title": title, "content": content, "source_url": ""})
    qs = safe_qs(r.get('quality_score', 0))
    _log_agent("quality", {"title": title[:60]}, {"score": qs, "reasons": r.get("reasons", [])[:3]})
    return qs

async def do_editor(title, content):
    try:
        from agents.editor_agent import EditorAgent
        ea = EditorAgent()
        r = await ea.execute(article={"title": title, "content_md": content}, dry_run=True)
        if isinstance(r, dict):
            art = r.get("article") or {}
            ed_content = art.get("content_md") or art.get("content") or content
            ed_score = r.get("quality_score") or r.get("edit_score")
            _log_agent("editor", {"title": title[:60], "word_count": len(content)},
                       {"score": safe_qs(ed_score), "word_count": len(ed_content)})
            return ed_content, safe_qs(ed_score)
        return content, 0
    except:
        return content, 0

async def do_rw_with_prompt(article):
    """Research+Write, 同时捕获 ResearchAgent 的 prompt"""
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    t = article['title']; desc = strip_html(article.get('full_content') or article.get('description',''))
    if len(desc) < 200:
        return t, desc, ""
    
    kw = article.get('keywords','') or t
    qs = article.get('quality_score') or article.get('quality') or 65
    topic = {
        "title": t, "primary_keyword": kw[:20], "secondary_keywords": [],
        "source_content": desc, "source_title": t,
        "source_url": article.get('original_url', ''),
        "content_type": "news", "search_intent": "informational",
        "quality_score": qs}
    
    ra = ResearchAgent()
    # 捕获 ResearchAgent 内部 prompt (通过 monkey patch)
    research_prompt = ""
    try:
        orig = ra.llm.ainvoke
        async def capture(messages, **kw):
            nonlocal research_prompt
            for msg in messages:
                research_prompt += str(msg.content)[:5000]
            return await orig(messages, **kw)
        ra.llm.ainvoke = capture
    except: pass
    
    _logger.phase_start("research_write", title=t[:60])
    res = await ra.execute_direct(topic=topic, mode="live")
    outline = (res or {}).get("outline") or (res or {}).get("detailed_outline")
    
    materials = res if isinstance(res, dict) else {}
    if "research_brief" not in materials:
        materials["research_brief"] = {
            "source_snapshot": {"source_title": t, "source_summary": desc[:500]},
            "source_highlights": [desc[:200]], "key_facts": [{"fact": desc[:300]}],
            "rewrite_constraints": ["保持原文事实准确"], "risk_points": [],
            "suggested_sections": [], "writer_outline": outline if isinstance(outline, dict) else {"sections": []},
        }
    
    brand_config = {"tone": ["专业","权威","亲和"], "must_include": [], "prohibited_words": [], "recommended_words": []}
    wa = WriterAgent()
    # 用 Research 生成的 writer_prompt 替代 WriterAgent 自带的 prompt.md
    # writer_prompt 在 res 顶层，不在 research_brief 里
    research_prompt = res.get("writer_prompt", {}).get("prompt_text", "") if isinstance(res, dict) else ""
    if research_prompt:
        wa._load_prompt = lambda: research_prompt
    write = await wa.execute(topic=topic, outline=outline, materials=materials, brand_config=brand_config, dry_run=True)
    rp_len = len(research_prompt)
    _log_agent("research", {"content_len": len(desc)}, {"prompt_len": rp_len})
    
    ct, tt = desc, t
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
        tt = art.get('title') or tt
    if not ct or len(ct) < 100:
        ct = str(write)
    _log_agent("writer", {"prompt_len": rp_len}, {"title": tt[:60], "word_count": len(ct)})
    return tt, ct, research_prompt

async def do_seo_img(article, content, title):
    from agents.seo_agent import SEOAgent
    from agents.image_agent.tools.coze_image_provider import CozeImageProvider
    result = {}
    try:
        s = await SEOAgent().execute(article={"title":title,"content_md":content,"meta_description":"","slug":""},
                                       topic=article, page_info={"slug":"","category":"news"}, dry_run=True, language='auto')
        if isinstance(s, dict):
            kw = s.get('keyword_result', {})
            result['seo'] = {"meta_title": s.get('meta_title',''), "meta_desc": s.get('meta_description',''),
                            "pk": kw.get('primary_keyword',''), "sk": kw.get('secondary_keywords',[])[:5]}
    except Exception as e: result['seo'] = {"error": str(e)}
    try:
        cp = CozeImageProvider()
        img = await cp.generate(prompt=f"新闻配图: {title}", n=1)
        if img.get('success') and img.get('images'):
            result['image'] = {"local": img['images'][0].get('local_path',''), "url": img['images'][0].get('url','')}
    except: result['image'] = {}
    return result

async def main():
    from agents.scoring_agent.scoring_summary import summarize_crawler_topics
    
    print("=" * 60)
    print("🚀 完整 Pipeline: 打分→质量→调研→写作→Editor→配图+SEO")
    print("=" * 60)
    
    articles = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    for a in articles: a['publish_date'] = '2026-06-26'
    amap = {a['id']: a for a in articles}
    
    # Phase 1: Score + Quality, only keep <=70
    target = 5; need_rewrite = []; scored_total = 0
    print(f"\n📊 打分+质量, 筛选 quality≤70 (目标{target}篇)...")
    for offset in range(0, len(articles), 30):
        chunk = articles[offset:offset+30]
        _logger.phase_start("scoring_batch", offset=offset, count=len(chunk))
        r = summarize_crawler_topics(chunk, use_ai=True, ai_concurrency=4)
        scores = [s for s in r.get("article_scores", []) if s.get("overall_score") is not None and s.get("overall_score",0) > 75]
        _logger.phase_end("scoring_batch", above_75=len(scores))
        for se in scores:
            scored_total += 1
            a = amap.get(se.get('article_id'), {})
            qs = await do_quality(a['title'], strip_html(a.get('description','')))
            if qs <= 70:
                need_rewrite.append({"score": se, "article": a, "quality": qs})
                print(f"  🔄 [{len(need_rewrite)}/{target}] id={a['id']} score={se['overall_score']:.0f} quality={qs:.1f}")
            if len(need_rewrite) >= target: break
        print(f"  累计{scored_total}篇, 需重写{len(need_rewrite)}篇")
        if len(need_rewrite) >= target: break
    
    _logger.phase_end("scoring", need_rewrite=len(need_rewrite))
    print(f"\n✅ {len(need_rewrite)}篇需重写\n📝 调研→写作→Editor→配图+SEO\n")
    final = []
    
    for idx, item in enumerate(need_rewrite):
        a = item['article']; se = item['score']; title = a['title']
        ai_score = se['overall_score']; qs0 = item['quality']; url = a.get('original_url','')
        
        print(f"[{idx+1}/20] id={a['id']} AI={ai_score:.0f} Q0={qs0:.1f} | {title[:50]}"); sys.stdout.flush()
        
        # Research + Write
        best_qs, best_ct, best_tt, rp = qs0, strip_html(a.get('description','')), title, ""
        for attempt in range(1):
            print(f"  第{attempt+1}轮..."); sys.stdout.flush()
            try:
                nt, nc, rp = await do_rw_with_prompt(a)
                q2 = await do_quality(nt, nc)
                print(f"  质量: {q2:.1f} | {len(nc)}字"); sys.stdout.flush()
                # 如果当前更好，或原内容太短且新内容更长，则采用
                if q2 > best_qs or (len(nc) > len(best_ct)*2 and len(best_ct) < 500):
                    best_qs, best_ct, best_tt = q2, nc, nt
                if q2 >= 85: print(f"  ✅ >=85!"); sys.stdout.flush(); break
            except Exception as e: print(f"  ❌ {e}"); traceback.print_exc()
        
        q_change = best_qs - qs0
        print(f"  📊 质量: {qs0}→{best_qs} ({q_change:+.0f}) | 重写后{len(best_ct)}字"); sys.stdout.flush()
        
        # Editor
        print(f"  ✂️ Editor..."); sys.stdout.flush()
        ed_ct, ed_score = await do_editor(best_tt, best_ct)
        print(f"  Editor分: {ed_score:.1f} | {len(ed_ct)}字"); sys.stdout.flush()
        
        # SEO ∥ Image 并行
        print(f"  🎨 配图+SEO (并行)..."); sys.stdout.flush()
        seo_task = do_seo_img(a, ed_ct or best_ct, best_tt)
        si = await seo_task
        
        final.append({
            "id": a['id'], "title": best_tt, "url": url,
            "ai_score": ai_score, "quality_before": qs0, "quality_after": best_qs,
            "editor_score": ed_score, "content_before": strip_html(a.get('description','')),
            "content_after_write": best_ct, "content_after_editor": ed_ct or best_ct,
            "research_prompt": rp[:3000], "seo": si.get('seo',{}), "image": si.get('image',{}),
        })
        save_json("final_results.json", final)
        time.sleep(1)
    
    # Save Markdown
    art_dir = OUT / "articles"; art_dir.mkdir(exist_ok=True)
    for r in final:
        seo = r.get('seo', {}) or {}; img = r.get('image', {}) or {}
        md = f"# {r['title']}\n\n"
        md += f"> AI评分: {r['ai_score']:.0f} | 质量: {r['quality_before']}→{r['quality_after']} | Editor: {r['editor_score']:.1f}\n\n"
        md += f"> 原文地址: {r['url']}\n\n"
        md += f"## SEO\n- Title: {seo.get('meta_title','')}\n- Desc: {seo.get('meta_desc','')}\n- 关键词: {seo.get('pk','')} | {seo.get('sk',[])}\n\n"
        md += f"## Editor后正文\n{r['content_after_editor']}\n\n"
        if img.get('local'): md += f"![封面]({img['local']})\n"
        (art_dir / f"{r['id']:04d}.md").write_text(md, encoding='utf-8')
    
    _logger.close()
    print(f"\n{'='*60}")
    print(f"✅ {len(final)}篇完成 → {OUT}")

asyncio.run(main())
