"""
选题Agent - 关键词研究工具
用于发现和分析关键词的搜索量、竞争度等数据
"""
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
import asyncio
import logging
import os
import re
import hashlib
from datetime import datetime

logger = logging.getLogger(__name__)


class SearchVolume(Enum):
    """搜索量级别"""
    LOW = "low"       # < 100
    MEDIUM = "medium" # 100-500
    HIGH = "high"     # 500-2000
    VERY_HIGH = "very_high"  # > 2000


@dataclass
class KeywordData:
    """关键词数据"""
    keyword: str
    search_volume: int
    keyword_difficulty: float  # 0-100
    cpc: Optional[float] = None  # 每次点击成本
    competition: Optional[str] = None  # 竞争程度
    trends: Optional[List[float]] = None  # 趋势数据
    related_keywords: Optional[List[str]] = None  # 相关关键词
    source: str = "unknown"  # 数据来源
    is_mock: bool = False
    data_confidence: str = "unknown"
    fetched_at: Optional[str] = None


@dataclass
class KeywordResearchResult:
    """关键词研究结果"""
    primary_keywords: List[KeywordData]
    long_tail_keywords: List[KeywordData]
    questions: List[str]  # 问题型关键词
    gaps: List[str]  # 发现的内容缺口
    is_mock: bool = False
    data_confidence: str = "unknown"
    warnings: List[str] = None


