#!/usr/bin/env python3
"""重写功能测试: 打分→质量→只留≤70→攒够20→写作→配图+SEO"""

import asyncio, json, os, re, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

OUT = ROOT / "output" / "rewrite_test"
OUT.mkdir(parents=True, exist_ok=True)

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

def save_json(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

async def do_quality(article):
    from agents.quality_agent import QualityAgent
    r = await QualityAgent().score_article({
        "title": article['title'], "content": strip_html(article.get('description','')),
        "source_url": article.get('original_url',''),
    })
    return round(float(r.get('quality_score',0)), 1)

async def do_rw(article):
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    t = article['title']; desc = strip_html(article.get('description',''))
    
    kw = article.get('keywords','') or t
    qs = article.get('quality_score') or article.get('quality') or 65
    topic = {
        "workflow_route": "full_rewrite_flow",
        "route_tier": "rewrite_candidate",
        "quality_score": qs,
        "title": t, "primary_keyword": kw[:20] if kw else t[:20],
        "secondary_keywords": [], "source_content": desc, "content_type": "news",
        "search_intent": "informational", "min_word_count": 800, "max_word_count": 1200, "target_word_count": 1000,
    }
    
    ra = ResearchAgent(); res = await ra.execute(topic=topic, mode="live")
    outline = (res or {}).get("outline") or (res or {}).get("detailed_outline")
    
    materials = res if isinstance(res, dict) else {}
    if "research_brief" not in materials:
        materials["research_brief"] = {
            "source_snapshot": {"source_title": t, "source_summary": desc[:500]},
            "source_highlights": [desc[:200]],
            "key_facts": [{"fact": desc[:300]}],
            "rewrite_constraints": ["保持原文事实准确"],
            "risk_points": [], "suggested_sections": [],
            "writer_outline": outline if isinstance(outline, dict) else {"sections": []},
        }
    
    brand_config = {"tone": ["专业","权威","亲和"], "must_include": [], "prohibited_words": [], "recommended_words": []}
    
    wa = WriterAgent()
    write = await wa.execute(topic=topic, outline=outline, materials=materials, brand_config=brand_config, dry_run=True)
    ct, tt = desc, t
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
        tt = art.get('title') or tt
    if not ct or len(ct) < 100:
        ct = str(write) if not isinstance(write, dict) else json.dumps(write, ensure_ascii=False)
    return tt, ct

async def do_image_seo(article, content, title):
    from agents.seo_agent import SEOAgent
    from agents.image_agent.tools.coze_image_provider import CozeImageProvider
    result = {}
    try:
        s = await SEOAgent().execute(article={"title":title,"content_md":content,"meta_description":"","slug":""},
                                       topic=article, page_info={"slug":"","category":"news"}, dry_run=True, language='auto')
        result['seo'] = s if isinstance(s, dict) else {"raw": str(s)}
    except Exception as e: result['seo'] = {"error": str(e)}
    try:
        cp = CozeImageProvider()
        img = await cp.generate(prompt=f"新闻配图，专业风格: {title}", n=1)
        if img.get('success') and img.get('images'):
            result['image'] = {"url": img['images'][0].get('url',''), "local": img['images'][0].get('local_path','')}
        else:
            result['image'] = {"error": img.get('error','')}
    except Exception as e: result['image'] = {"error": str(e)}
    return result

async def main():
    from agents.topic_agent.topic_summary import summarize_crawler_topics
    
    print("=" * 60)
    print("🧪 重写功能测试 (修复版)")
    print("=" * 60)
    
    articles = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    for a in articles: a['publish_date'] = '2026-06-26'
    amap = {a['id']: a for a in articles}
    
    target = 20; need_rewrite = []; scored_total = 0; batch = 30
    
    print(f"\n📊 打分+质量, 筛选 quality≤70 (目标{target}篇)...")
    for offset in range(0, len(articles), batch):
        chunk = articles[offset:offset+batch]
        r = summarize_crawler_topics(chunk, use_ai=True, ai_concurrency=4)
        scores = [s for s in r.get("article_scores", []) if s.get("overall_score") is not None and s.get("overall_score",0) > 75]
        for se in scores:
            scored_total += 1
            a = amap.get(se.get('article_id'), {})
            qs = await do_quality(a)
            if qs <= 70:
                need_rewrite.append({"score": se, "article": a, "quality": qs})
                print(f"  🔄 [{len(need_rewrite)}/{target}] id={a['id']} score={se['overall_score']:.0f} quality={qs:.1f} | {a['title'][:50]}")
            elif len(need_rewrite) < 3:
                print(f"  ⏭️ id={a['id']} quality={qs:.1f} 跳过")
            if len(need_rewrite) >= target: break
        print(f"  累计{scored_total}篇, 需重写{len(need_rewrite)}篇")
        if len(need_rewrite) >= target: break
    
    print(f"\n✅ 筛选完成: {len(need_rewrite)} 篇\n📝 写作+重评+配图+SEO\n")
    final = []
    
    for idx, item in enumerate(need_rewrite):
        a = item['article']; se = item['score']; title = a.get('title','')
        ai_score = se['overall_score']; first_qs = item['quality']
        
        print(f"[{idx+1}/{len(need_rewrite)}] id={a['id']} AI={ai_score:.0f} 原始质量={first_qs:.1f} | {title[:50]}"); sys.stdout.flush()
        best_qs, best_ct, best_tt, rw = first_qs, strip_html(a.get('description','')), title, 0
        
        for attempt in range(2):
            print(f"  第{attempt+1}轮..."); sys.stdout.flush()
            try:
                nt, nc = await do_rw(a)
                q2 = await do_quality({**a, 'description': nc, 'title': nt})
                print(f"  质量: {q2:.1f} | {len(nc)}字"); sys.stdout.flush()
                if q2 > best_qs: best_qs, best_ct, best_tt = q2, nc, nt
                rw += 1
                if q2 >= 85: print(f"  ✅ >=85!"); sys.stdout.flush(); break
            except Exception as e: print(f"  ❌ {e}"); traceback.print_exc()
        
        delta = best_qs - first_qs
        print(f"  📊 {first_qs}→{best_qs} ({delta:+.0f}) {len(best_ct)}字"); sys.stdout.flush()
        
        print(f"  🎨 配图+SEO..."); sys.stdout.flush()
        is_res = await do_image_seo(a, best_ct, best_tt)
        final.append({"id": a['id'], "title": best_tt, "ai_score": ai_score,
                       "quality_before": first_qs, "quality_after": best_qs,
                       "rewrites": rw, "word_count": len(best_ct), "content": best_ct, "img_seo": is_res})
        save_json("rewrite_results.json", final)
        time.sleep(1)
    
    # Save Markdown
    art_dir = OUT / "articles"; art_dir.mkdir(exist_ok=True)
    for r in final:
        isr = r.get('img_seo', {}); s = isr.get('seo', {}) if isinstance(isr, dict) else {}
        img = isr.get('image', {}) if isinstance(isr, dict) else {}
        mt = s.get('meta_title','') if isinstance(s, dict) else ''; md = s.get('meta_description','') if isinstance(s, dict) else ''
        kw = s.get('keyword_result', {}) if isinstance(s, dict) else {}
        md_text = f"# {r['title']}\n\n> AI评分: {r['ai_score']:.0f} | 质量: {r['quality_before']}→{r['quality_after']} | {r['word_count']}字\n\n"
        md_text += f"## SEO\n- Title: {mt}\n- Desc: {md}\n- 关键词: {kw.get('primary_keyword','')} | {kw.get('secondary_keywords',[])[:3]}\n\n## 正文\n{r['content']}\n\n"
        if img.get('local'): md_text += f"![封面]({img['local']})\n"
        (art_dir / f"{r['id']:04d}.md").write_text(md_text, encoding='utf-8')
    
    improvements = [r['quality_after'] - r['quality_before'] for r in final]
    wcs = [r['word_count'] for r in final]
    print(f"\n{'='*60}")
    print(f"✅ 完成 {len(final)}篇 | >=85: {sum(1 for r in final if r['quality_after']>=85)}篇")
    print(f"质量提升: avg {sum(improvements)/len(improvements):+.1f} | 字数: {min(wcs)}-{max(wcs)} avg{sum(wcs)//len(wcs)}")
    print(f"📁 {OUT}")

asyncio.run(main())
