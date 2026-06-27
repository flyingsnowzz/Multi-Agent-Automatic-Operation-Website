#!/usr/bin/env python3
"""完整 Pipeline: 解析SQL → AI评分(30+) → 质量 → 调研+写作 → 配图+SEO"""

import asyncio, json, os, re, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

OUT = ROOT / "output" / "pipeline_batch"
OUT.mkdir(parents=True, exist_ok=True)

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

def save_json(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

# ===== Phase 0: Parse SQL =====
def parse_articles():
    with open(ROOT / 'crawler_data_test.sql', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    articles = []
    for line in lines:
        if not line.startswith("INSERT INTO `crawler_news_main` VALUES "):
            continue
        m = re.search(r'VALUES \((.+)\);$', line.strip())
        if not m: continue
        vals = m.group(1)
        fields = []; cur = []; depth = 0; in_s = False; esc = False
        for ch in vals:
            if esc: cur.append(ch); esc = False; continue
            if ch == '\\': cur.append(ch); esc = True; continue
            if ch == "'": in_s = not in_s; cur.append(ch); continue
            if ch == '{' and not in_s: depth += 1; cur.append(ch)
            elif ch == '}' and not in_s: depth -= 1; cur.append(ch)
            elif ch == ',' and not in_s and depth == 0: fields.append(''.join(cur).strip()); cur = []
            else: cur.append(ch)
        if cur: fields.append(''.join(cur).strip())
        if len(fields) < 23: continue
        
        def clean(v):
            v = v.strip()
            if v == 'NULL': return None
            if v.startswith("'") and v.endswith("'"): return v[1:-1]
            return v
        
        sd_raw = fields[22].strip("'")
        sd = {}
        try:
            r = sd_raw.replace('\\"','"').replace("\\'","'").replace('\\\\','\\')
            sd = json.loads(r)
        except: pass
        
        articles.append({
            "id": int(fields[0]), "title": clean(fields[7]),
            "description": clean(fields[10]), "original_url": clean(fields[12]),
            "publish_date": "2026-06-26", "total_score": sd.get("total_score", 0),
        })
    save_json("articles.json", articles)
    return articles

# ===== Phase 1: AI Scoring =====
def phase_scoring(articles, target=30):
    from agents.scoring_agent.scoring_summary import summarize_crawler_topics
    above = []
    scored_all = []
    batch = 30
    for offset in range(0, len(articles), batch):
        chunk = articles[offset:offset+batch]
        r = summarize_crawler_topics(chunk, use_ai=True, ai_concurrency=4)
        scores = [s for s in r.get("article_scores", []) if s.get("overall_score") is not None]
        scored_all.extend(scores)
        new = [s for s in scores if s.get("overall_score", 0) > 75]
        above.extend(new)
        print(f"  批{offset//batch+1}: {len(scores)}篇, +{len(new)}>75, 累计{len(above)}/{target}")
        if len(above) >= target:
            above = above[:target]
            break
    save_json("01_scoring.json", {"above": above, "all": scored_all})
    return above

# ===== Phase 2: Quality =====
async def do_quality(article):
    from agents.quality_agent import QualityAgent
    r = await QualityAgent().score_article({
        "title": article['title'], "content": strip_html(article.get('description','')),
        "source_url": article.get('original_url',''),
    })
    return round(float(r.get('quality_score',0)), 1)

# ===== Phase 3: Research + Write =====
async def do_rw(article):
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    t = article['title']
    topic = {"title": t, "primary_keyword": t[:10], "original_url": article.get('original_url',''),
             "source_content": strip_html(article.get('description','')), "content_type": "news"}
    ra = ResearchAgent(); res = await ra.execute(topic=topic, mode="mock")
    outline = (res or {}).get("outline") or (res or {}).get("detailed_outline")
    wa = WriterAgent(); write = await wa.execute(topic=topic, outline=outline, materials=res or {}, dry_run=True)
    ct, tt = strip_html(article.get('description','')), article['title']
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
        tt = art.get('title') or tt
    return tt, ct

# ===== Phase 4: Image + SEO =====
async def do_is(article, content, title):
    from agents.seo_agent import SEOAgent
    from agents.image_agent.tools.coze_image_provider import CozeImageProvider
    result = {}
    try:
        s = await SEOAgent(keyword_mode='v2').execute(article={"title":title,"content_md":content,"meta_description":"","slug":""},
                                       topic=article, page_info={"slug":"","category":"news"}, dry_run=True)
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
    print("=" * 60)
    print("🚀 Full Pipeline: 解析 → 评分(30+) → 质量 → 写作 → 配图+SEO")
    print("=" * 60)
    
    # Phase 0
    print("\n📦 解析 SQL...")
    articles = parse_articles()
    print(f"   {len(articles)} 篇")
    
    # Phase 1
    print("\n📊 Phase 1: AI 评分 (目标 30 篇 >75)...")
    above = phase_scoring(articles, target=30)
    print(f"   ✅ {len(above)} 篇 >75")
    for s in above[:5]:
        print(f"   [{s['overall_score']:.1f}] {s.get('title','')[:50]}")
    if len(above) > 5: print(f"   ... 共 {len(above)} 篇")
    
    amap = {a['id']: a for a in articles}
    
    # Phase 2-4
    print(f"\n📋 Phase 2-4: 质量 → 写作 → 配图+SEO ({len(above)} 篇)")
    final = []
    
    for idx, se in enumerate(above):
        aid = se.get('article_id')
        a = amap.get(aid, {})
        title = a.get('title','')
        print(f"\n[{idx+1}/{len(above)}] id={aid} score={se['overall_score']:.0f} | {title[:50]}"); sys.stdout.flush()
        
        qs = await do_quality(a)
        print(f"  质量: {qs:.1f}"); sys.stdout.flush()
        
        best_qs, best_ct, best_tt, rw = qs, strip_html(a.get('description','')), title, 0
        
        if qs <= 70 or len(best_ct) < 800:  # 字数不足也触发重写
            print(f"  🔄 重写..."); sys.stdout.flush()
            for attempt in range(2):
                print(f"    第{attempt+1}轮..."); sys.stdout.flush()
                try:
                    nt, nc = await do_rw(a)
                    q2 = await do_quality({**a, 'description': nc, 'title': nt})
                    print(f"    质量: {q2:.1f}"); sys.stdout.flush()
                    if q2 > best_qs: best_qs, best_ct, best_tt = q2, nc, nt
                    rw += 1
                    if q2 >= 85: print(f"    ✅ >=85!"); sys.stdout.flush(); break
                except Exception as e: print(f"    ❌ {e}"); traceback.print_exc()
            print(f"  最终: {best_qs:.1f} (重写{rw}次)"); sys.stdout.flush()
        
        print(f"  🎨 配图+SEO..."); sys.stdout.flush()
        is_res = await do_is(a, best_ct, best_tt)
        final.append({"id": aid, "title": best_tt, "ai_score": se['overall_score'], "quality": best_qs, "rewrites": rw, "img_seo": is_res})
        save_json("02_final.json", final)
        time.sleep(1)
    
    # Save markdown articles
    print(f"\n📝 保存文章...")
    art_dir = OUT / "articles"
    art_dir.mkdir(exist_ok=True)
    for r in final:
        img_info = r['img_seo'].get('image', {}) if isinstance(r['img_seo'], dict) else {}
        seo_info = r['img_seo'].get('seo', {}) if isinstance(r['img_seo'], dict) else {}
        mt = seo_info.get('meta_title','') if isinstance(seo_info, dict) else ''
        md = seo_info.get('meta_description','') if isinstance(seo_info, dict) else ''
        content = r.get('content', strip_html(amap.get(r['id'],{}).get('description','')))
        
        md_text = f"# {r['title']}\n\n"
        md_text += f"> AI评分: {r['ai_score']:.0f} | 质量分: {r['quality']:.1f} | 重写: {r['rewrites']}次\n\n"
        md_text += f"## SEO\n- Title: {mt}\n- Description: {md}\n\n"
        md_text += f"## 正文\n{content}\n\n"
        if img_info.get('local'):
            md_text += f"![封面]({img_info['local']})\n"
        (art_dir / f"{r['id']:04d}.md").write_text(md_text, encoding='utf-8')
    
    print(f"\n✅ 完成 {len(final)} 篇 → {OUT}")

asyncio.run(main())
