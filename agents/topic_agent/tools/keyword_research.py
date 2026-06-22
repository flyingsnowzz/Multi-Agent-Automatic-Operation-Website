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


def is_valid_api_key(key: Optional[str]) -> bool:
    """检查 API 密钥是否有效（排除空值和占位符）"""
    if not key:
        return False
    k = key.strip().lower()
    if not k or k.startswith("your_") or k.endswith("_here") or "api_key_here" in k or "your_serpapi_key" in k:
        return False
    return True


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
        self.local_keywords = self._load_local_keywords()
        self.local_profiles = self._load_local_profiles()
        self.cache = {}  # 简单内存缓存

    def _load_local_profiles(self) -> Dict[str, Any]:
        import yaml
        from pathlib import Path

        cfg = (self.agent_config or {}).get("keyword_research") if isinstance(self.agent_config, dict) else {}
        pool_path = str(cfg.get("keyword_pool") or "config/keywords.yaml").strip() if isinstance(cfg, dict) else "config/keywords.yaml"

        paths_to_try = [
            Path(pool_path),
            Path("config/keywords.yaml"),
            Path(__file__).resolve().parents[3] / "config" / "keywords.yaml",
            Path(__file__).resolve().parents[2] / "config" / "keywords.yaml",
        ]

        for p in paths_to_try:
            if p.exists() and p.is_file():
                try:
                    with open(str(p), "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                        profiles = data.get("profiles") or {}
                        return profiles if isinstance(profiles, dict) else {}
                except Exception as e:
                    logger.warning(f"加载本地 profiles 失败 {p}: {e}")
        return {}

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip().lower())

    def _active_profile_name(self, seed_keywords: List[str]) -> str:
        joined = self._normalize_text("".join(seed_keywords or []))
        if "emba" in joined or "商学院" in joined:
            return "emba"
        return "default"

    def _profile(self, seed_keywords: List[str]) -> Dict[str, Any]:
        name = self._active_profile_name(seed_keywords)
        p = self.local_profiles.get(name) if isinstance(self.local_profiles, dict) else None
        return p if isinstance(p, dict) else {}

    def _business_semantics(self) -> Dict[str, Any]:
        bs = (self.agent_config or {}).get("business_semantics") if isinstance(self.agent_config, dict) else {}
        return bs if isinstance(bs, dict) else {}

    def _forbidden_patterns(self, seed_keywords: List[str]) -> List[str]:
        bs = self._business_semantics()
        p = self._profile(seed_keywords)
        out: List[str] = []
        for src in (bs.get("forbidden_patterns"), p.get("forbidden_patterns")):
            if isinstance(src, list):
                out.extend([str(x) for x in src if str(x).strip()])
        return out

    def _generic_bad_suffixes(self, seed_keywords: List[str]) -> List[str]:
        bs = self._business_semantics()
        p = self._profile(seed_keywords)
        out: List[str] = []
        for src in (bs.get("generic_bad_suffixes"), p.get("generic_bad_suffixes")):
            if isinstance(src, list):
                out.extend([str(x) for x in src if str(x).strip()])
        if not out:
            out = ["技巧", "方法", "工具", "模板"]
        return out

    def _keyword_min_len(self) -> int:
        gates = (self.agent_config or {}).get("quality_gates") if isinstance(self.agent_config, dict) else {}
        if isinstance(gates, dict) and gates.get("keyword_min_len") is not None:
            try:
                return int(gates.get("keyword_min_len"))
            except Exception:
                return 4
        return 4

    def _is_bad_incomplete_question(self, keyword: str) -> bool:
        kw = (keyword or "").strip()
        if re.match(r"^(怎么|如何)\s*[A-Za-z\u4e00-\u9fff]{2,12}$", kw):
            if re.search(r"(怎么选|如何选|怎么考|怎么报|怎么申|怎么读|怎么学|怎么准备|怎么申请|怎么区别)", kw):
                return False
            return True
        return False

    def _match_forbidden(self, keyword: str, forbidden_patterns: List[str]) -> bool:
        kw_norm = self._normalize_text(keyword)
        for p in forbidden_patterns:
            if self._normalize_text(p) and self._normalize_text(p) in kw_norm:
                return True
        return False

    def _semantic_filter_keyword(self, keyword: str, *, seed_keywords: List[str]) -> bool:
        kw = (keyword or "").strip()
        if not kw:
            return False
        if len(kw) < self._keyword_min_len():
            return False
        forbidden = self._forbidden_patterns(seed_keywords)
        if forbidden and self._match_forbidden(kw, forbidden):
            return False
        if self._is_bad_incomplete_question(kw):
            return False
        bad_suffixes = self._generic_bad_suffixes(seed_keywords)
        if any(self._normalize_text(x) in self._normalize_text(kw) for x in bad_suffixes):
            if self._active_profile_name(seed_keywords) != "default":
                return False
        return True
    
    def _load_local_keywords(self) -> Dict[str, Dict[str, Any]]:
        """从 config/keywords.yaml 加载本地关键词库"""
        import yaml
        from pathlib import Path
        
        cfg = (self.agent_config or {}).get("keyword_research") if isinstance(self.agent_config, dict) else {}
        pool_path = str(cfg.get("keyword_pool") or "config/keywords.yaml").strip() if isinstance(cfg, dict) else "config/keywords.yaml"
        
        paths_to_try = [
            Path(pool_path),
            Path("config/keywords.yaml"),
            Path(__file__).resolve().parents[3] / "config" / "keywords.yaml",
            Path(__file__).resolve().parents[2] / "config" / "keywords.yaml",
        ]
        
        for p in paths_to_try:
            if p.exists() and p.is_file():
                try:
                    with open(str(p), "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                        kw_list = data.get("keywords") or []
                        return {str(item.get("keyword", "")).strip().lower(): item for item in kw_list if item.get("keyword")}
                except Exception as e:
                    logger.warning(f"加载本地关键词表失败 {p}: {e}")
        return {}
    
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

        if self.mode == "live":
            # 检查是否有有效的 API 密钥作为真实的活体数据层
            valid_keys = {k: v for k, v in self.api_keys.items() if is_valid_api_key(v)}
            if not valid_keys:
                raise RuntimeError("live_mode_missing_api_keys")

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
        
        filtered_by_terms = self._apply_filters(unique_keywords, seed_keywords=seed_keywords)

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
        p = self._profile(keywords)
        templates = p.get("mock_question_templates") if isinstance(p, dict) else None
        if not isinstance(templates, list) or not templates:
            templates = ["什么是{kw}", "{kw}是什么", "{kw}有哪些", "如何选择{kw}", "{kw}需要多久", "{kw}多少钱"]
        out: List[KeywordData] = []
        for kw in keywords:
            for t in templates:
                q = t.format(kw=kw)
                if not self._semantic_filter_keyword(q, seed_keywords=keywords):
                    continue
                if self.mode == "live":
                    out.append(await self._fetch_live_keyword_metrics(q, fetched_at=fetched_at))
                else:
                    out.append(self._mock_keyword_metrics(q, fetched_at=fetched_at, source="questions"))
        return out
    
    def _is_question_keyword(self, keyword: str) -> bool:
        """判断是否为问题型关键词"""
        kw = (keyword or "").strip()
        question_starts = ['如何', '怎么', '为什么', '什么', '哪个', '哪里', '什么时候', '多少', '是否']
        if any(kw.startswith(q) for q in question_starts):
            return True
        if kw.endswith("吗") or kw.endswith("么") or kw.endswith("是什么"):
            return True
        if "值不值得" in kw or "值不值" in kw:
            return True
        return False
    
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
        seed = [base]
        p = self._profile(seed)
        templates = p.get("mock_expansion_templates") if isinstance(p, dict) else None
        out: List[str] = []
        if isinstance(templates, list) and templates:
            for t in templates:
                try:
                    out.append(str(t).format(kw=base))
                except Exception:
                    continue
        else:
            suffixes = ["指南", "攻略", "教程", "流程", "对比", "案例", "清单"]
            out.append(base)
            for s in suffixes:
                out.append(f"{base}{s}")
                out.append(f"{base} {s}")
        out = [x.strip() for x in out if self._semantic_filter_keyword(x, seed_keywords=seed)]
        seen: set[str] = set()
        uniq: List[str] = []
        for x in out:
            key = self._normalize_text(x)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(x)
        return uniq[: max(1, cluster_size)]

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

    def _apply_filters(self, items: List[KeywordData], *, seed_keywords: List[str]) -> List[KeywordData]:
        exclude_terms = self._exclude_terms()
        forbidden = self._forbidden_patterns(seed_keywords)
        out: List[KeywordData] = []
        for it in items:
            if any(t in it.keyword for t in exclude_terms):
                continue
            if forbidden and self._match_forbidden(it.keyword, forbidden):
                continue
            if not self._semantic_filter_keyword(it.keyword, seed_keywords=seed_keywords):
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
        kw_clean = (keyword or "").strip()
        kw_lower = kw_clean.lower()
        
        # 1. 优先从本地关键词表匹配
        if kw_lower in self.local_keywords:
            item = self.local_keywords[kw_lower]
            logger.info(f"从本地关键词表匹配成功: {kw_clean}")
            return KeywordData(
                keyword=kw_clean,
                search_volume=int(item.get("search_volume", 100)),
                keyword_difficulty=float(item.get("keyword_difficulty", 20.0)),
                cpc=float(item.get("cpc")) if item.get("cpc") is not None else None,
                competition=item.get("competition"),
                source="local_db",
                is_mock=False,
                data_confidence="high",
                fetched_at=fetched_at,
            )
            
        # 2. 尝试从 Semrush API 获取
        semrush_key = self.api_keys.get("semrush")
        if semrush_key:
            try:
                import httpx
                logger.info(f"尝试从 Semrush API 获取指标: {kw_clean}")
                params = {
                    "type": "phrase_this",
                    "key": semrush_key,
                    "phrase": kw_clean,
                    "export_columns": "Ph,Nq,Cp,Co,Kd",
                    "database": "us",
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get("https://api.semrush.com/", params=params)
                    resp.raise_for_status()
                    lines = resp.text.split("\n")
                    if len(lines) >= 2:
                        header = lines[0].strip().split(";")
                        if len(header) <= 1:
                            header = lines[0].strip().split(",")
                        
                        data_line = lines[1].strip().split(";")
                        if len(data_line) <= 1:
                            data_line = lines[1].strip().split(",")
                            
                        row = dict(zip(header, data_line))
                        
                        volume_str = row.get("Search Volume") or row.get("Nq") or "100"
                        cpc_str = row.get("CPC") or row.get("Cp") or "0.0"
                        comp_str = row.get("Competition") or row.get("Co") or "0.5"
                        kd_str = row.get("Keyword Difficulty") or row.get("Kd") or "20"
                        
                        volume = int(float(volume_str.replace('"', '')) or 100)
                        cpc = float(cpc_str.replace('"', ''))
                        kd = float(kd_str.replace('"', ''))
                        comp_val = float(comp_str.replace('"', ''))
                        competition = "high" if comp_val > 0.7 else ("medium" if comp_val > 0.3 else "low")
                        
                        return KeywordData(
                            keyword=kw_clean,
                            search_volume=volume,
                            keyword_difficulty=kd,
                            cpc=cpc,
                            competition=competition,
                            source="semrush",
                            is_mock=False,
                            data_confidence="high",
                            fetched_at=fetched_at,
                        )
            except Exception as e:
                logger.error(f"Semrush API 获取失败 ({kw_clean}): {e}")
                
        # 3. 尝试从 Ahrefs API 获取
        ahrefs_key = self.api_keys.get("ahrefs")
        if ahrefs_key:
            try:
                import httpx
                logger.info(f"尝试从 Ahrefs API 获取指标: {kw_clean}")
                headers = {"Authorization": f"Bearer {ahrefs_key}"}
                params = {
                    "keywords": kw_clean,
                    "country": "us",
                    "volume_mode": "monthly"
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get("https://api.ahrefs.com/v3/keywords-explorer/overview", params=params, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        metrics_list = data.get("keywords", [])
                        if metrics_list:
                            metrics = metrics_list[0]
                            volume = int(metrics.get("volume") or 100)
                            kd = float(metrics.get("difficulty") or 20.0)
                            cpc = float(metrics.get("cpc")) if metrics.get("cpc") is not None else None
                            comp_val = float(metrics.get("clicks") or 0.5)
                            competition = "high" if comp_val > 0.7 else ("medium" if comp_val > 0.3 else "low")
                            
                            return KeywordData(
                                keyword=kw_clean,
                                search_volume=volume,
                                keyword_difficulty=kd,
                                cpc=cpc,
                                competition=competition,
                                source="ahrefs",
                                is_mock=False,
                                data_confidence="high",
                                fetched_at=fetched_at,
                            )
            except Exception as e:
                logger.error(f"Ahrefs API 获取失败 ({kw_clean}): {e}")

        # 4. 尝试通过 SerpAPI (Google Search) 估算关键词难度和竞争度
        serpapi_key = self.api_keys.get("serpapi")
        if serpapi_key:
            try:
                import httpx
                logger.info(f"通过 SerpAPI 估算关键词指标: {kw_clean}")
                params = {
                    "api_key": serpapi_key,
                    "engine": "google",
                    "q": kw_clean,
                    "gl": "us",
                    "hl": "en",
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.get("https://serpapi.com/search.json", params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    search_info = data.get("search_information") or {}
                    total_results = int(search_info.get("total_results") or 0)
                    
                    organic = data.get("organic_results") or []
                    ads = data.get("ads") or []
                    
                    if total_results > 100_000_000:
                        vol = 2500
                    elif total_results > 10_000_000:
                        vol = 1200
                    elif total_results > 1_000_000:
                        vol = 500
                    elif total_results > 100_000:
                        vol = 150
                    else:
                        vol = 50
                        
                    ad_count = len(ads)
                    if ad_count >= 3:
                        kd = 65.0
                        competition = "high"
                    elif ad_count >= 1:
                        kd = 40.0
                        competition = "medium"
                    else:
                        kd = 20.0
                        competition = "low"
                        
                    in_title_count = 0
                    for item in organic[:5]:
                        t = str(item.get("title") or "").lower()
                        if kw_lower in t:
                            in_title_count += 1
                    kd = min(100.0, max(0.0, kd + in_title_count * 5.0))
                    
                    related_searches = data.get("related_searches") or []
                    related_kws = [str(r.get("query") or "") for r in related_searches if r.get("query")][:5]
                    
                    return KeywordData(
                        keyword=kw_clean,
                        search_volume=vol,
                        keyword_difficulty=kd,
                        cpc=1.5 if ad_count > 0 else 0.5,
                        competition=competition,
                        related_keywords=related_kws,
                        source="serpapi_estimate",
                        is_mock=False,
                        data_confidence="medium",
                        fetched_at=fetched_at,
                    )
            except Exception as e:
                logger.error(f"SerpAPI 估算指标失败 ({kw_clean}): {e}")

        # 5. 兜底：若 live 模式下没有合适的 API 密钥或请求全部失败，回退至 Mock 兜底
        logger.warning(f"Live 模式下未能成功获取 API 指标，退回到 Mock 兜底 ({kw_clean})")
        mock_data = self._mock_keyword_metrics(kw_clean, fetched_at=fetched_at, source="live_fallback_mock")
        mock_data.data_confidence = "low"
        return mock_data


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
