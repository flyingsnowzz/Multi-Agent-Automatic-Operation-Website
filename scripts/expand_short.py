#!/usr/bin/env python3
"""Expand short articles (<800 chars) with WriterAgent."""
import asyncio, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

async def rewrite(article):
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    t = article['title']
    topic = {"title": t, "primary_keyword": t[:10], "original_url": article.get('original_url',''),
             "source_content": article.get('description',''), "content_type": "news"}
    ra = ResearchAgent(); res = await ra.execute(topic=topic, mode="mock")
    outline = (res or {}).get("outline")
    wa = WriterAgent(); write = await wa.execute(topic=topic, outline=outline, materials=res or {}, dry_run=True)
    ct = article.get('description', '')
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
    return ct

async def main():
    final = json.loads((ROOT / "output/pipeline_batch/02_final.json").read_text('utf-8'))
    articles = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    amap = {a['id']: a for a in articles}
    
    short = [(i, r) for i, r in enumerate(final) if len(r.get('content','')) < 800 and r['rewrites'] == 0]
    print(f"找到 {len(short)} 篇短文章需要扩展\n")
    
    for idx, (i, r) in enumerate(short):
        a = amap.get(r['id'], {})
        print(f"[{idx+1}/{len(short)}] id={r['id']} {r['title'][:50]} ({len(r['content'])}字)")
        
        try:
            new_ct = await rewrite(a)
            wc = len(new_ct)
            if wc > len(r['content']):
                r['content'] = new_ct
                r['rewrites'] = 1
                print(f"  ✅ {len(r['content'])}字 → {wc}字 (+{wc - len(r['content'])})")
            else:
                print(f"  ⚠️ 扩展失败，保留原文")
        except Exception as e:
            print(f"  ❌ {e}")
    
    # Save
    (ROOT / "output/pipeline_batch/02_final.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
    
    # Regen Markdown
    art_dir = ROOT / "output/pipeline_batch/articles"
    for r in final:
        seo = (r.get('img_seo', {}) if isinstance(r.get('img_seo'), dict) else {})
        s = seo.get('seo', {}) if isinstance(seo, dict) else {}
        img = seo.get('image', {}) if isinstance(seo, dict) else {}
        mt = s.get('meta_title', '') if isinstance(s, dict) else ''
        md = s.get('meta_description', '') if isinstance(s, dict) else ''
        
        md_text = f"# {r['title']}\n\n"
        md_text += f"> AI评分: {r['ai_score']:.0f} | 质量分: {r['quality']:.1f} | 重写: {r['rewrites']}次\n\n"
        md_text += f"## SEO\n- Title: {mt}\n- Description: {md}\n\n"
        md_text += f"## 正文\n{r.get('content', '')}\n\n"
        if img.get('local'):
            md_text += f"![封面]({img['local']})\n"
        (art_dir / f"{r['id']:04d}.md").write_text(md_text, encoding='utf-8')
    
    wcs = [len(r.get('content','')) for r in final]
    short2 = sum(1 for w in wcs if w < 800)
    print(f"\n✅ 完成: <800字: {short2}篇, >=800: {len(wcs)-short2}篇")

asyncio.run(main())
