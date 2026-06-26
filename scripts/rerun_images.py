#!/usr/bin/env python3
"""用 Coze 重新生成 20 篇文章的配图"""
import asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from agents.image_agent.tools.coze_image_provider import CozeImageProvider

async def main():
    with open(ROOT / "output/pipeline_batch/03_final_results.json") as f:
        results = json.load(f)
    
    provider = CozeImageProvider()
    
    for i, r in enumerate(results):
        title = r.get('title', '')
        print(f"\n[{i+1}/20] {title[:50]}")
        
        result = await provider.generate(
            prompt=f"新闻配图，专业风格: {title}",
            n=1
        )
        
        if result.get('success'):
            imgs = result.get('images', [])
            if imgs:
                r['img_seo']['image'] = {
                    'provider': 'coze',
                    'url': imgs[0].get('url', ''),
                    'local_path': imgs[0].get('local_path', ''),
                }
                print(f"  ✅ {imgs[0].get('local_path', '')}")
            else:
                print(f"  ⚠️ 生成成功但无图片")
                r['img_seo']['image'] = {'provider': 'coze', 'error': 'no_image_in_result'}
        else:
            print(f"  ❌ {result.get('error','')}")
            r['img_seo']['image'] = {'provider': 'coze', 'error': result.get('error','')}
        
        # Save incrementally
        with open(ROOT / "output/pipeline_batch/03_final_results.json", 'w') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        time.sleep(2)  # rate limit
    
    print(f"\n✅ 全部完成")

asyncio.run(main())
