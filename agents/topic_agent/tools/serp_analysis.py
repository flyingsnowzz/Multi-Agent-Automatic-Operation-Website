"""
选题Agent - SERP分析工具
用于分析搜索引擎结果页面，了解竞争情况
"""
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
import asyncio
import logging

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """SERP内容类型"""
    BLOG_POST = "blog_post"
    NEWS = "news"
    VIDEO = "video"
    PRODUCT = "product"
    FAQ = "faq"
    HOW_TO = "how_to"
    GUIDE = "guide"
    LISTICLE = "listicle"
    COMPARISON = "comparison"
    CASE_STUDY = "case_study"
    DEFINITION = "definition"
    UNKNOWN = "unknown"


@dataclass
class SERPResult:
    """SERP单条结果"""
    position: int  # 排名位置
    url: str
    title: str
    snippet: str
    content_type: ContentType
    domain_authority: int  # 域名权重 (0-100)
    page_authority: int  # 页面权重 (0-100)
    is_featured_snippet: bool = False
    is_video_result: bool = False
    has_images: bool = False
    publish_date: Optional[str] = None
    word_count: Optional[int] = None


@dataclass
class SERPAnalysis:
    """SERP分析结果"""
    keyword: str
    total_results: int
    results: List[SERPResult]
    featured_snippet: Optional[str] = None
    top_domains: List[Dict[str, int]] = None  # [{domain: str, count: int}]
    content_types_distribution: Dict[str, int] = None
    avg_word_count: float = 0
    competition_score: float = 0  # 0-100的竞争分数
    content_gaps: List[str] = None  # 发现的内容缺口
    opportunities: List[str] = None  # 建议的机会点


