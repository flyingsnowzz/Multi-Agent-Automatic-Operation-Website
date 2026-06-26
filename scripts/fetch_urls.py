#!/usr/bin/env python3
"""Fetch full article content from original_url for short articles."""
import asyncio, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

from agents.crawler_processor_agent.tools.url_content_fetcher import URLContentFetcher

async def main():
    final = json.loads((ROOT / "output/pipeline_batch/02_final.json").read_text('utf-8'))
    articles = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    amap = {a['id']: a for a in articles}
    
    short = [(i, r) for i, r in enumerate(final) if len(r.get('content','')) < 800]
    print(f"📡 抓取 {len(short)} 篇短文章原文...\n")
    
    fetcher = URLContentFetcher(timeout=15, max_content_chars=10000)
    updated = 0
    
    for idx, (i, r) in enumerate(short):
        a = amap.get(r['id'], {})
        url = a.get('original_url', '')
        if not url:
            print(f"[{idx+1}/{len(short)}] id={r['id']} 无URL, 跳过")
            continue
        
        print(f"[{idx+1}/{len(short)}] id={r['id']} {r['title'][:45]}")
        try:
            result = await fetcher.fetch(url)
            if result.success and result.content and len(result.content) > 100:
                r['content'] = result.content
                updated += 1
                print(f"  ✅ {len(r['content'])}字 (from {url[:50]})")
            else:
                # Fallback: use existing description
                desc = a.get('description', '')
                if len(desc) > len(r['content']):
                    r['content'] = desc
                    print(f"  ⚠️ 抓取失败, 用description: {len(desc)}字")
                else:
                    print(f"  ⚠️ 抓取失败: {result.error or '无内容'}")
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
    s = sum(1 for w in wcs if w < 800)
    print(f"\n✅ 完成: 更新{updated}篇, <800字: {s}篇, >=800: {len(wcs)-s}篇")

asyncio.run(main())
