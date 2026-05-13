#!/usr/bin/env python3
"""
数据收集工具 - ResearchAgent
从多个来源收集相关资料和数据
"""

import os
import json
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime
import asyncio

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
            sources = ['official', 'academic', 'news', 'expert']
        
        results = {
            'topic': topic,
            'keywords': keywords,
            'collected_at': datetime.now().isoformat(),
            'data': {}
        }
        
        # 并行收集
        tasks = []
        
        if 'official' in sources:
            tasks.append(self._collect_official_data(topic, keywords))
        
        if 'academic' in sources:
            tasks.append(self._collect_academic_data(topic, keywords))
        
        if 'news' in sources:
            tasks.append(self._collect_news_data(topic, keywords))
        
        if 'expert' in sources:
            tasks.append(self._collect_expert_opinions(topic, keywords))
        
        # 执行所有任务
        collected = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 整理结果
        for i, source in enumerate(sources):
            if isinstance(collected[i], dict):
                results['data'][source] = collected[i]
            else:
                results['data'][source] = {'error': str(collected[i])}
        
        await self.http_client.aclose()
        return results
    
    async def _collect_official_data(self, topic: str, keywords: List[str]) -> Dict:
        """收集官方统计数据"""
        # 这里可以接入官方API，如国家统计局等
        return {
            'type': 'official',
            'data': [],
            'sources': [],
            'note': '需要配置官方API密钥'
        }
    
    async def _collect_academic_data(self, topic: str, keywords: List[str]) -> Dict:
        """收集学术论文"""
        # 可以接入Google Scholar, CNKI等
        return {
            'type': 'academic',
            'papers': [],
            'citations': [],
            'note': '需要配置学术数据库API'
        }
    
    async def _collect_news_data(self, topic: str, keywords: List[str]) -> Dict:
        """收集新闻报道"""
        articles = []
        
        # 使用Google Search API搜索新闻
        if HAS_GOOGLESEARCH:
            for keyword in keywords[:3]:
                try:
                    search_results = google_search(
                        f"{keyword} news",
                        num_results=5,
                        lang='zh'
                    )
                    articles.extend(search_results)
                except Exception as e:
                    pass
        
        return {
            'type': 'news',
            'articles': articles[:10],
            'count': len(articles)
        }
    
    async def _collect_expert_opinions(self, topic: str, keywords: List[str]) -> Dict:
        """收集专家观点"""
        return {
            'type': 'expert',
            'opinions': [],
            'sources': [],
            'note': '需要人工审核和补充'
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
    def data_collector_tool(topic: str, keywords: str, sources: str = "official,academic,news,expert") -> str:
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
        
        result = asyncio.run(collector.collect(topic, keywords_list, sources_list))
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