class SERPAnalysisTool:
    """SERP分析工具
    
    使用SerpAPI获取Google/Baidu搜索结果数据
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.api_key = self._load_api_key()
        self.cache = {}
    
    def _load_api_key(self) -> str:
        """加载API密钥"""
        # TODO: 从环境变量加载
        return ""
    
    async def analyze_serp(
        self,
        keyword: str,
        search_engine: str = "google",
        location: str = "cn",
        language: str = "zh-CN"
    ) -> SERPAnalysis:
        """分析SERP结果
        
        Args:
            keyword: 关键词
            search_engine: 搜索引擎 (google/baidu)
            location: 搜索位置
            language: 搜索语言
        
        Returns:
            SERPAnalysis: 分析结果
        """
        logger.info(f"分析SERP: {keyword}")
        
        # 检查缓存
        cache_key = f"{keyword}_{search_engine}_{location}_{language}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # 获取SERP数据
        serp_results = await self._fetch_serp(keyword, search_engine, location, language)
        
        # 分析结果
        analysis = self._analyze_results(keyword, serp_results)
        
        # 缓存
        self.cache[cache_key] = analysis
        
        return analysis
    
    async def analyze_multiple_keywords(
        self,
        keywords: List[str],
        search_engine: str = "google",
        max_concurrent: int = 5
    ) -> Dict[str, SERPAnalysis]:
        """并行分析多个关键词
        
        Args:
            keywords: 关键词列表
            search_engine: 搜索引擎
            max_concurrent: 最大并发数
        
        Returns:
            Dict[str, SERPAnalysis]: 各关键词的分析结果
        """
        logger.info(f"批量分析关键词: {len(keywords)}个")
        
        # 使用信号量控制并发
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def analyze_with_limit(kw):
            async with semaphore:
                return kw, await self.analyze_serp(kw, search_engine)
        
        tasks = [analyze_with_limit(kw) for kw in keywords]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        analysis_dict = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"关键词分析失败: {result}")
                continue
            kw, analysis = result
            analysis_dict[kw] = analysis
        
        return analysis_dict
    
    async def _fetch_serp(
        self,
        keyword: str,
        search_engine: str,
        location: str,
        language: str
    ) -> List[SERPResult]:
        """获取SERP数据"""
        # TODO: 实现SerpAPI调用
        # 示例API调用:
        # params = {
        #     "api_key": self.api_key,
        #     "engine": "google",
        #     "q": keyword,
        #     "location": location,
        #     "hl": language,
        # }
        
        # 模拟数据
        return [
            SERPResult(
                position=i+1,
                url=f"https://example{i}.com/article",
                title=f"{keyword} - 相关文章{i+1}",
                snippet=f"这是关于{keyword}的内容摘要...",
                content_type=ContentType.BLOG_POST,
                domain_authority=50 - i*3,
                page_authority=40 - i*2,
                word_count=2000 + i*100
            )
            for i in range(10)
        ]
    
    def _analyze_results(self, keyword: str, results: List[SERPResult]) -> SERPAnalysis:
        """分析SERP结果"""
        if not results:
            return SERPAnalysis(
                keyword=keyword,
                total_results=0,
                results=[],
                competition_score=0
            )
        
        # 统计顶级域名
        domain_counts = {}
        for r in results:
            domain = r.url.split('/')[2] if '/' in r.url else r.url
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
        
        top_domains = [
            {'domain': d, 'count': c}
            for d, c in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        
        # 内容类型分布
        type_dist = {}
        for r in results:
            t = r.content_type.value
            type_dist[t] = type_dist.get(t, 0) + 1
        
        # 平均字数
        word_counts = [r.word_count for r in results if r.word_count]
        avg_word_count = sum(word_counts) / len(word_counts) if word_counts else 0
        
        # 竞争分数计算
        competition_score = self._calculate_competition(results)
        
        # 内容缺口分析
        content_gaps = self._identify_content_gaps(results)
        
        # 机会识别
        opportunities = self._identify_opportunities(results, avg_word_count)
        
        # 获取精选摘要
        featured = None
        for r in results:
            if r.is_featured_snippet:
                featured = r.snippet
                break
        
        return SERPAnalysis(
            keyword=keyword,
            total_results=len(results),
            results=results,
            featured_snippet=featured,
            top_domains=top_domains,
            content_types_distribution=type_dist,
            avg_word_count=avg_word_count,
            competition_score=competition_score,
            content_gaps=content_gaps,
            opportunities=opportunities
        )
    
    def _calculate_competition(self, results: List[SERPResult]) -> float:
        """计算竞争分数
        
        考虑因素:
        - 高权重网站数量
        - 排名靠前的结果质量
        - 内容深度
        """
        if not results:
            return 0
        
        score = 0
        
        # 顶级结果权重
        top_3 = results[:3]
        for r in top_3:
            score += r.domain_authority * 0.3
            score += r.page_authority * 0.2
            if r.word_count and r.word_count > 2000:
                score += 10
        
        # 整体平均
        avg_da = sum(r.domain_authority for r in results) / len(results)
        score += avg_da * 0.2
        
        return min(score / 10, 100)  # 归一化到0-100
    
    def _identify_content_gaps(self, results: List[SERPResult]) -> List[str]:
        """识别内容缺口"""
        gaps = []
        
        # 检查内容类型多样性
        types = set(r.content_type for r in results)
        
        if ContentType.VIDEO not in types:
            gaps.append("当前无视频内容，可以制作视频版本")
        if ContentType.FAQ not in types:
            gaps.append("缺少FAQ类型内容")
        if ContentType.HOW_TO not in types:
            gaps.append("缺少操作指南类内容")
        
        # 检查数据新鲜度
        old_results = [r for r in results if r.publish_date and self._is_old_content(r.publish_date)]
        if len(old_results) > len(results) * 0.5:
            gaps.append("搜索结果多为旧内容，更新版本有机会")
        
        # 检查内容深度
        avg_words = sum(r.word_count or 0 for r in results) / len(results)
        if avg_words < 1500:
            gaps.append("现有内容普遍较短，深度内容有机会")
        
        return gaps
    
    def _identify_opportunities(
        self,
        results: List[SERPResult],
        avg_word_count: float
    ) -> List[str]:
        """识别机会点"""
        opportunities = []
        
        # 如果没有精选摘要
        has_snippet = any(r.is_featured_snippet for r in results)
        if not has_snippet:
            opportunities.append("无精选摘要，内容优化后可能获得精选")
        
        # 如果内容普遍较短
        if avg_word_count < 1500:
            opportunities.append("平均字数较低，3000+深度文章有机会脱颖而出")
        
        # 如果没有视频
        has_video = any(r.is_video_result for r in results)
        if not has_video:
            opportunities.append("无视频结果，图文+视频内容有机会")
        
        # 检查弱域名
        weak_count = sum(1 for r in results[:5] if r.domain_authority < 40)
        if weak_count >= 2:
            opportunities.append("头部结果有较多低权重站点，有机会竞争")
        
        return opportunities
    
    def _is_old_content(self, publish_date: str) -> bool:
        """判断内容是否过期"""
        # TODO: 实现日期解析和判断
        return False
    
    def infer_content_type(self, title: str, snippet: str) -> ContentType:
        """推断内容类型"""
        title_lower = title.lower()
        snippet_lower = snippet.lower()
        
        # 关键词匹配
        if any(k in title_lower for k in ['如何', 'how to', '教程', 'guide']):
            return ContentType.HOW_TO
        if any(k in title_lower for k in ['多少', '什么', 'why', '原因']):
            return ContentType.FAQ
        if any(k in title_lower for k in ['10个', '5个', 'top 10', 'list']):
            return ContentType.LISTICLE
        if any(k in title_lower for k in ['对比', '比较', 'vs', 'versus']):
            return ContentType.COMPARISON
        if any(k in title_lower for k in ['案例', 'case study', '实例']):
            return ContentType.CASE_STUDY
        if any(k in title_lower for k in ['指南', 'complete guide', '攻略']):
            return ContentType.GUIDE
        
        return ContentType.BLOG_POST


# === 便捷函数 ===

async def analyze_keyword_competition(keyword: str) -> Dict[str, Any]:
    """便捷函数：分析关键词竞争情况
    
    Args:
        keyword: 关键词
    
    Returns:
        Dict: 竞争分析结果
    """
    tool = SERPAnalysisTool()
    analysis = await tool.analyze_serp(keyword)
    
    return {
        'keyword': keyword,
        'competition_score': analysis.competition_score,
        'competition_level': _get_competition_level(analysis.competition_score),
        'avg_word_count': analysis.avg_word_count,
        'top_domains': analysis.top_domains,
        'content_gaps': analysis.content_gaps,
        'opportunities': analysis.opportunities,
        'featured_snippet': analysis.featured_snippet,
        'recommendation': _get_recommendation(analysis)
    }


def _get_competition_level(score: float) -> str:
    """获取竞争等级"""
    if score < 30:
        return "低"
    elif score < 60:
        return "中"
    else:
        return "高"


def _get_recommendation(analysis: SERPAnalysis) -> str:
    """获取建议"""
    if analysis.competition_score < 30:
        return "✅ 竞争较低，建议立即制作内容"
    elif analysis.competition_score < 50:
        return "📝 竞争适中，需要高质量内容竞争"
    elif analysis.competition_score < 70:
        return "⚠️ 竞争较高，需要差异化视角"
    else:
        return "❌ 竞争激烈，建议选择其他关键词"


if __name__ == '__main__':
    # 测试
    async def test():
        result = await analyze_keyword_competition("EMBA 报考指南")
        print(f"关键词: {result['keyword']}")
        print(f"竞争分数: {result['competition_score']}")
        print(f"竞争等级: {result['competition_level']}")
        print(f"机会点: {result['opportunities']}")
    
    asyncio.run(test())
