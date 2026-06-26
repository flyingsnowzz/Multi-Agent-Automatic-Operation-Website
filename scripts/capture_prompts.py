#!/usr/bin/env python3
"""Capture actual Research + Writer prompts for all 20 articles."""
import asyncio, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

# ---- LLM Wrapper that captures prompts ----
class PromptCaptureWrapper:
    def __init__(self, real_llm, capture_list, label=""):
        self._llm = real_llm
        self._capture = capture_list
        self._label = label
    def __getattr__(self, name):
        return getattr(self._llm, name)
    async def ainvoke(self, messages, **kwargs):
        texts = []
        for m in messages:
            texts.append(f"[{m.type}]: {m.content}")
        self._capture.append({"label": self._label, "prompt": "\n\n".join(texts)})
        return await self._llm.ainvoke(messages, **kwargs)
    def invoke(self, messages, **kwargs):
        texts = []
        for m in messages:
            texts.append(f"[{m.type}]: {m.content}")
        self._capture.append({"label": self._label, "prompt": "\n\n".join(texts)})
        return self._llm.invoke(messages, **kwargs)

def strip_html(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()

async def main():
    with open('output/final_test/final_results.json') as f:
        data = json.load(f)
    with open('output/pipeline_batch/articles.json') as f:
        articles = json.load(f)
    amap = {a['id']: a for a in articles}
    
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    
    for r in data:
        a = amap.get(r['id'], {})
        title = r['title']; desc = strip_html(a.get('description',''))
        print(f"id={r['id']} capturing prompts...")
        
        capture = []
        
        # Research Agent with capture
        ra = ResearchAgent()
        if hasattr(ra, 'llm') and ra.llm:
            ra.llm = PromptCaptureWrapper(ra.llm, capture, "research")
        
        kw = title[:20]
        topic = {"title": title, "primary_keyword": kw, "secondary_keywords": [],
                 "source_content": desc, "content_type": "news", "search_intent": "informational",
                 "min_word_count": 800, "max_word_count": 1200, "target_word_count": 1000}
        
        try:
            res = await ra.execute(topic=topic, mode="live")
        except:
            res = {}
        
        outline = (res or {}).get("outline") or (res or {}).get("detailed_outline")
        materials = res if isinstance(res, dict) else {}
        if "research_brief" not in materials:
            materials["research_brief"] = {
                "source_snapshot": {"source_title": title, "source_summary": desc[:500]},
                "source_highlights": [desc[:200]], "key_facts": [{"fact": desc[:300]}],
                "rewrite_constraints": ["保持原文事实准确"], "risk_points": [],
                "suggested_sections": [], "writer_outline": outline if isinstance(outline, dict) else {"sections": []},
            }
        
        # Writer Agent with capture
        wa = WriterAgent()
        if hasattr(wa, 'llm') and wa.llm:
            wa.llm = PromptCaptureWrapper(wa.llm, capture, "writer")
        
        brand_config = {"tone": ["专业","权威","亲和"], "must_include": [], "prohibited_words": [], "recommended_words": []}
        try:
            await wa.execute(topic=topic, outline=outline, materials=materials, brand_config=brand_config, dry_run=True)
        except:
            pass
        
        # Save captured prompts
        r['actual_prompts'] = capture
        print(f"  captured {len(capture)} prompts")
    
    with open('output/final_test/final_results.json', 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Show sample
    r0 = data[0]
    prompts = r0.get('actual_prompts', [])
    if prompts:
        p0 = prompts[0]
        print(f"\n✅ 示例 ({p0['label']}):")
        print(p0['prompt'][:500])
    print(f"\n✅ 完成 {len(data)} 篇")

asyncio.run(main())