class KeywordResearchTool:
    """关键词研究工具
    
    支持的数据源:
    - Google Keyword Planner (通过SerpAPI)
    - Ahrefs API
    - Semrush API
    - 百度指数
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.mode = (self.config.get("mode") or os.environ.get("TOPIC_AGENT_MODE") or "mock").strip().lower()
        self.agent_config = self.config.get("config") if isinstance(self.config.get("config"), dict) else {}
        self.api_keys = self._load_api_keys()
        self.cache = {}  # 简单内存缓存
    
    def _load_api_keys(self) -> Dict[str, str]:
        """加载API密钥"""
        cfg = (self.agent_config or {}).get("api_keys") if isinstance(self.agent_config, dict) else {}
        cfg_serpapi = str(cfg.get("serpapi") or "").strip() if isinstance(cfg, dict) else ""
        cfg_ahrefs = str(cfg.get("ahrefs") or "").strip() if isinstance(cfg, dict) else ""
        cfg_semrush = str(cfg.get("semrush") or "").strip() if isinstance(cfg, dict) else ""

        serpapi = cfg_serpapi or os.environ.get("SERPAPI_API_KEY", "").strip()
        ahrefs = cfg_ahrefs or os.environ.get("AHREFS_API_KEY", "").strip()
        semrush = cfg_semrush or os.environ.get("SEMRUSH_API_KEY", "").strip()
        return {
            'serpapi': serpapi,
            'ahrefs': ahrefs,
            'semrush': semrush,
        }
    
    async def research_keywords(
        self,
        seed_keywords: List[str],
        min_search_volume: int = 100,
        max_kd: float = 50,
        limit: int = 50
    ) -> KeywordResearchResult:
        """研究关键词
        
        Args:
            seed_keywords: 种子关键词
            min_search_volume: 最小搜索量
            max_kd: 最大关键词难度
            limit: 返回结果限制
        
        Returns:
            KeywordResearchResult: 研究结果
        """
        logger.info(f"开始关键词研究: {seed_keywords}")
        
        warnings: List[str] = []
        is_mock = self.mode != "live"
        data_confidence = "low" if is_mock else "high"

        if self.mode == "live" and not self.api_keys.get("serpapi"):
            raise RuntimeError("live_mode_missing_serpapi_api_key")
        if self.mode == "live":
            raise NotImplementedError("live_keyword_research_not_implemented")

        all_keywords = []
        
        # 并行从多个数据源获取
        tasks = [
            self._get_google_keywords(seed_keywords),
            self._get_related_keywords(seed_keywords),
            self._get_questions(seed_keywords),
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"关键词获取失败: {result}")
                continue
            all_keywords.extend(result)
        
        # 去重
        seen = set()
        unique_keywords = []
        for kw in all_keywords:
            if kw.keyword not in seen:
                seen.add(kw.keyword)
                unique_keywords.append(kw)
        
        filtered_by_terms = self._apply_filters(unique_keywords)

        # 过滤和排序
        filtered = [
            kw for kw in filtered_by_terms
            if kw.search_volume >= min_search_volume
            and kw.keyword_difficulty <= max_kd
        ]
        prefer_terms = self._prefer_terms()
        filtered.sort(key=lambda x: (self._prefer_score(x.keyword, prefer_terms), x.search_volume), reverse=True)
        
        # 分类
        primary = [kw for kw in filtered if kw.search_volume >= 500][:limit // 2]
        long_tail = [kw for kw in filtered if kw.search_volume < 500][:(limit // 4)]
        questions = [kw.keyword for kw in filtered if self._is_question_keyword(kw.keyword)][:limit // 4]
        
        return KeywordResearchResult(
            primary_keywords=primary,
            long_tail_keywords=long_tail,
            questions=questions,
            gaps=self._identify_gaps(primary, long_tail),
            is_mock=is_mock,
            data_confidence=data_confidence,
            warnings=warnings,
        )
    
    async def _get_google_keywords(self, keywords: List[str]) -> List[KeywordData]:
        """从Google获取关键词数据"""
        fetched_at = datetime.now().isoformat()
        results = []
        for kw in keywords:
            if kw in self.cache:
                results.append(self.cache[kw])
            else:
                if self.mode == "live":
                    data = await self._fetch_live_keyword_metrics(kw, fetched_at=fetched_at)
                else:
                    data = self._mock_keyword_metrics(kw, fetched_at=fetched_at, source="google")
                self.cache[kw] = data
                results.append(data)
        return results
    
    async def _get_related_keywords(self, keywords: List[str]) -> List[KeywordData]:
        """获取相关关键词"""
        fetched_at = datetime.now().isoformat()
        results: List[KeywordData] = []
        for kw in keywords:
            expansions = self.expand_keyword_cluster(kw, cluster_size=12)
            for e in expansions:
                if e == kw:
                    continue
                if self.mode == "live":
                    results.append(await self._fetch_live_keyword_metrics(e, fetched_at=fetched_at))
                else:
                    results.append(self._mock_keyword_metrics(e, fetched_at=fetched_at, source="related"))
        return results
    
    async def _get_questions(self, keywords: List[str]) -> List[KeywordData]:
        """获取问题型关键词"""
        fetched_at = datetime.now().isoformat()
        templates = ["如何{kw}", "怎么{kw}", "{kw}是什么", "{kw}有哪些", "{kw}需要多久", "{kw}多少钱"]
        out: List[KeywordData] = []
        for kw in keywords:
            for t in templates:
                q = t.format(kw=kw)
                if self.mode == "live":
                    out.append(await self._fetch_live_keyword_metrics(q, fetched_at=fetched_at))
                else:
                    out.append(self._mock_keyword_metrics(q, fetched_at=fetched_at, source="questions"))
        return out
    
    def _is_question_keyword(self, keyword: str) -> bool:
        """判断是否为问题型关键词"""
        question_starts = ['如何', '怎么', '为什么', '什么', '哪个', '哪里', '什么时候', '多少']
        return any(keyword.startswith(q) for q in question_starts)
    
    def _identify_gaps(self, primary: List[KeywordData], long_tail: List[KeywordData]) -> List[str]:
        """识别内容缺口"""
        gaps: List[str] = []
        pool = primary + long_tail
        texts = [p.keyword for p in pool]
        if not any(self._is_question_keyword(t) for t in texts):
            gaps.append("问题型内容缺口：缺少面向疑问的FAQ/解释类文章")
        if not any(any(x in t for x in ["对比", "比较", "vs", "versus"]) for t in texts):
            gaps.append("对比类内容缺口：缺少竞品/方案对比视角")
        if not any(any(x in t for x in ["流程", "步骤", "怎么", "如何", "教程"]) for t in texts):
            gaps.append("流程/教程类内容缺口：缺少可执行步骤与避坑总结")
        return gaps[:5]
    
    def expand_keyword_cluster(self, keyword: str, cluster_size: int = 10) -> List[str]:
        """扩展关键词簇
        
        基于一个核心关键词，扩展出一组相关关键词
        """
        base = (keyword or "").strip()
        if not base:
            return []
        suffixes = ["指南", "攻略", "教程", "技巧", "方法", "流程", "对比", "案例", "清单", "模板", "工具"]
        out = [base]
        for s in suffixes:
            out.append(f"{base}{s}")
            out.append(f"{base} {s}")
        return out[: max(1, cluster_size)]

    def _exclude_terms(self) -> List[str]:
        cfg = (self.agent_config or {}).get("keyword_research") if isinstance(self.agent_config, dict) else {}
        filters = (cfg.get("filters") or {}) if isinstance(cfg, dict) else {}
        exclude = filters.get("exclude") or []
        return [str(x).strip() for x in exclude if str(x).strip()]

    def _prefer_terms(self) -> List[str]:
        cfg = (self.agent_config or {}).get("keyword_research") if isinstance(self.agent_config, dict) else {}
        filters = (cfg.get("filters") or {}) if isinstance(cfg, dict) else {}
        prefer = filters.get("prefer") or []
        return [str(x).strip() for x in prefer if str(x).strip()]

    def _prefer_score(self, keyword: str, prefer_terms: List[str]) -> int:
        s = 0
        for t in prefer_terms:
            if t and t in keyword:
                s += 1
        return s

    def _apply_filters(self, items: List[KeywordData]) -> List[KeywordData]:
        exclude_terms = self._exclude_terms()
        if not exclude_terms:
            return items
        out: List[KeywordData] = []
        for it in items:
            if any(t in it.keyword for t in exclude_terms):
                continue
            out.append(it)
        return out

    def _hash_int(self, text: str) -> int:
        return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)

    def _mock_keyword_metrics(self, keyword: str, *, fetched_at: str, source: str) -> KeywordData:
        h = self._hash_int(keyword)
        vol = 80 + (h % 2200)
        kd = 10 + (h % 55)
        prefer_terms = self._prefer_terms()
        if self._prefer_score(keyword, prefer_terms) > 0:
            vol = int(vol * 1.2)
            kd = max(0, kd - 5)
        vol = int(min(vol, 5000))
        kd = float(min(max(kd, 0), 100))
        return KeywordData(
            keyword=keyword,
            search_volume=vol,
            keyword_difficulty=kd,
            source=source,
            is_mock=True,
            data_confidence="low",
            fetched_at=fetched_at,
        )

    async def _fetch_live_keyword_metrics(self, keyword: str, *, fetched_at: str) -> KeywordData:
        raise NotImplementedError("live_keyword_metrics_not_implemented")


def get_keyword_research_tool():
    from crewai.tools import tool
    import json
    import yaml
    import os
    from pathlib import Path
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    @tool("keyword_research")
    def keyword_research_tool(
        seed_keywords: str,
        min_search_volume: int = 100,
        max_kd: float = 35,
        limit: int = 30,
        mode: str = "mock",
    ) -> str:
        keywords = [k.strip() for k in (seed_keywords or "").split(",") if k.strip()]

        async def _run() -> Dict[str, Any]:
            cfg = {}
            p = Path(__file__).resolve().parents[1] / "config.yaml"
            if p.exists():
                with open(str(p), "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            tool_obj = KeywordResearchTool(config={"mode": mode, "config": cfg})
            out = await tool_obj.research_keywords(
                seed_keywords=keywords,
                min_search_volume=int(min_search_volume),
                max_kd=float(max_kd),
                limit=int(limit),
            )
            return {
                "primary_keywords": [vars(k) for k in out.primary_keywords],
                "long_tail_keywords": [vars(k) for k in out.long_tail_keywords],
                "questions": out.questions,
                "gaps": out.gaps,
                "is_mock": out.is_mock,
                "data_confidence": out.data_confidence,
                "warnings": out.warnings or [],
            }

        try:
            asyncio.get_running_loop()
            return json.dumps({"success": False, "error": "async_context_not_supported"}, ensure_ascii=False, indent=2)
        except RuntimeError:
            result = asyncio.run(_run())
            return json.dumps(result, ensure_ascii=False, indent=2)

    return keyword_research_tool


# === 便捷函数 ===

async def research_topic_keywords(
    seed_keywords: List[str],
    min_volume: int = 100,
    max_kd: float = 35
) -> KeywordResearchResult:
    """便捷函数：研究选题关键词
    
    Args:
        seed_keywords: 种子关键词列表
        min_volume: 最小搜索量
        max_kd: 最大关键词难度
    
    Returns:
        KeywordResearchResult: 研究结果
    """
    tool = KeywordResearchTool()
    return await tool.research_keywords(
        seed_keywords=seed_keywords,
        min_search_volume=min_volume,
        max_kd=max_kd
    )


if __name__ == '__main__':
    # 测试
    async def test():
        tool = KeywordResearchTool()
        result = await tool.research_keywords(
            seed_keywords=['EMBA', '商学院'],
            min_search_volume=100,
            max_kd=30
        )
        print(f"主关键词: {len(result.primary_keywords)}")
        print(f"长尾关键词: {len(result.long_tail_keywords)}")
        print(f"问题型: {len(result.questions)}")
    
    asyncio.run(test())
