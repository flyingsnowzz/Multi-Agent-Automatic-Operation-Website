#!/usr/bin/env python3
"""
数据收集工具 - ResearchAgent
从多个来源收集相关资料和数据
"""

import os
import json
import httpx
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import asyncio
from urllib.parse import quote
import xml.etree.ElementTree as ET

# 可选依赖检查
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from googlesearch import search as google_search
    HAS_GOOGLESEARCH = True
except ImportError:
    HAS_GOOGLESEARCH = False


class DataCollector:
    """多来源数据收集工具"""
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self.http_client.aclose()

    async def __aenter__(self) -> "DataCollector":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _canonicalize_source(self, source: str) -> Tuple[str, str]:
        s = (source or "").strip().lower()
        mapping = {
            "official_statistics": "official",
            "industry_reports": "industry",
            "academic_papers": "academic",
            "news_articles": "news",
            "expert_opinions": "expert",
            "official": "official",
            "industry": "industry",
            "academic": "academic",
            "news": "news",
            "expert": "expert",
        }
        if s in mapping:
            return s, mapping[s]
        return s, ""
    
    async def collect(
        self,
        topic: str,
        keywords: List[str],
        sources: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        从多个来源收集数据
        
        Args:
            topic: 主题
            keywords: 关键词列表
            sources: 数据源列表 ['official', 'academic', 'news', 'expert']
            
        Returns:
            收集的数据
        """
        if sources is None:
            sources = ["official_statistics", "industry_reports", "academic_papers", "news_articles", "expert_opinions"]
        
        results = {
            'topic': topic,
            'keywords': keywords,
            'collected_at': datetime.now().isoformat(),
            'data': {},
            'warnings': []
        }
        
        task_map: List[Tuple[str, Any]] = []
        for raw in sources:
            orig, canon = self._canonicalize_source(raw)
            if not canon:
                results["data"][orig] = {"success": False, "error": "unknown_source", "items": []}
                results["warnings"].append(f"unknown_source:{orig}")
                continue
            if canon == "official":
                task_map.append((orig, self._collect_official_data(topic, keywords)))
            elif canon == "industry":
                task_map.append((orig, self._collect_industry_reports(topic, keywords)))
            elif canon == "academic":
                task_map.append((orig, self._collect_academic_data(topic, keywords)))
            elif canon == "news":
                task_map.append((orig, self._collect_news_data(topic, keywords)))
            elif canon == "expert":
                task_map.append((orig, self._collect_expert_opinions(topic, keywords)))
            else:
                results["data"][orig] = {"success": False, "error": "unknown_source", "items": []}
                results["warnings"].append(f"unknown_source:{orig}")

        if task_map:
            collected = await asyncio.gather(*[t for _, t in task_map], return_exceptions=True)
            for (orig, _), item in zip(task_map, collected):
                if isinstance(item, Exception):
                    results["data"][orig] = {"success": False, "error": str(item), "items": []}
                elif isinstance(item, dict):
                    results["data"][orig] = item
                else:
                    results["data"][orig] = {"success": False, "error": "collector_return_type_invalid", "items": []}
        return results
    
    async def _collect_official_data(self, topic: str, keywords: List[str]) -> Dict:
        """收集官方统计数据"""
        return {
            'success': True,
            'type': 'official',
            'items': [],
            'sources': [],
            'warning': 'official_source_not_configured'
        }

    async def _collect_industry_reports(self, topic: str, keywords: List[str]) -> Dict:
        out = await self._collect_news_rss(topic=topic, keywords=keywords, extra_query="report OR 白皮书 OR 行业报告")
        out["type"] = "industry"
        return out
    
    async def _collect_academic_data(self, topic: str, keywords: List[str]) -> Dict:
        """收集学术论文"""
        return {
            'success': True,
            'type': 'academic',
            'items': [],
            'sources': [],
            'warning': 'academic_source_not_configured'
        }
    
    async def _collect_news_data(self, topic: str, keywords: List[str]) -> Dict:
        """收集新闻报道"""
        return await self._collect_news_rss(topic=topic, keywords=keywords, extra_query="")

    async def _collect_news_rss(self, *, topic: str, keywords: List[str], extra_query: str) -> Dict[str, Any]:
        query_parts = []
        if topic:
            query_parts.append(topic)
        for k in (keywords or [])[:3]:
            if k and k.strip():
                query_parts.append(k.strip())
        if extra_query:
            query_parts.append(extra_query)
        query = " ".join(query_parts).strip() or topic or "news"
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        try:
            resp = await self.http_client.get(url)
            resp.raise_for_status()
            items = []
            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is not None:
                for it in channel.findall("item")[:10]:
                    title = (it.findtext("title") or "").strip()
                    link = (it.findtext("link") or "").strip()
                    published = (it.findtext("pubDate") or "").strip()
                    items.append(
                        {
                            "title": title,
                            "url": link,
                            "published": published,
                            "source_type": "news_rss",
                            "authority_score": "medium",
                            "extracted_at": datetime.now().isoformat(),
                        }
                    )
            return {"success": True, "type": "news", "items": items, "sources": [{"url": url, "type": "rss"}]}
        except Exception as e:
            return {"success": False, "type": "news", "items": [], "sources": [{"url": url, "type": "rss"}], "error": str(e)}
    
    async def _collect_expert_opinions(self, topic: str, keywords: List[str]) -> Dict:
        """收集专家观点"""
        return {
            'success': True,
            'type': 'expert',
            'items': [],
            'sources': [],
            'warning': 'expert_source_not_configured'
        }
    
    async def scrape_url(self, url: str) -> Dict[str, Any]:
        """
        抓取网页内容
        
        Args:
            url: 网页URL
            
        Returns:
            抓取的内容
        """
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            
            if HAS_BS4:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 提取正文
                for script in soup(["script", "style"]):
                    script.decompose()
                
                text = soup.get_text(separator='\n', strip=True)
                
                return {
                    'url': url,
                    'title': soup.title.string if soup.title else '',
                    'content': text[:5000],  # 限制长度
                    'status': 'success'
                }
            else:
                return {
                    'url': url,
                    'content': response.text[:5000],
                    'status': 'success',
                    'note': 'BeautifulSoup未安装，无法解析HTML'
                }
                
        except Exception as e:
            return {
                'url': url,
                'error': str(e),
                'status': 'error'
            }
    
    def validate_source(self, source: Dict) -> Dict[str, Any]:
        """
        验证数据来源可靠性
        
        Args:
            source: 来源信息
            
        Returns:
            验证结果
        """
        score = 0
        issues = []
        
        # 检查URL
        if 'url' in source:
            score += 20
        else:
            issues.append('缺少来源URL')
        
        # 检查日期
        if 'date' in source or 'published' in source:
            score += 20
        else:
            issues.append('缺少发布日期')
        
        # 检查作者/机构
        if 'author' in source or 'organization' in source:
            score += 30
        else:
            issues.append('缺少作者/机构信息')
        
        # 检查数据类型
        if source.get('type') in ['official', 'academic']:
            score += 30
        
        return {
            'score': min(score, 100),
            'issues': issues,
            'reliable': score >= 60
        }


# CrewAI Tool 包装
def get_data_collector_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("data_collector")
    def data_collector_tool(
        topic: str,
        keywords: str,
        sources: str = "official_statistics,industry_reports,academic_papers,news_articles,expert_opinions",
    ) -> str:
        """
        从多个来源收集相关资料和数据。
        
        Args:
            topic: 要研究的主题
            keywords: 关键词列表，用逗号分隔
            sources: 数据源列表，用逗号分隔，可选：official, academic, news, expert
            
        Returns:
            JSON格式的收集结果
        """
        import json
        collector = DataCollector()
        keywords_list = [k.strip() for k in keywords.split(',')]
        sources_list = [s.strip() for s in sources.split(',')]

        async def _run() -> Dict[str, Any]:
            try:
                return await collector.collect(topic, keywords_list, sources_list)
            finally:
                await collector.close()

        try:
            asyncio.get_running_loop()
            return json.dumps({"success": False, "error": "async_context_not_supported"}, ensure_ascii=False, indent=2)
        except RuntimeError:
            result = asyncio.run(_run())
            return json.dumps(result, ensure_ascii=False, indent=2)
    
    return data_collector_tool


if __name__ == "__main__":
    # 测试
    async def test():
        collector = DataCollector()
        result = await collector.collect(
            topic="EMBA项目选择",
            keywords=["EMBA", "商学院", "高管教育"],
            sources=["news", "expert"]
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    asyncio.run(test())
