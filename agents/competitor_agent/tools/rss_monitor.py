#!/usr/bin/env python3
"""
RSS监控工具 - CompetitorAgent
监控竞品RSS订阅源，发现新内容
"""

import os
import json
import httpx
import feedparser
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class FeedEntry:
    """RSS条目"""
    title: str
    link: str
    summary: str
    published: Optional[str]
    author: Optional[str]
    source: str


class RSSMonitor:
    """RSS监控工具"""
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.last_check = {}  # 存储每个订阅源的最后检查时间
    
    async def fetch_feed(
        self,
        url: str,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        获取RSS内容
        
        Args:
            url: RSS订阅源URL
            limit: 获取条目数量限制
            
        Returns:
            解析后的RSS数据
        """
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            
            # 解析RSS/Atom
            feed = feedparser.parse(response.text)
            
            entries = []
            for entry in feed.entries[:limit]:
                entries.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": self._clean_summary(entry.get("summary", "")),
                    "published": entry.get("published", ""),
                    "author": entry.get("author", ""),
                    "tags": [tag.term for tag in entry.get("tags", [])]
                })
            
            return {
                "success": True,
                "feed_title": feed.feed.get("title", ""),
                "feed_url": url,
                "entries": entries,
                "entry_count": len(entries),
                "last_build_date": feed.feed.get("updated", "")
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "feed_url": url,
                "entries": []
            }
    
    async def fetch_multiple(
        self,
        urls: List[str],
        limit_per_feed: int = 5
    ) -> Dict[str, Any]:
        """
        批量获取多个RSS源
        
        Args:
            urls: RSS源URL列表
            limit_per_feed: 每个源的条目限制
            
        Returns:
            汇总结果
        """
        results = []
        all_entries = []
        
        for url in urls:
            result = await self.fetch_feed(url, limit_per_feed)
            results.append(result)
            
            if result.get("success"):
                for entry in result.get("entries", []):
                    entry["source"] = result.get("feed_title", url)
                    all_entries.append(entry)
        
        # 按发布时间排序
        all_entries.sort(
            key=lambda x: x.get("published", ""),
            reverse=True
        )
        
        return {
            "success": True,
            "feeds_count": len(urls),
            "successful_feeds": sum(1 for r in results if r.get("success")),
            "failed_feeds": sum(1 for r in results if not r.get("success")),
            "total_entries": len(all_entries),
            "entries": all_entries[:50],  # 最多返回50条
            "feeds": results
        }
    
    async def check_updates(
        self,
        url: str,
        since: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        检查RSS更新
        
        Args:
            url: RSS源URL
            since: 检查更新时间之后的新内容
            
        Returns:
            新条目列表
        """
        if since:
            since_dt = self._parse_date(since)
        else:
            # 默认检查24小时内
            since_dt = datetime.now() - timedelta(days=1)
        
        result = await self.fetch_feed(url, limit=50)
        
        if not result.get("success"):
            return result
        
        # 过滤新条目
        new_entries = []
        for entry in result.get("entries", []):
            entry_dt = self._parse_date(entry.get("published", ""))
            if entry_dt and entry_dt > since_dt:
                new_entries.append(entry)
        
        return {
            "success": True,
            "feed_url": url,
            "feed_title": result.get("feed_title", ""),
            "new_entries_count": len(new_entries),
            "new_entries": new_entries,
            "check_time": datetime.now().isoformat(),
            "since": since or since_dt.isoformat()
        }
    
    def _clean_summary(self, summary: str, max_length: int = 200) -> str:
        """清理摘要"""
        import re
        
        # 移除HTML标签
        summary = re.sub(r'<[^>]+>', '', summary)
        
        # 移除多余空白
        summary = ' '.join(summary.split())
        
        # 截断
        if len(summary) > max_length:
            summary = summary[:max_length-3] + "..."
        
        return summary
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """解析日期"""
        if not date_str:
            return None
        
        from email.utils import parsedate_to_datetime
        
        try:
            return parsedate_to_datetime(date_str)
        except:
            # 尝试其他格式
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d"
            ]
            
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except:
                    continue
        
        return None
    
    async def discover_rss(self, website_url: str) -> List[str]:
        """
        从网站发现RSS订阅源
        
        Args:
            website_url: 网站URL
            
        Returns:
            发现的RSS源列表
        """
        common_paths = [
            "/feed",
            "/rss",
            "/rss.xml",
            "/feed.xml",
            "/atom.xml",
            "/blog/feed",
            "/news/feed"
        ]
        
        discovered = []
        
        parsed = urlparse(website_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        for path in common_paths:
            url = base_url + path
            try:
                response = await self.http_client.head(url, follow_redirects=True)
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    if "xml" in content_type or "rss" in content_type or "atom" in content_type:
                        discovered.append(url)
            except:
                pass
        
        return discovered
    
    def analyze_content_patterns(
        self,
        entries: List[Dict]
    ) -> Dict[str, Any]:
        """
        分析内容模式
        
        Args:
            entries: 内容条目列表
            
        Returns:
            分析结果
        """
        if not entries:
            return {"success": False, "error": "No entries to analyze"}
        
        # 提取关键词
        all_words = []
        for entry in entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = title + " " + summary
            all_words.extend(text.split())
        
        # 词频统计（简单实现）
        word_freq = {}
        for word in all_words:
            word = word.strip("，。！？、；：""''（）")
            if len(word) >= 2:
                word_freq[word] = word_freq.get(word, 0) + 1
        
        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:20]
        
        # 标题模式分析
        title_patterns = {
            "has_number": sum(1 for e in entries if any(c.isdigit() for c in e.get("title", ""))),
            "has_question": sum(1 for e in entries if "？" in e.get("title", "") or "?" in e.get("title", "")),
            "has_list": sum(1 for e in entries if any(w in e.get("title", "") for w in ["Top", "十大", "5个", "7个"])),
        }
        
        return {
            "success": True,
            "total_entries": len(entries),
            "top_keywords": [{"word": w, "count": c} for w, c in top_words],
            "title_patterns": title_patterns,
            "avg_title_length": sum(len(e.get("title", "")) for e in entries) / len(entries),
            "avg_summary_length": sum(len(e.get("summary", "")) for e in entries) / len(entries)
        }
    
    async def close(self):
        """关闭客户端"""
        await self.http_client.aclose()


