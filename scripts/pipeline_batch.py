#!/usr/bin/env python3
"""
批量打分 → 质量 → 调研 → 写作 → 配图 → SEO Pipeline
流程:
  1. 解析 crawler_data_test.sql 提取文章
  2. publish_date 改为今天
  3. 打分(content_evaluator) → 筛选>75 → 攒够20篇
  4. 质量Agent → >70 通过, <=70 进调研+写作
  5. 质量复评 → >85 通过, 最多重写2次取最好
  6. 配图 + SEO
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, date
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
load_dotenv(".env.local", override=True)

TODAY = date.today().isoformat()  # '2026-06-25'
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "pipeline_batch")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# Phase 1: Parse SQL dump
# ============================================================

def strip_html(text: str) -> str:
    """Remove HTML tags, decode entities, normalize whitespace."""
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_main_table(sql_path: str) -> List[Dict[str, Any]]:
    """Parse crawler_news_main INSERT statements."""
    with open(sql_path, 'r', encoding='utf-8') as f:
        content = f.read()

    articles = []
    # Find the INSERT INTO `crawler_news_main` block
    pattern = re.compile(
        r"INSERT INTO `crawler_news_main` VALUES\s*(.+?);",
        re.DOTALL
    )
    
    for match in pattern.finditer(content):
        values_text = match.group(1)
        # Parse each row: (...),(...)
        rows = re.findall(r'\(([^()]*(?:\([^()]*\)[^()]*)*)\)', values_text)
        
        for row in rows:
            # Simple CSV parser for the row
            fields = _parse_csv_row(row)
            if len(fields) >= 25:  # minimum fields
                try:
                    article = {
                        "id": int(fields[0].strip()),
                        "repo_kind": int(fields[1].strip()) if fields[1].strip() else 1,
                        "college_id": int(fields[2].strip()) if fields[2].strip() else 0,
                        "college_name": fields[3].strip().strip("'"),
                        "specialty_id": fields[4].strip() if fields[4].strip() != 'NULL' else None,
                        "specialty_name": fields[5].strip().strip("'") if fields[5].strip() != 'NULL' else None,
                        "category": fields[6].strip() if fields[6].strip() != 'NULL' else None,
                        "title": fields[7].strip().strip("'"),
                        "author": fields[8].strip().strip("'") if fields[8].strip() != 'NULL' else None,
                        "keywords": fields[9].strip().strip("'") if fields[9].strip() != 'NULL' else None,
                        "description": fields[10].strip().strip("'") if len(fields) > 10 else "",
                        "views": int(fields[11].strip()) if fields[11].strip() and fields[11].strip() != 'NULL' else 0,
                        "original_url": fields[12].strip().strip("'") if len(fields) > 12 and fields[12].strip() != 'NULL' else None,
                        "publish_date": TODAY,  # <-- CHANGED TO TODAY
                        "score_detail": _try_parse_json(fields[21] if len(fields) > 21 else '{}'),
                    }
                    articles.append(article)
                except (ValueError, IndexError) as e:
                    continue
    
    return articles


def _try_parse_json(text: str) -> Dict:
    try:
        s = text.strip().strip("'")
        return json.loads(s)
    except:
        return {}


def _parse_csv_row(row: str) -> List[str]:
    """Simple CSV parser handling quoted strings and NULL."""
    fields = []
    current = []
    in_quotes = False
    quote_char = None
    
    for char in row:
        if in_quotes:
            if char == quote_char:
                # Check for escaped quote
                current.append(char)
                in_quotes = False
            elif char == '\\':
                current.append(char)
            else:
                current.append(char)
        elif char in ("'", '"'):
            in_quotes = True
            quote_char = char
            current.append(char)
        elif char == ',':
            fields.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    
    if current:
        fields.append(''.join(current).strip())
    
    return fields


def parse_content_tables(sql_path: str) -> Dict[int, str]:
    """Parse crawler_news_0~4 content tables. Returns {news_id: content_html}."""
    with open(sql_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content_map = {}
    pattern = re.compile(
        r"INSERT INTO `crawler_news_\d+` VALUES\s*(.+?);",
        re.DOTALL
    )
    
    for match in pattern.finditer(content):
        values_text = match.group(1)
        rows = re.findall(r'\(([^()]*(?:\([^()]*\)[^()]*)*)\)', values_text)
        
        for row in rows:
            fields = _parse_csv_row(row)
            if len(fields) >= 3:
                try:
                    # First field: id (auto-increment), second: news_id, third: content
                    idx_id = 0
                    news_id = int(fields[idx_id + 1].strip())
                    html_content = fields[idx_id + 2].strip().strip("'")
                    if html_content and html_content != 'NULL':
                        content_map[news_id] = html_content
                except (ValueError, IndexError):
                    continue
    
    return content_map


# ============================================================
# Phase 2: Scoring (content_evaluator)
# ============================================================

async def score_articles(articles: List[Dict], target_count: int = 20) -> List[Dict]:
    """Score all articles, return those with score > 75, stop after target_count."""
    from agents.crawler_processor_agent.tools.content_evaluator import ContentEvaluator
    
    evaluator = ContentEvaluator()
    scored = []
    total = len(articles)
    
    print(f"\n{'='*60}")
    print(f"📊 打分阶段: 共 {total} 篇文章")
    print(f"{'='*60}")
    
    for i, article in enumerate(articles):
        title = article.get("title", "")
        desc = article.get("description", "")
        url = article.get("original_url", "")
        full_text = f"{title}\n{desc}"
        
        result = await evaluator.evaluate(
            title=title,
            content=full_text,
            source_url=url,
            target_keywords=[]
        )
        
        if result.get("success"):
            qs = result["quality_score"] * 100
            rs = result["relevance_score"] * 100
            sp = result["seo_potential_score"] * 100
            # Composite score: weighted average
            composite = qs * 0.4 + rs * 0.3 + sp * 0.3
            
            article["eval_result"] = {
                "quality": round(qs, 1),
                "relevance": round(rs, 1),
                "seo": round(sp, 1),
                "composite": round(composite, 1),
                "word_count": result.get("word_count", 0),
            }
            
            if composite > 75:
                scored.append(article)
                print(f"  ✅ [{len(scored)}/{target_count}] id={article['id']} score={composite:.1f} | {title[:50]}...")
            else:
                if i < 5 or i % 50 == 0:
                    print(f"  ❌ id={article['id']} score={composite:.1f} (discard)")
        else:
            print(f"  ⚠️ id={article['id']} eval failed: {result.get('error')}")
        
        if len(scored) >= target_count:
            print(f"\n🎯 已攒够 {target_count} 篇 >75 分文章，停止打分。")
            break
    
    # Save all scoring results
    save_json(os.path.join(OUTPUT_DIR, "01_scoring_results.json"), {
        "total": total,
        "scored_above_75": len(scored),
        "articles": [{**a, "description": a.get("description", "")[:200]} for a in scored]
    })
    
    return scored


# ============================================================
# Phase 3: Quality check
# ============================================================

async def check_quality(article: Dict) -> Dict:
    """Run QualityAgent on an article. Returns quality result dict."""
    from agents.quality_agent import QualityAgent
    
    title = article.get("title", "")
    desc = article.get("description", "")
    
    agent = QualityAgent()
    
    result = await agent.score_article({
        "title": title,
        "content": desc,  # description as content for quality check
        "source_url": article.get("original_url", ""),
    })
    
    qs = result.get("quality_score", 0)
    if isinstance(qs, (int, float)):
        if qs <= 1:
            qs *= 100
        qs = round(float(qs), 1)
    
    return {
        "quality_score": qs,
        "dimensions": result.get("dimensions", {}),
        "if_ai_generated": result.get("if_ai_generated", False),
        "reasons": result.get("reasons", []),
        "suggestions": result.get("suggestions", []),
    }


# ============================================================
# Phase 4: Research + Write
# ============================================================

async def research_and_write(article: Dict, quality_feedback: str = "") -> Tuple[Dict, Dict]:
    """Research → Write, returns (research_result, write_result)."""
    from agents.research_agent import ResearchAgent
    from agents.writer_agent import WriterAgent
    
    title = article.get("title", "")
    desc = article.get("description", "")
    url = article.get("original_url", "")
    keywords = article.get("keywords", "")
    
    # Build topic for research
    topic = {
        "title": title,
        "primary_keyword": keywords or title[:10],
        "original_url": url,
        "source_content": desc,
        "content_type": "news",
        "quality_feedback": quality_feedback,
    }
    
    # Research
    print(f"    🔍 调研中...")
    research_agent = ResearchAgent()
    research_result = await research_agent.execute(topic=topic, mode="mock")
    
    outline = None
    materials = {}
    if isinstance(research_result, dict):
        outline = research_result.get("outline") or research_result.get("detailed_outline")
        materials = research_result
    
    # Write
    print(f"    ✍️ 写作中...")
    writer_agent = WriterAgent()
    write_result = await writer_agent.execute(
        topic=topic,
        outline=outline if isinstance(outline, dict) else None,
        materials=materials,
        brand_config={"brand_guide": "config/brand_guidelines.yaml"},
        dry_run=True,
    )
    
    return research_result, write_result


# ============================================================
# Phase 5: Image + SEO
# ============================================================

async def image_and_seo(article: Dict, final_content: str, final_title: str) -> Dict:
    """Run ImageAgent + SEOAgent on the final article."""
    from agents.seo_agent import SEOAgent
    from agents.image_agent.image_agent import ImageAgent
    
    results = {}
    
    # SEO
    print(f"    🔍 SEO优化中...")
    try:
        seo_agent = SEOAgent()
        seo_result = await seo_agent.execute(
            article={
                "title": final_title or article.get("title", ""),
                "content_md": final_content or article.get("description", ""),
                "meta_description": "",
                "slug": "",
            },
            topic=article,
            page_info={"slug": "", "category": "news"},
            dry_run=True,
        )
        results["seo"] = seo_result if isinstance(seo_result, dict) else {"raw": str(seo_result)}
    except Exception as e:
        results["seo"] = {"error": str(e)}
    
    # Image (plan_only mode)
    print(f"    🎨 配图方案生成中...")
    try:
        image_agent = ImageAgent()
        prompt_text = final_content[:1500] if final_content else article.get("description", "")[:1500]
        image_result = await image_agent.generate_featured_image(
            prompt=f"为文章生成封面图: {final_title}\n{prompt_text}",
            visual_style="professional",
        )
        results["image"] = image_result if isinstance(image_result, dict) else {"raw": str(image_result)}
    except Exception as e:
        results["image"] = {"error": str(e)}
    
    return results


# ============================================================
# Main Pipeline
# ============================================================

async def run_pipeline():
    sql_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "crawler_data_test.sql"
    )
    
    print("=" * 60)
    print("🚀 多Agent自动运营 Pipeline 启动")
    print(f"📅 日期统一改为: {TODAY}")
    print("=" * 60)
    
    # --- Step 1: Parse SQL ---
    print("\n📦 解析 SQL dump...")
    articles = parse_main_table(sql_path)
    print(f"   提取到 {len(articles)} 篇文章 (crawler_news_main)")
    
    # --- Step 2: Score ---
    scored = await score_articles(articles, target_count=20)
    
    if len(scored) < 20:
        print(f"\n⚠️ 只有 {len(scored)} 篇 >75 分，不足20篇。可能需要调整评分阈值。")
    
    # --- Step 3+: Quality → Research+Write → Image+SEO ---
    print(f"\n{'='*60}")
    print(f"📋 质量评估 + 后续流程 (共 {len(scored)} 篇)")
    print(f"{'='*60}")
    
    final_results = []
    
    for idx, article in enumerate(scored):
        print(f"\n--- [{idx+1}/{len(scored)}] id={article['id']}: {article['title'][:60]} ---")
        print(f"   评分: composite={article['eval_result']['composite']}")
        
        # Quality check
        quality = await check_quality(article)
        qs = quality["quality_score"]
        print(f"   📊 质量分: {qs}")
        
        if qs > 70:
            print(f"   ✅ 质量>70，直接通过 → 配图+SEO")
            final_content = article.get("description", "")
            final_title = article.get("title", "")
            rewrite_attempts = 0
        else:
            print(f"   🔄 质量<=70，进入调研+写作...")
            best_quality = qs
            best_content = article.get("description", "")
            best_title = article.get("title", "")
            best_result = None
            
            for attempt in range(2):
                print(f"   📝 第{attempt+1}轮重写...")
                try:
                    research_r, write_r = await research_and_write(
                        article,
                        quality_feedback=quality.get("suggestions", [])
                    )
                    
                    # Extract rewritten content
                    if isinstance(write_r, dict):
                        art = write_r.get("article") or {}
                        if isinstance(art, dict):
                            new_content = art.get("content_md") or art.get("content") or ""
                            new_title = art.get("title") or article.get("title", "")
                        else:
                            new_content = str(write_r)
                            new_title = article.get("title", "")
                    else:
                        new_content = str(write_r)
                        new_title = article.get("title", "")
                    
                    # Re-run quality
                    rewrite_article = {**article, "description": new_content, "title": new_title}
                    quality2 = await check_quality(rewrite_article)
                    qs2 = quality2["quality_score"]
                    print(f"   第{attempt+1}轮质量分: {qs2}")
                    
                    if qs2 > best_quality:
                        best_quality = qs2
                        best_content = new_content
                        best_title = new_title
                        best_result = {"research": research_r, "write": write_r, "quality": quality2}
                    
                    if qs2 >= 85:
                        print(f"   ✅ 质量>=85，通过！")
                        break
                    else:
                        print(f"   ⚠️ 质量={qs2}，未达85...")
                        
                except Exception as e:
                    print(f"   ❌ 第{attempt+1}轮重写出错: {e}")
                    continue
            
            final_content = best_content
            final_title = best_title
            rewrite_attempts = 2 if best_quality < 85 else 1
            quality = best_result["quality"] if best_result else quality
            
            print(f"   📊 最终质量分: {best_quality}")
        
        # Image + SEO
        try:
            img_seo = await image_and_seo(article, final_content, final_title)
        except Exception as e:
            img_seo = {"error": str(e)}
            print(f"   ❌ 配图/SEO出错: {e}")
        
        final_results.append({
            "article_id": article["id"],
            "title": final_title,
            "original_title": article.get("title", ""),
            "eval_composite": article["eval_result"]["composite"],
            "quality_score": quality.get("quality_score", qs),
            "rewrite_attempts": rewrite_attempts,
            "final_quality": best_quality if 'best_quality' in dir() else qs,
            "image_seo": img_seo,
        })
        
        # Save incrementally
        save_json(os.path.join(OUTPUT_DIR, "02_pipeline_results.json"), final_results)
        
        # Rate limit
        time.sleep(2)
    
    print(f"\n{'='*60}")
    print(f"✅ Pipeline 完成！共处理 {len(final_results)} 篇文章")
    print(f"📁 结果保存: {OUTPUT_DIR}")
    print(f"{'='*60}")


def save_json(path: str, data: Any):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(run_pipeline())
