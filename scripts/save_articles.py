#!/usr/bin/env python3
"""保存所有20篇文章（原文+重写稿）+ SEO + 图片"""
import asyncio, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

OUT = ROOT / "output" / "pipeline_batch" / "articles_generated"
OUT.mkdir(parents=True, exist_ok=True)

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

async def rewrite_article(article):
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
    ct, tt = strip_html(article.get('description','')), article['title']
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
        tt = art.get('title') or tt
    return tt, ct

async def main():
    final = json.loads((ROOT / "output/pipeline_batch/03_final_results.json").read_text('utf-8'))
    articles_all = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    amap = {a['id']: a for a in articles_all}
    
    for i, r in enumerate(final):
        aid = r['id']
        a = amap.get(aid, {})
        title = r['title']
        rewrites = r['rewrites']
        
        print(f"[{i+1}/20] id={aid} rewrites={rewrites}")
        
        if rewrites > 0:
            # Need to regenerate the rewritten article
            title, content = await rewrite_article(a)
        else:
            # Use original description
            content = strip_html(a.get('description', ''))
        
        # SEO data
        seo_data = r.get('img_seo', {}).get('seo', {})
        # Image data
        img_data = r.get('img_seo', {}).get('image', {})
        
        # Build output
        out = {
            "id": aid,
            "title": title,
            "original_title": a.get('title', ''),
            "ai_score": r['ai_score'],
            "quality_score": r['quality'],
            "rewrites": rewrites,
            "content": content,
            "word_count": len(content),
            "seo": {
                "meta_title": seo_data.get('meta_title', '') if isinstance(seo_data, dict) else '',
                "meta_description": seo_data.get('meta_description', '') if isinstance(seo_data, dict) else '',
                "schema_type": (seo_data.get('schema_json', {}).get('@type', '') if isinstance(seo_data, dict) else ''),
            },
            "image": {
                "provider": img_data.get('provider', ''),
                "local_path": img_data.get('local_path', ''),
                "url": img_data.get('url', ''),
            } if isinstance(img_data, dict) else {},
        }
        
        fname = f"{aid:04d}_{title[:30].replace('/','_').replace(' ','_')}.json"
        (OUT / fname).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        
        # Also save as markdown
        md = f"# {title}\n\n"
        md += f"> AI评分: {r['ai_score']:.0f} | 质量分: {r['quality']:.1f} | 重写: {rewrites}次\n\n"
        md += f"## SEO\n- Meta Title: {out['seo']['meta_title']}\n- Meta Description: {out['seo']['meta_description']}\n\n"
        md += f"## 正文\n\n{content}\n\n"
        if out['image'].get('local_path'):
            md += f"## 配图\n![封面]({out['image']['local_path']})\n"
        (OUT / f"{aid:04d}_{title[:30].replace('/','_').replace(' ','_')}.md").write_text(md, encoding='utf-8')
    
    print(f"\n✅ 已保存 {len(final)} 篇到 {OUT}")

asyncio.run(main())
