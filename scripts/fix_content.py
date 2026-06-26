#!/usr/bin/env python3
"""Fix: add content field to 02_final.json + regenerate rewritten article content."""
import asyncio, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

async def regenerate(title, article):
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    t = title
    topic = {"title": t, "primary_keyword": t[:10], "original_url": article.get('original_url',''),
             "source_content": strip_html(article.get('description','')), "content_type": "news"}
    ra = ResearchAgent(); res = await ra.execute(topic=topic, mode="mock")
    outline = (res or {}).get("outline") or (res or {}).get("detailed_outline")
    wa = WriterAgent(); write = await wa.execute(topic=topic, outline=outline, materials=res or {}, dry_run=True)
    ct, tt = strip_html(article.get('description','')), title
    if isinstance(write, dict):
        art = write.get('article') or {}
        ct = art.get('content_md') or art.get('content') or ct
        tt = art.get('title') or tt
    return tt, ct

async def main():
    final = json.loads((ROOT / "output/pipeline_batch/02_final.json").read_text('utf-8'))
    articles_all = json.loads((ROOT / "output/pipeline_batch/articles.json").read_text('utf-8'))
    amap = {a['id']: a for a in articles_all}
    
    for i, r in enumerate(final):
        if 'content' in r and r['content']:
            continue
        a = amap.get(r['id'], {})
        if r['rewrites'] > 0:
            print(f"[{i+1}/30] id={r['id']} 重写稿重现中...")
            tt, ct = await regenerate(r['title'], a)
            r['content'] = ct
            if tt != r['title']:
                r['title'] = tt
        else:
            r['content'] = strip_html(a.get('description', ''))
            print(f"[{i+1}/30] id={r['id']} 直接通过, 取原文")
    
    (ROOT / "output/pipeline_batch/02_final.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
    
    # Regenerate Markdown
    art_dir = ROOT / "output/pipeline_batch/articles"
    art_dir.mkdir(exist_ok=True)
    for r in final:
        seo = (r.get('img_seo', {}).get('seo', {}) if isinstance(r.get('img_seo'), dict) else {})
        img = (r.get('img_seo', {}).get('image', {}) if isinstance(r.get('img_seo'), dict) else {})
        mt = seo.get('meta_title', '') if isinstance(seo, dict) else ''
        md = seo.get('meta_description', '') if isinstance(seo, dict) else ''
        
        md_text = f"# {r['title']}\n\n"
        md_text += f"> AI评分: {r['ai_score']:.0f} | 质量分: {r['quality']:.1f} | 重写: {r['rewrites']}次\n\n"
        md_text += f"## SEO\n- Title: {mt}\n- Description: {md}\n\n"
        md_text += f"## 正文\n{r.get('content', '')}\n\n"
        if img.get('local'):
            md_text += f"![封面]({img['local']})\n"
        (art_dir / f"{r['id']:04d}.md").write_text(md_text, encoding='utf-8')
    
    print(f"\n✅ {len(final)} 篇全部补完 → 02_final.json + articles/")

asyncio.run(main())
