"""
选题Agent - 趋势检测工具
用于追踪行业热点和趋势话题
"""
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
import logging

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
        self.cache = {}
        self.cache_ttl = timedelta(hours=1)  # 缓存1小时
    
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
        # TODO: 实现Google Trends API调用
        # 使用 pytrends 库
        return []
    
    async def _get_baidu_trends(self, keywords: List[str]) -> List[TrendItem]:
        """获取百度指数数据"""
        # TODO: 实现百度指数API调用
        return []
    
    async def _get_social_trends(self, keywords: List[str]) -> List[TrendItem]:
        """获取社交媒体趋势"""
        # TODO: 实现微博热搜、知乎热榜等API调用
        return []
    
    async def _get_google_trending_searches(self, industry: str) -> List[TrendingTopic]:
        """获取Google热门搜索"""
        # TODO: 实现Google Trending Searches API
        return []
    
    async def _get_social_trending_topics(self, industry: str) -> List[TrendingTopic]:
        """获取社交媒体热门话题"""
        # TODO: 实现微博热搜、知乎热榜API
        return []
    
    async def _get_news_trends(self, industry: str) -> List[TrendingTopic]:
        """获取新闻趋势"""
        # TODO: 实现新闻API调用
        return []
    
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
        
        return min(base_score + growth_score + velocity_score, 100)


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
    
    topics = await tool.identify_trending_topics(industry, limit)
    
    if keywords:
        # 如果有关键词，进一步筛选
        trend_keywords = await tool.detect_trends(keywords, "7d")
        # 合并结果
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


def _get_recommendation(trend: TrendItem) -> str:
    """获取趋势建议"""
    if trend.trend_score >= 80 and trend.change_percent > 50:
        return "🔥 强烈建议立即制作相关内容！"
    elif trend.trend_score >= 60 and trend.change_percent > 20:
        return "📈 建议本周内制作相关内容"
    elif trend.trend_score >= 40:
        return "📝 可以作为选题参考"
    else:
        return "⏳ 建议继续观察趋势发展"


if __name__ == '__main__':
    # 测试
    async def test():
        topics = await get_industry_trends("EMBA 商学院", limit=5)
        for t in topics:
            print(f"{t.title} - 热度: {t.trend_score}")
    
    asyncio.run(test())
