"""
选题Agent - 趋势检测工具
用于追踪行业热点和趋势话题
"""
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
import logging
import os
from urllib.parse import quote
import xml.etree.ElementTree as ET
import hashlib

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TrendItem:
    """趋势条目"""
    keyword: str
    trend_score: float  # 0-100的热度分数
    change_percent: float  # 相比上期的变化百分比
    category: str  # 趋势类别
    source: str  # 数据来源
    timestamp: datetime
    related_queries: List[str] = None


@dataclass
class TrendingTopic:
    """热门话题"""
    title: str
    keywords: List[str]
    trend_score: float
    start_date: Optional[datetime] = None
    peak_date: Optional[datetime] = None
    is_rising: bool = True
    related_news: List[str] = None
    content_opportunities: List[str] = None


class TrendDetectionTool:
    """趋势检测工具
    
    支持的数据源:
    - Google Trends
    - 百度指数
    - 微博热搜
    - 知乎热榜
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.mode = (self.config.get("mode") or os.environ.get("TOPIC_AGENT_MODE") or "mock").strip().lower()
        self.agent_config = self.config.get("config") if isinstance(self.config.get("config"), dict) else {}
        self.cache = {}
        self.cache_ttl = timedelta(hours=1)  # 缓存1小时
        self.http_client = httpx.AsyncClient(timeout=30.0)
    
    async def detect_trends(
        self,
        keywords: List[str],
        time_range: str = "7d"
    ) -> List[TrendItem]:
        """检测趋势
        
        Args:
            keywords: 关键词列表
            time_range: 时间范围 (today, 7d, 30d, 90d)
        
        Returns:
            List[TrendItem]: 趋势列表
        """
        logger.info(f"检测趋势: {keywords}, 时间范围: {time_range}")

        if self.mode != "live":
            now = datetime.now()
            out: List[TrendItem] = []
            for kw in keywords:
                q = (kw or "").strip()
                if not q:
                    continue
                h = self._hash_int(q)
                score = 20 + (h % 70)
                change = float((h % 120) - 20)
                out.append(
                    TrendItem(
                        keyword=q,
                        trend_score=float(min(max(score, 0), 100)),
                        change_percent=change,
                        category="mock",
                        source="mock",
                        timestamp=now,
                        related_queries=[],
                    )
                )
            out.sort(key=lambda x: x.trend_score, reverse=True)
            return out
        
        tasks = [
            self._get_google_trends(keywords, time_range),
            self._get_baidu_trends(keywords),
            self._get_social_trends(keywords),
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_trends = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"趋势获取失败: {result}")
                continue
            all_trends.extend(result)
        
        # 按热度排序
        all_trends.sort(key=lambda x: x.trend_score, reverse=True)
        
        # 去重
        seen = set()
        unique = []
        for t in all_trends:
            if t.keyword not in seen:
                seen.add(t.keyword)
                unique.append(t)
        
        return unique
    
    async def identify_trending_topics(
        self,
        industry: str,
        limit: int = 10
    ) -> List[TrendingTopic]:
        """识别热门话题
        
        Args:
            industry: 行业名称
            limit: 返回数量
        
        Returns:
            List[TrendingTopic]: 热门话题列表
        """
        logger.info(f"识别热门话题: {industry}")

        if self.mode != "live":
            base = (industry or "").strip() or "industry"
            now = datetime.now()
            topics: List[TrendingTopic] = []
            for i in range(max(1, limit)):
                title = f"{base} 热点话题 {i+1}"
                h = self._hash_int(f"{base}:{i}")
                score = 30 + (h % 60)
                topics.append(
                    TrendingTopic(
                        title=title,
                        keywords=[base],
                        trend_score=float(min(max(score, 0), 100)),
                        start_date=now - timedelta(days=7),
                        peak_date=now,
                        is_rising=True,
                        related_news=[],
                        content_opportunities=[],
                    )
                )
            topics.sort(key=lambda x: x.trend_score, reverse=True)
            return topics[:limit]
        
        # 并行获取多个来源的趋势
        tasks = [
            self._get_google_trending_searches(industry),
            self._get_social_trending_topics(industry),
            self._get_news_trends(industry),
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        topics = []
        for result in results:
            if isinstance(result, Exception):
                continue
            topics.extend(result)
        
        # 按热度排序并限制数量
        topics.sort(key=lambda x: x.trend_score, reverse=True)
        return topics[:limit]
    
    async def _get_google_trends(
        self,
        keywords: List[str],
        time_range: str
    ) -> List[TrendItem]:
        """获取Google Trends数据"""
        return await self._get_news_trend_items(keywords=keywords, time_range=time_range, source="news_rss")
    
    async def _get_baidu_trends(self, keywords: List[str]) -> List[TrendItem]:
        """获取百度指数数据"""
        return []
    
    async def _get_social_trends(self, keywords: List[str]) -> List[TrendItem]:
        """获取社交媒体趋势"""
        return []
    
    async def _get_google_trending_searches(self, industry: str) -> List[TrendingTopic]:
        """获取Google热门搜索"""
        topics = await self._get_news_topics(industry, source="news_rss")
        return topics
    
    async def _get_social_trending_topics(self, industry: str) -> List[TrendingTopic]:
        """获取社交媒体热门话题"""
        return []
    
    async def _get_news_trends(self, industry: str) -> List[TrendingTopic]:
        """获取新闻趋势"""
        return await self._get_news_topics(industry, source="news_rss")
    
    def calculate_trend_score(
        self,
        current_volume: int,
        previous_volume: int,
        search_velocity: float
    ) -> float:
        """计算趋势分数
        
        综合考虑：
        - 搜索量基数
        - 增长幅度
        - 搜索速度
        """
        # 基础分数
        base_score = min(current_volume / 1000 * 10, 50)  # 最高50分
        
        # 增长加分
        if previous_volume > 0:
            growth_rate = (current_volume - previous_volume) / previous_volume
            growth_score = min(growth_rate * 20, 30)  # 最高30分
        else:
            growth_score = 20  # 新关键词给基础分
        
        # 速度加分
        velocity_score = min(search_velocity * 20, 20)  # 最高20分
        
        score = base_score + max(growth_score, 0) + velocity_score
        return float(min(max(score, 0), 100))

    def _hash_int(self, text: str) -> int:
        return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)

    async def _get_news_topics(self, industry: str, source: str) -> List[TrendingTopic]:
        query = (industry or "").strip() or "news"
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        try:
            resp = await self.http_client.get(url)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                return []
            items = channel.findall("item")
            recent = items[:10]
            topics: List[TrendingTopic] = []
            score = min(len(items) * 5, 100)
            for it in recent[:5]:
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                topics.append(
                    TrendingTopic(
                        title=title,
                        keywords=[industry],
                        trend_score=float(score),
                        is_rising=True,
                        related_news=[(it.findtext("link") or "").strip()],
                        content_opportunities=[],
                    )
                )
            return topics
        except Exception as e:
            logger.error(f"新闻趋势获取失败: {e}")
            return []

    async def _get_news_trend_items(self, *, keywords: List[str], time_range: str, source: str) -> List[TrendItem]:
        window_days = 7
        s = (time_range or "").strip().lower()
        if s.endswith("d"):
            try:
                window_days = max(1, int(s[:-1]))
            except Exception:
                window_days = 7

        now = datetime.now()
        start_current = now - timedelta(days=window_days)
        start_prev = now - timedelta(days=window_days * 2)

        out: List[TrendItem] = []
        for kw in keywords:
            query = (kw or "").strip()
            if not query:
                continue
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            try:
                resp = await self.http_client.get(url)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                if channel is None:
                    continue
                items = channel.findall("item")
                current = 0
                prev = 0
                for it in items[:50]:
                    pub = (it.findtext("pubDate") or "").strip()
                    dt = self._parse_rfc822(pub)
                    if not dt:
                        continue
                    if dt >= start_current:
                        current += 1
                    elif dt >= start_prev:
                        prev += 1
                velocity = current / max(window_days, 1)
                score = self.calculate_trend_score(current_volume=current * 100, previous_volume=prev * 100, search_velocity=velocity / 10)
                change_percent = ((current - prev) / prev * 100) if prev > 0 else (100.0 if current > 0 else 0.0)
                out.append(
                    TrendItem(
                        keyword=query,
                        trend_score=score,
                        change_percent=change_percent,
                        category="news",
                        source=source,
                        timestamp=now,
                        related_queries=[],
                    )
                )
            except Exception as e:
                logger.error(f"趋势RSS失败: {query}: {e}")
                continue
        return out

    def _parse_rfc822(self, value: str) -> Optional[datetime]:
        s = (value or "").strip()
        if not s:
            return None
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
        return None

    async def close(self) -> None:
        await self.http_client.aclose()


def get_trend_detection_tool():
    from crewai.tools import tool
    import json
    import yaml
    from pathlib import Path
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    @tool("trend_detection")
    def trend_detection_tool(keywords: str, time_range: str = "30d", mode: str = "mock") -> str:
        kws = [k.strip() for k in (keywords or "").split(",") if k.strip()]

        async def _run() -> List[Dict[str, Any]]:
            cfg = {}
            p = Path(__file__).resolve().parents[1] / "config.yaml"
            if p.exists():
                with open(str(p), "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            tool_obj = TrendDetectionTool(config={"mode": mode, "config": cfg})
            try:
                trends = await tool_obj.detect_trends(kws, time_range=time_range)
                return [
                    {
                        "keyword": t.keyword,
                        "trend_score": t.trend_score,
                        "change_percent": t.change_percent,
                        "category": t.category,
                        "source": t.source,
                        "timestamp": t.timestamp.isoformat(),
                    }
                    for t in trends
                ]
            finally:
                await tool_obj.close()

        try:
            asyncio.get_running_loop()
            return json.dumps({"success": False, "error": "async_context_not_supported"}, ensure_ascii=False, indent=2)
        except RuntimeError:
            result = asyncio.run(_run())
            return json.dumps(result, ensure_ascii=False, indent=2)

    return trend_detection_tool


# === 便捷函数 ===

async def get_industry_trends(
    industry: str,
    keywords: List[str] = None,
    limit: int = 10
) -> List[TrendingTopic]:
    """便捷函数：获取行业趋势
    
    Args:
        industry: 行业名称
        keywords: 相关关键词（可选）
        limit: 返回数量
    
    Returns:
        List[TrendingTopic]: 热门话题列表
    """
    tool = TrendDetectionTool()
    try:
        topics = await tool.identify_trending_topics(industry, limit)
        if keywords:
            trend_keywords = await tool.detect_trends(keywords, "7d")
            all_topics = topics.copy()
            for tk in trend_keywords[:limit]:
                topic = TrendingTopic(
                    title=tk.keyword,
                    keywords=[tk.keyword],
                    trend_score=tk.trend_score,
                    is_rising=tk.change_percent > 0
                )
                all_topics.append(topic)
            all_topics.sort(key=lambda x: x.trend_score, reverse=True)
            return all_topics[:limit]
        return topics
    finally:
        await tool.close()


async def monitor_keyword_trend(
    keyword: str,
    time_range: str = "30d"
) -> Dict[str, Any]:
    """便捷函数：监控单个关键词趋势
    
    Args:
        keyword: 关键词
        time_range: 时间范围
    
    Returns:
        Dict: 趋势分析结果
    """
    tool = TrendDetectionTool()
    try:
        trends = await tool.detect_trends([keyword], time_range)
        if not trends:
            return {
                'keyword': keyword,
                'has_trend': False,
                'recommendation': '未检测到明显趋势'
            }
        trend = trends[0]
        return {
            'keyword': keyword,
            'has_trend': True,
            'trend_score': trend.trend_score,
            'change_percent': trend.change_percent,
            'is_rising': trend.change_percent > 0,
            'recommendation': _get_recommendation(trend)
        }
    finally:
        await tool.close()


def _get_recommendation(trend: TrendItem) -> str:
    """获取趋势建议"""
    if trend.trend_score >= 80 and trend.change_percent > 50:
        return "强烈建议立即制作相关内容"
    elif trend.trend_score >= 60 and trend.change_percent > 20:
        return "建议本周内制作相关内容"
    elif trend.trend_score >= 40:
        return "可以作为选题参考"
    else:
        return "建议继续观察趋势发展"


if __name__ == '__main__':
    # 测试
    async def test():
        topics = await get_industry_trends("EMBA 商学院", limit=5)
        for t in topics:
            print(f"{t.title} - 热度: {t.trend_score}")
    
    asyncio.run(test())