# CrewAI Tool 包装
def get_rss_monitor_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("rss_monitor")
    def rss_monitor_tool(
        action: str,
        url: str = "",
        urls: str = "",
        since: str = "",
        limit: int = 10
    ) -> str:
        """
        监控竞品RSS订阅源，发现新内容。
        
        Args:
            action: 操作类型 fetch/fetch_multiple/check_updates/discover/analyze
            url: 单个RSS源URL
            urls: 多个RSS源URL，用换行分隔
            since: 检查更新时间之后的内容 YYYY-MM-DD格式
            limit: 获取条目数量
            
        Returns:
            JSON格式的监控结果
        """
        import asyncio
        
        monitor = RSSMonitor()
        
        async def run():
            if action == "fetch":
                return await monitor.fetch_feed(url, limit)
            
            elif action == "fetch_multiple":
                url_list = [u.strip() for u in urls.split("\n") if u.strip()]
                return await monitor.fetch_multiple(url_list, limit)
            
            elif action == "check_updates":
                return await monitor.check_updates(url, since or None)
            
            elif action == "discover":
                discovered = await monitor.discover_rss(url)
                return {
                    "success": True,
                    "website": url,
                    "discovered_feeds": discovered
                }
            
            elif action == "analyze":
                # 需要先获取内容再分析
                result = await monitor.fetch_feed(url, limit)
                if result.get("success"):
                    return monitor.analyze_content_patterns(result.get("entries", []))
                return result
            
            else:
                return {"success": False, "error": f"未知操作: {action}"}
        
        try:
            result = asyncio.run(run())
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            asyncio.run(monitor.close())
    
    return rss_monitor_tool


if __name__ == "__main__":
    # 测试
    import asyncio
    
    async def test():
        monitor = RSSMonitor()
        
        # 测试获取
        result = await monitor.fetch_feed(
            "https://example.com/feed",
            limit=5
        )
        
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        await monitor.close()
    
    asyncio.run(test())
