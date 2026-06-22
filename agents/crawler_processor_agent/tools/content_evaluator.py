"""
Content Evaluator Tool

评估爬虫内容的质量、相关性和SEO潜力。
支持规则评估和LLM深度评估。
"""

import json
import re
from collections import Counter
from typing import Dict, List, Any, Optional

try:
    from crewai.tools import tool
except Exception:
    def tool(func):
        return func


class ContentEvaluator:
    """内容评估器"""
    
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化内容评估器。
        
        Args:
            config: 评估配置，包含：
                - use_llm: 是否使用LLM评估（默认False，省钱）
                - llm_model: LLM模型（默认gpt-4o）
                - min_word_count: 最小字数
                - max_word_count: 最大字数
        """
        self.config = config or {}
        self.use_llm = self.config.get("use_llm", False)
        self.llm_model = self.config.get("llm_model", "gpt-4o")
        self.min_word_count = self.config.get("min_word_count", 100)
        self.max_word_count = self.config.get("max_word_count", 5000)
        self._llm_client = None
    
    async def _get_llm_client(self):
        """获取LLM客户端"""
        if self._llm_client is None:
            import openai
            self._llm_client = openai.AsyncOpenAI(
                api_key=self.config.get("api_key") or None  # 使用环境变量
            )
        return self._llm_client
    
    async def evaluate(
        self,
        title: str,
        content: str,
        source_url: Optional[str] = None,
        target_keywords: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        评估内容质量、相关性、SEO潜力。
        
        Args:
            title: 内容标题
            content: 内容正文
            source_url: 来源URL（可选）
            target_keywords: 目标关键词列表（可选）
            
        Returns:
            包含 success, quality_score, relevance_score, seo_potential_score, 
            word_count, readability_score, has_copyright_risk, details 的字典
        """
        try:
            title = (title or "").strip()
            content = self._normalize_text(content or "")
            source_url = (source_url or "").strip()
            word_count = self._count_words(content)
            readability = self._readability(content)
            content_signals = self._content_signals(title, content, source_url, target_keywords)
            
            if self.use_llm:
                llm_result = await self._evaluate_with_llm(
                    title, content, source_url, target_keywords
                )
                return {
                    "success": True,
                    "quality_score": llm_result.get("quality_score", 0.5),
                    "relevance_score": llm_result.get("relevance_score", 0.5),
                    "seo_potential_score": llm_result.get("seo_potential_score", 0.5),
                    "word_count": word_count,
                    "readability_score": readability,
                    "has_copyright_risk": self._check_copyright(content),
                    "details": {
                        **content_signals,
                        **(llm_result.get("details", {}) or {}),
                    }
                }
            else:
                # 规则评估
                quality = self._rule_quality(title, content, source_url, word_count, content_signals)
                relevance = self._rule_relevance(title, content, target_keywords, content_signals)
                seo = self._rule_seo(title, content, target_keywords, word_count, content_signals)
                
                return {
                    "success": True,
                    "quality_score": quality,
                    "relevance_score": relevance,
                    "seo_potential_score": seo,
                    "word_count": word_count,
                    "readability_score": readability,
                    "has_copyright_risk": self._check_copyright(content),
                    "details": {
                        **content_signals,
                        "grammar_score": content_signals["language_score"],
                        "originality_score": content_signals["originality_score"],
                        "information_density": content_signals["information_density"],
                    }
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _count_words(self, text: str) -> int:
        """字数统计（中英文混合）"""
        chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
        english = len(re.findall(r'\b[a-zA-Z]+\b', text))
        return chinese + english

    def _normalize_text(self, text: str) -> str:
        """规整空白，降低爬虫正文里多余换行/空格对评分的影响。"""
        text = re.sub(r"\r\n?", "\n", text or "")
        text = re.sub(r"[ \t]+", " ", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    
    def _readability(self, text: str) -> float:
        """可读性得分（简化版）"""
        words = self._count_words(text)
        if words < 100:
            return 0.3
        elif words < 500:
            return 0.6
        elif words < 2000:
            return 0.8
        else:
            return 0.9
    
    def _content_signals(
        self,
        title: str,
        content: str,
        source_url: str,
        keywords: Optional[List[str]],
    ) -> Dict[str, Any]:
        """提取可解释的质量信号，供质量/相关性/SEO 三类评分复用。"""
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
        sentences = [s.strip() for s in re.split(r"[。！？!?；;\n]+", content) if s.strip()]
        sentence_counts = Counter(sentences)
        repeated_sentences = sum(count - 1 for count in sentence_counts.values() if count > 1)
        repetition_ratio = repeated_sentences / max(len(sentences), 1)

        boilerplate_patterns = [
            r"点击.*(查看|阅读|下载)",
            r"更多.*(资讯|内容|精彩)",
            r"关注.*(公众号|我们)",
            r"免责声明",
            r"转载.*(注明|来源)",
            r"上一篇|下一篇",
            r"相关推荐",
        ]
        boilerplate_hits = [
            pattern for pattern in boilerplate_patterns
            if re.search(pattern, content, re.IGNORECASE)
        ]

        keyword_hits = []
        text_l = f"{title}\n{content}".lower()
        for keyword in keywords or []:
            kw = (keyword or "").strip().lower()
            if kw and kw in text_l:
                keyword_hits.append(keyword)

        word_count = self._count_words(content)
        unique_chars = len(set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", content)))
        information_density = min(unique_chars / max(word_count, 1) * 2.5, 1.0)

        title_len = self._count_words(title)
        title_score = 1.0
        if not title:
            title_score = 0.0
        elif title_len < 6 or title_len > 80:
            title_score = 0.55
        elif title_len < 10 or title_len > 60:
            title_score = 0.8

        has_source = bool(source_url)
        language_score = 0.85
        if repetition_ratio > 0.35:
            language_score -= 0.25
        if len(boilerplate_hits) >= 3:
            language_score -= 0.2
        if word_count and len(paragraphs) <= 1 and word_count > 300:
            language_score -= 0.15

        originality_score = max(0.2, 1.0 - repetition_ratio * 1.6 - len(boilerplate_hits) * 0.06)

        return {
            "paragraph_count": len(paragraphs),
            "line_count": len(lines),
            "sentence_count": len(sentences),
            "repetition_ratio": round(repetition_ratio, 4),
            "boilerplate_hits": boilerplate_hits,
            "keyword_hits": keyword_hits,
            "keyword_coverage": round(len(keyword_hits) / max(len(keywords or []), 1), 4) if keywords else 0.0,
            "has_source_url": has_source,
            "title_score": round(title_score, 4),
            "language_score": round(max(0.0, min(language_score, 1.0)), 4),
            "originality_score": round(max(0.0, min(originality_score, 1.0)), 4),
            "information_density": round(max(0.0, min(information_density, 1.0)), 4),
        }
    
    def _rule_quality(
        self,
        title: str,
        content: str,
        source_url: str,
        word_count: int,
        signals: Dict[str, Any],
    ) -> float:
        """基于规则的质量得分，偏向可解释、稳定、低成本。"""
        score = 0.25

        if self.min_word_count <= word_count <= self.max_word_count:
            score += 0.2
        elif word_count >= max(50, int(self.min_word_count * 0.6)):
            score += 0.1

        if signals["title_score"] >= 0.8:
            score += 0.12
        elif title:
            score += 0.06

        paragraph_count = int(signals.get("paragraph_count") or 0)
        if paragraph_count >= 3:
            score += 0.12
        elif paragraph_count >= 1:
            score += 0.06

        score += float(signals.get("language_score") or 0) * 0.12
        score += float(signals.get("originality_score") or 0) * 0.12
        score += float(signals.get("information_density") or 0) * 0.12

        if source_url:
            score += 0.05

        if signals.get("boilerplate_hits"):
            score -= min(len(signals["boilerplate_hits"]) * 0.05, 0.2)
        score -= min(float(signals.get("repetition_ratio") or 0) * 0.35, 0.25)

        if self._check_copyright(content):
            score -= 0.25

        if word_count < self.min_word_count:
            score = min(score, 0.49)
        elif word_count > self.max_word_count:
            score = min(score, 0.55)

        return round(max(0.0, min(score, 1.0)), 4)
    
    def _rule_relevance(
        self,
        title: str,
        content: str,
        keywords: Optional[List[str]],
        signals: Dict[str, Any],
    ) -> float:
        """基于规则的相关性得分（简化）"""
        if not keywords:
            return 0.5
        
        title_l = title.lower()
        content_l = content.lower()
        score = 0.0
        for keyword in keywords:
            kw = (keyword or "").strip().lower()
            if not kw:
                continue
            if kw in title_l:
                score += 1.0
            elif kw in content_l:
                score += 0.65
        score = score / max(len(keywords), 1)
        if signals.get("keyword_hits") and signals.get("paragraph_count", 0) >= 2:
            score += 0.1
        return round(max(0.0, min(score, 1.0)), 4)
    
    def _rule_seo(
        self,
        title: str,
        content: str,
        keywords: Optional[List[str]],
        word_count: int,
        signals: Dict[str, Any],
    ) -> float:
        """基于规则的SEO潜力得分（简化）"""
        score = 0.25
        
        if keywords:
            title_l = title.lower()
            score += min(float(signals.get("keyword_coverage") or 0) * 0.25, 0.25)
            for k in keywords:
                kw = (k or "").strip().lower()
                if not kw:
                    continue
                if kw in title_l:
                    score += 0.08
                density = content.lower().count(kw) / max(word_count, 1)
                if 0.01 <= density <= 0.03:
                    score += 0.08
        
        if 500 <= word_count <= 3000:
            score += 0.18
        elif 200 <= word_count < 500:
            score += 0.08

        if int(signals.get("paragraph_count") or 0) >= 3:
            score += 0.08

        if signals.get("has_source_url"):
            score += 0.06
        
        return round(max(0.0, min(score, 1.0)), 4)
    
    def _check_copyright(self, content: str) -> bool:
        """检查版权风险（简单规则）"""
        configured = self.config.get("copyright_risk_keywords") or []
        risk_keywords = [
            "版权所有",
            "保留所有权利",
            "未经许可",
            "不得转载",
            "Copyright",
            "All Rights Reserved",
            *configured,
        ]
        return any(r in content for r in risk_keywords)
    
    async def _evaluate_with_llm(
        self,
        title: str,
        content: str,
        source_url: Optional[str],
        target_keywords: Optional[List[str]]
    ) -> Dict[str, Any]:
        """使用LLM评估内容（深度评估）"""
        client = await self._get_llm_client()
        
        prompt = f"""
        请评估以下内容的：
        1. 质量得分（0-1，考虑语法、原创性、信息密度）
        2. 相关性得分（0-1，考虑与目标关键词的相关性）
        3. SEO潜力得分（0-1，考虑关键词布局、内容长度、可读性）
        
        标题：{title}
        内容：{content[:2000]}...  # 截断，避免超限
        目标关键词：{target_keywords or '无'}
        
        返回 JSON 格式：
        {{
            "quality_score": 0.8,
            "relevance_score": 0.7,
            "seo_potential_score": 0.75,
            "details": {{
                "grammar_score": 0.9,
                "originality_score": 0.8,
                "information_density": 0.7
            }}
        }}
        """
        
        response = await client.chat.completions.create(
            model=self.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result


@tool
def get_content_evaluator_tool(config: Optional[Dict] = None) -> ContentEvaluator:
    """
    获取内容评估器工具。
    
    Args:
        config: 评估配置字典（可选）
        
    Returns:
        ContentEvaluator 实例
    """
    return ContentEvaluator(config)


async def evaluate_content(
    title: str,
    content: str,
    source_url: Optional[str] = None,
    target_keywords: Optional[List[str]] = None,
    config: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    评估内容的便捷函数。
    
    Args:
        title: 内容标题
        content: 内容正文
        source_url: 来源 URL（可选）
        target_keywords: 目标关键词列表（可选）
        config: 评估配置（可选）
        
    Returns:
        包含 success, quality_score, relevance_score, seo_potential_score 等的字典
    """
    evaluator = ContentEvaluator(config)
    return await evaluator.evaluate(title, content, source_url, target_keywords)


if __name__ == "__main__":
    # 测试代码
    import asyncio
    
    async def test_evaluate():
        result = await evaluate_content(
            title="测试标题",
            content="这是测试内容。" * 100,
            target_keywords=["测试", "内容"]
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    asyncio.run(test_evaluate())
