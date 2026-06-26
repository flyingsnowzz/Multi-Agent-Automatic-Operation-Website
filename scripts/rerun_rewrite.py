#!/usr/bin/env python3
"""重跑 rewrite 阶段: 用已有分数 + 修复版 WriterAgent"""
import asyncio, json, os, re, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

OUT = ROOT / "output" / "final_test"

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

async def do_quality(title, content):
    from agents.quality_agent import QualityAgent
    r = await QualityAgent().score_article({"title": title, "content": content, "source_url": ""})
    try: return round(float(r.get('quality_score', 0)), 1)
    except: return 0.0

async def do_editor(title, content):
    try:
        from agents.editor_agent import EditorAgent
        ea = EditorAgent()
        r = await ea.execute(article={"title": title, "content_md": content}, dry_run=False)
        if isinstance(r, dict):
            art = r.get("article") or {}
            return art.get("content_md") or art.get("content") or content, r.get("content_html", "")
        return content, ""
    except: return content, ""

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
    except: result['seo'] = {}
    try:
        cp = CozeImageProvider()
        img = await cp.generate(prompt=f"新闻配图: {title}", n=1)
        if img.get('success') and img.get('images'):
            result['image'] = {"local": img['images'][0].get('local_path',''), "url": img['images'][0].get('url','')}
    except: result['image'] = {}
    return result

async def rewrite_one(a, quality_score):
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    
    title = a['title']; desc = strip_html(a.get('full_content','') or a.get('description',''))
    capture = []
    
    class W:
        def __init__(s, llm, cap, label): s._llm=llm; s._cap=cap; s._label=label
        def __getattr__(s,n): return getattr(s._llm,n)
        async def ainvoke(s, msgs, **kw):
            t = [f"[{m.type}]: {m.content}" for m in msgs]
            s._cap.append({"label": s._label, "prompt": "\n\n".join(t)})
            return await s._llm.ainvoke(msgs, **kw)
    
    # Research
    ra = ResearchAgent()
    ra.llm = W(ra.llm, capture, "research")
    topic = {"title": title, "primary_keyword": title[:20], "secondary_keywords": [],
             "source_content": desc, "content_type": "news",
             "workflow_route": "full_rewrite_flow", "route_tier": "rewrite_candidate",
             "quality_score": quality_score,
             "min_word_count": 800, "max_word_count": 1200, "target_word_count": 1000}
    try: res = await ra.execute(topic=topic, mode="live")
    except: res = {}
    
    # Writer - use Research's writer_prompt
    outline = (res or {}).get("outline") or (res or {}).get("detailed_outline")
    materials = res if isinstance(res, dict) else {}
    if "research_brief" not in materials:
        materials["research_brief"] = {
            "source_snapshot": {"source_title": title, "source_summary": desc[:500]},
            "source_highlights": [desc[:200]], "key_facts": [{"fact": desc[:300]}],
            "rewrite_constraints": ["保持原文事实准确"], "risk_points": [],
            "suggested_sections": [], "writer_outline": outline if isinstance(outline, dict) else {"sections": []},
        }
    
    wa = WriterAgent()
    wa.llm = W(wa.llm, capture, "writer")
    rp = res.get("writer_prompt", {}).get("prompt_text", "") if isinstance(res, dict) else ""
    if rp: wa._load_prompt = lambda: rp
    
    brand = {"tone": ["专业","权威","亲和"], "must_include": [], "prohibited_words": [], "recommended_words": []}
    write = await wa.execute(topic=topic, outline=outline, materials=materials, brand_config=brand, dry_run=True)
    
    ct, tt = desc, title
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
        tt = art.get('title') or tt
    if not ct or len(ct) < 100:
        ct = str(write)
    
    return tt, ct, capture

async def main():
    with open(OUT / "final_results.json") as f: data = json.load(f)
    with open(ROOT / "output/pipeline_batch/articles.json") as f: articles = json.load(f)
    amap = {a['id']: a for a in articles}
    
    for idx, r in enumerate(data):
        a = amap.get(r['id'], {})
        if not a: continue
        title = a['title']; qs0 = r['quality_before']; ai_score = r['ai_score']
        
        print(f"[{idx+1}/{len(data)}] id={r['id']} AI={ai_score:.0f} Q0={qs0:.1f} | {title[:45]}"); sys.stdout.flush()
        
        if not strip_html(a.get('description','')):
            print(f"  ⚠️ source_content 为空，跳过")
            continue
        
        best_qs, best_ct, best_tt = qs0, strip_html(a.get('description','')), title
        all_prompts = []
        
        for attempt in range(2):
            print(f"  第{attempt+1}轮...", end=' ', flush=True)
            try:
                nt, nc, prompts = await rewrite_one(a, qs0)
                all_prompts = prompts
                q2 = await do_quality(nt, nc)
                print(f"Q={q2:.1f} {len(nc)}字", flush=True)
                if q2 > best_qs or (len(nc) > len(best_ct)*2 and len(best_ct) < 500):
                    best_qs, best_ct, best_tt = q2, nc, nt
                if q2 >= 85: print(f"  ✅ >=85!"); break
            except Exception as e:
                print(f"❌ {e}"); flush(); break
        
        delta = best_qs - qs0
        print(f"  📊 {qs0}→{best_qs} (+{delta:+.0f}) {len(best_ct)}字", flush=True)
        
        # Editor
        ed_ct, html = await do_editor(best_tt, best_ct)
        print(f"  ✂️ Editor: MD={len(ed_ct)}字 HTML={len(html)}字", flush=True)
        
        # SEO + Image
        si = await do_seo_img(a, ed_ct or best_ct, best_tt)
        
        r['quality_after'] = best_qs
        r['content_after_write'] = best_ct
        r['content_after_editor'] = ed_ct or best_ct
        r['content_html'] = html
        r['actual_prompts'] = all_prompts
        r['img_seo'] = si
        
        with open(OUT / "final_results.json", 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        time.sleep(1)
    
    # Markdown
    art_dir = OUT / "articles"; art_dir.mkdir(exist_ok=True)
    for r in data:
        seo = (r.get('img_seo', {}) or {}).get('seo', {}) or {}
        img = (r.get('img_seo', {}) or {}).get('image', {}) or {}
        html = r.get('content_html', '')
        md = f"# {r['title']}\n\n"
        md += f"> AI: {r['ai_score']:.0f} | Q: {r['quality_before']}→{r['quality_after']}\n\n"
        md += f"> {r.get('url','')}\n\n"
        md += f"## SEO\n{seo.get('meta_title','')}\n\n"
        md += f"## Editor正文\n{r.get('content_after_editor','')}\n\n"
        if html: md += f"## HTML\n```html\n{html[:3000]}\n```\n\n"
        if img.get('local'): md += f"![封面]({img['local']})\n"
        (art_dir / f"{r['id']:04d}.md").write_text(md, encoding='utf-8')
    
    print(f"\n✅ {len(data)}篇完成")

asyncio.run(main())
