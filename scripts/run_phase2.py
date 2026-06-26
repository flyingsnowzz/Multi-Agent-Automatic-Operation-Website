#!/usr/bin/env python3
"""Phase 2-4: 质量 → 调研+写作 → 配图+SEO"""
import asyncio, json, os, re, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "output" / "pipeline_batch"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FLUSH = sys.stdout.flush

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

def save_json(name, data):
    (OUT_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

async def do_quality(article):
    from agents.quality_agent import QualityAgent
    r = await QualityAgent().score_article({
        "title": article['title'],
        "content": strip_html(article.get('description', '')),
        "source_url": article.get('original_url', ''),
    })
    qs = r.get('quality_score', 0)
    return round(float(qs), 1) if isinstance(qs, (int, float)) else 0.0

async def do_rw(article):
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    t = article['title']
    topic = {"title": t, "primary_keyword": t[:10], "original_url": article.get('original_url',''),
             "source_content": strip_html(article.get('description','')), "content_type": "news"}
    ra = ResearchAgent()
    res = await ra.execute(topic=topic, mode="mock")
    outline = (res or {}).get("outline") or (res or {}).get("detailed_outline")
    wa = WriterAgent()
    write = await wa.execute(topic=topic, outline=outline, materials=res or {}, dry_run=True)
    ct = strip_html(article.get('description',''))
    tt = article['title']
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
        tt = art.get('title') or tt
    return tt, ct

async def do_is(article, content, title):
    from agents.seo_agent import SEOAgent
    result = {}
    try:
        s = await SEOAgent().execute(article={"title":title,"content_md":content,"meta_description":"","slug":""},
                                       topic=article, page_info={"slug":"","category":"news"}, dry_run=True)
        result['seo'] = s if isinstance(s, dict) else {"raw": str(s)}
    except Exception as e: result['seo'] = {"error": str(e)}
    try:
        from agents.image_agent.image_agent import ImageAgent
        img = await ImageAgent().generate_featured_image(prompt=f"封面图: {title}\n{content[:800]}", visual_style="professional")
        result['image'] = img if isinstance(img, dict) else {"raw": str(img)}
    except Exception as e: result['image'] = {"error": str(e)}
    return result

async def main():
    scoring = json.loads((ROOT / "output/pipeline_batch/01_ai_scoring.json").read_text('utf-8'))
    above_75 = scoring.get('above_75', scoring.get('top20', []))
    articles_all = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    amap = {a['id']: a for a in articles_all}
    
    print(f"📋 Phase 2-4: 质量 → 调研+写作 → 配图+SEO ({len(above_75)} 篇)")
    
    final = []
    for idx, se in enumerate(above_75):
        aid = se.get('article_id')
        a = amap.get(aid, {})
        title = a.get('title', '')
        print(f"\n[{idx+1}/{len(above_75)}] id={aid} score={se['overall_score']:.0f} | {title[:55]}"); FLUSH()
        
        qs = await do_quality(a)
        print(f"  质量: {qs:.1f}"); FLUSH()
        
        best_qs, best_ct, best_tt, rw_cnt = qs, strip_html(a.get('description','')), title, 0
        
        if qs <= 70:
            print(f"  🔄 重写..."); FLUSH()
            for attempt in range(2):
                print(f"    第{attempt+1}轮..."); FLUSH()
                try:
                    nt, nc = await do_rw(a)
                    q2 = await do_quality({**a, 'description': nc, 'title': nt})
                    print(f"    质量: {q2:.1f}"); FLUSH()
                    if q2 > best_qs: best_qs, best_ct, best_tt = q2, nc, nt
                    rw_cnt += 1
                    if q2 >= 85: print(f"    ✅ >=85!"); FLUSH(); break
                except Exception as e:
                    print(f"    ❌ {e}"); FLUSH(); traceback.print_exc()
            print(f"  最终: {best_qs:.1f} (重写{rw_cnt}次)"); FLUSH()
        
        print(f"  🎨 配图+SEO..."); FLUSH()
        is_res = await do_is(a, best_ct, best_tt)
        
        final.append({"id": aid, "title": best_tt, "ai_score": se['overall_score'],
                       "quality": best_qs, "rewrites": rw_cnt, "img_seo": is_res})
        save_json("03_final_results.json", final)
        time.sleep(1)
    
    print(f"\n✅ 完成 {len(final)} 篇 → {OUT_DIR}/03_final_results.json")

asyncio.run(main())
