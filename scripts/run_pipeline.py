#!/usr/bin/env python3
"""Pipeline: AI评分 → 质量 → 调研+写作 → 编辑 → 配图+SEO(并行)"""

import asyncio, json, os, re, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "output" / "pipeline_batch"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

def save_json(name, data):
    (OUT_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

async def phase_quality(article):
    from agents.quality_agent import QualityAgent
    agent = QualityAgent()
    r = await agent.score_article({
        "title": article['title'],
        "content": strip_html(article.get('description', '')),
        "source_url": article.get('original_url', ''),
    })
    qs = r.get('quality_score', 0)
    if isinstance(qs, (int, float)):
        qs = round(float(qs), 1)
    return qs

async def phase_research_write(article):
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    title = article['title']
    topic = {"title": title, "primary_keyword": title[:10], "original_url": article.get('original_url',''),
             "source_content": strip_html(article.get('description','')), "content_type": "news"}
    ra = ResearchAgent()
    research = await ra.execute(topic=topic, mode="mock")
    outline = (research or {}).get("outline") or (research or {}).get("detailed_outline")
    wa = WriterAgent()
    write = await wa.execute(topic=topic, outline=outline, materials=research or {}, dry_run=True)
    # Extract
    content = best_content = strip_html(article.get('description',''))
    title_out = article['title']
    if isinstance(write, dict):
        art = write.get('article') or {}
        content = art.get('content_md') or art.get('content') or content
        title_out = art.get('title') or title_out
    return title_out, content

async def phase_edit(content_text, content_title):
    from agents.editor_agent import EditorAgent
    agent = EditorAgent()
    r = await agent.execute(article={"title": content_title, "content_md": content_text}, dry_run=True)
    return r.get("content_md", content_text)

async def phase_seo(article, content_text, content_title):
    from agents.seo_agent import SEOAgent
    try:
        agent = SEOAgent()
        seo = await agent.execute(article={"title": content_title, "content_md": content_text,
            "meta_description": "", "slug": ""}, topic=article, page_info={"slug": "", "category": "news"}, dry_run=True)
        return seo if isinstance(seo, dict) else {"raw": str(seo)}
    except Exception as e:
        return {"error": str(e)}

async def phase_image(content_text, content_title):
    from agents.image_agent.image_agent import ImageAgent
    try:
        ia = ImageAgent()
        img = await ia.generate_featured_image(prompt=f"封面图: {content_title}\n{content_text[:800]}", visual_style="professional")
        return img if isinstance(img, dict) else {"raw": str(img)}
    except Exception as e:
        return {"error": str(e)}

async def main():
    from agents.scoring_agent.scoring_summary import summarize_crawler_topics
    
    print("=" * 60)
    print("🚀 Pipeline: AI评分 → 质量 → 调研+写作 → 编辑 → 配图+SEO(并行)")
    print("=" * 60)
    
    articles = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    for a in articles: a['publish_date'] = '2026-06-25'
    
    # Phase 1: Score batches until 20 >75
    print("\n📊 Phase 1: AI 评分 (并发=4)")
    above_75 = []
    batch_size = 30
    scored_all = []
    
    for offset in range(0, len(articles), batch_size):
        batch = articles[offset:offset+batch_size]
        result = summarize_crawler_topics(batch, use_ai=True, ai_concurrency=4)
        scores = [s for s in result.get("article_scores", []) if s.get("overall_score") is not None]
        scored_all.extend(scores)
        
        new_above = [s for s in scores if s.get("overall_score", 0) > 75]
        above_75.extend(new_above)
        print(f"   批次 {offset//batch_size+1}: {len(scores)}篇, +{len(new_above)}篇>75, 累计{len(above_75)}")
        
        if len(above_75) >= 20:
            above_75 = above_75[:20]
            break
    
    print(f"\n   ✅ 评分完成: {len(above_75)}篇>75")
    for s in above_75:
        print(f"   [{s['overall_score']:.1f}] id={s.get('article_id')} | {s.get('title','')[:50]}")
    
    article_map = {a['id']: a for a in articles}
    save_json("01_ai_scoring.json", {"above_75": above_75})
    
    # Phase 2-4
    print(f"\n📋 Phase 2-4: 质量 → 调研/写作 → 编辑 → 配图/SEO(并行)")
    final = []
    
    for idx, se in enumerate(above_75):
        aid = se.get('article_id')
        a = article_map.get(aid, {})
        title = a.get('title', '')
        
        print(f"\n{'='*50}")
        print(f"[{idx+1}/20] id={aid} score={se['overall_score']:.0f} | {title[:60]}")
        
        qs = await phase_quality(a)
        print(f"  📊 质量: {qs}")
        
        best_qs, best_ct, best_tt, rw_cnt = qs, strip_html(a.get('description','')), title, 0
        
        if qs <= 70:
            print(f"  🔄 质量<=70，重写...")
            for attempt in range(2):
                print(f"  📝 第{attempt+1}轮...")
                try:
                    nt, nc = await phase_research_write(a)
                    q2 = await phase_quality({**a, 'description': nc, 'title': nt})
                    print(f"  📊 第{attempt+1}轮: {q2}")
                    if q2 > best_qs: best_qs, best_ct, best_tt = q2, nc, nt
                    rw_cnt += 1
                    if q2 >= 85: print(f"  ✅ >=85!"); break
                except Exception as e:
                    print(f"  ❌ {e}"); traceback.print_exc()
            print(f"  📊 最终: {best_qs} (重写{rw_cnt}次)")

        print(f"  📝 编辑...")
        best_ct = await phase_edit(best_ct, best_tt)

        print(f"  🎨 配图+SEO (并行)...")
        seo_result, img_result = await asyncio.gather(
            phase_seo(a, best_ct, best_tt),
            phase_image(best_ct, best_tt)
        )
        img_seo = {"seo": seo_result, "image": img_result}
        
        final.append({"article_id": aid, "title": best_tt, "ai_score": se['overall_score'],
                       "quality": best_qs, "rewrites": rw_cnt, "image_seo": img_seo})
        save_json("02_pipeline_results.json", final)
        time.sleep(1)
    
    print(f"\n{'='*60}")
    print(f"✅ 完成 {len(final)} 篇 → {OUT_DIR}")

asyncio.run(main())
