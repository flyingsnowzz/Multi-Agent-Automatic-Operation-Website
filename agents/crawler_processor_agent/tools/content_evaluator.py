"""
Content Evaluator Tool

评估 crawler 内容是否通过门禁，并兼容返回历史评分字段。
支持规则评估和 LLM 深度评估。
"""

import json
import re
from collections import Counter
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse

try:
    from crewai.tools import tool
except Exception:
    def tool(func):
        return func


class ContentEvaluator:
    """Crawler 门禁评估器。"""
    
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
        self.min_base_relevance_score = float(
            self.config.get("min_base_relevance_score", self.config.get("min_relevance_score", 0.4))
        )
        self.min_base_usability_score = float(
            self.config.get("min_base_usability_score", self.config.get("min_quality_score", 0.5))
        )
        self.require_source_ok = bool(self.config.get("require_source_ok", False))
        self.require_content_complete = bool(self.config.get("require_content_complete", False))
        self.max_noise_ratio = float(self.config.get("max_noise_ratio", 0.35))
        trusted_domains = self.config.get("trusted_source_domains") or []
        self.trusted_source_domains = [str(domain).strip().lower() for domain in trusted_domains if str(domain).strip()]
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
        评估 crawler 门禁结果，并兼容返回历史评分字段。
        
        Args:
            title: 内容标题
            content: 内容正文
            source_url: 来源URL（可选）
            target_keywords: 目标关键词列表（可选）
            
        Returns:
            包含新门禁字段与历史兼容字段的字典
        """
        try:
            title = (title or "").strip()
            content = self._normalize_text(content or "")
            source_url = (source_url or "").strip()
            word_count = self._count_words(content)
            readability = self._readability(content)
            content_signals = self._content_signals(title, content, source_url, target_keywords)
            source_ok = self._check_source(source_url)
            has_copyright_risk = self._check_copyright(content)
            
            if self.use_llm:
                llm_result = await self._evaluate_with_llm(
                    title, content, source_url, target_keywords
                )
                relevance = self._clamp_unit(llm_result.get("relevance_score", 0.5))
                seo = self._clamp_unit(llm_result.get("seo_potential_score", 0.5))
                usability = self._clamp_unit(llm_result.get("quality_score", 0.5))
                extra_details = llm_result.get("details", {}) or {}
            else:
                relevance = self._rule_relevance(title, content, target_keywords, content_signals)
                seo = self._rule_seo(title, content, target_keywords, word_count, content_signals)
                usability = self._rule_usability(
                    title=title,
                    content=content,
                    source_url=source_url,
                    word_count=word_count,
                    signals=content_signals,
                    source_ok=source_ok,
                    has_copyright_risk=has_copyright_risk,
                )
                extra_details = {}

            topic_hint = self._topic_hint(title, content, target_keywords, content_signals)
            material_score = self._material_score(
                relevance=relevance,
                usability=usability,
                seo=seo,
            )
            has_risk = self._has_risk(
                has_copyright_risk=has_copyright_risk,
                signals=content_signals,
            )
            gate_failures = self._gate_failures(
                base_relevance_score=relevance,
                base_usability_score=usability,
                source_ok=source_ok,
                content_complete=bool(content_signals.get("content_complete")),
                noise_ratio=float(content_signals.get("noise_ratio") or 0.0),
                has_copyright_risk=has_copyright_risk,
            )
            gate_passed = not gate_failures
            gate_result = "pass_to_scoring" if gate_passed else "discard"
            next_agent = "ScoringAgent" if gate_passed else None

            return {
                "success": True,
                "quality_score": usability,
                "relevance_score": relevance,
                "seo_potential_score": seo,
                "material_score": material_score,
                "has_risk": has_risk,
                "topic_hint": topic_hint,
                "reason": self._build_reason(gate_failures, gate_result),
                "base_relevance_score": relevance,
                "base_usability_score": usability,
                "source_ok": source_ok,
                "content_complete": bool(content_signals.get("content_complete")),
                "noise_ratio": round(float(content_signals.get("noise_ratio") or 0.0), 4),
                "gate_passed": gate_passed,
                "gate_result": gate_result,
                "next_agent": next_agent,
                "compatibility_mode": True,
                "word_count": word_count,
                "readability_score": readability,
                "has_copyright_risk": has_copyright_risk,
                "details": {
                    **content_signals,
                    **extra_details,
                    "grammar_score": content_signals["language_score"],
                    "originality_score": content_signals["originality_score"],
                    "information_density": content_signals["information_density"],
                    "gate_failures": gate_failures,
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

    def _clamp_unit(self, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        if number > 1:
            number = number / 100.0
        return round(max(0.0, min(number, 1.0)), 4)
    
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
        source_domain = self._source_domain(source_url)
        trusted_source = (
            bool(source_domain)
            and (
                not self.trusted_source_domains
                or any(
                    source_domain == trusted or source_domain.endswith(f".{trusted}")
                    for trusted in self.trusted_source_domains
                )
            )
        )
        language_score = 0.85
        if repetition_ratio > 0.35:
            language_score -= 0.25
        if len(boilerplate_hits) >= 3:
            language_score -= 0.2
        if word_count and len(paragraphs) <= 1 and word_count > 300:
            language_score -= 0.15

        originality_score = max(0.2, 1.0 - repetition_ratio * 1.6 - len(boilerplate_hits) * 0.06)
        content_complete = bool(
            title
            and word_count >= max(30, int(self.min_word_count * 0.5))
            and len(sentences) >= 2
            and len(set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", content))) >= 40
        )
        noise_ratio = min(
            1.0,
            (len(boilerplate_hits) + max(repeated_sentences, 0))
            / max(len(sentences), 1),
        )

        return {
            "paragraph_count": len(paragraphs),
            "line_count": len(lines),
            "sentence_count": len(sentences),
            "repetition_ratio": round(repetition_ratio, 4),
            "boilerplate_hits": boilerplate_hits,
            "keyword_hits": keyword_hits,
            "keyword_coverage": round(len(keyword_hits) / max(len(keywords or []), 1), 4) if keywords else 0.0,
            "has_source_url": has_source,
            "source_domain": source_domain,
            "trusted_source": trusted_source,
            "title_score": round(title_score, 4),
            "language_score": round(max(0.0, min(language_score, 1.0)), 4),
            "originality_score": round(max(0.0, min(originality_score, 1.0)), 4),
            "information_density": round(max(0.0, min(information_density, 1.0)), 4),
            "content_complete": content_complete,
            "noise_ratio": round(noise_ratio, 4),
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

    def _rule_usability(
        self,
        title: str,
        content: str,
        source_url: str,
        word_count: int,
        signals: Dict[str, Any],
        source_ok: bool,
        has_copyright_risk: bool,
    ) -> float:
        """基础可用性分：只判断是否适合进入下一层，不承担写作质量职责。"""
        score = self._rule_quality(title, content, source_url, word_count, signals)
        if self.require_source_ok and not source_ok:
            score = min(score, 0.45)
        if self.require_content_complete and not signals.get("content_complete"):
            score = min(score, 0.35)
        if float(signals.get("noise_ratio") or 0.0) > self.max_noise_ratio:
            score = min(score, 0.4)
        if has_copyright_risk:
            score = min(score, 0.3)
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

    def _source_domain(self, source_url: str) -> str:
        if not source_url:
            return ""
        parsed = urlparse(source_url)
        return (parsed.netloc or "").lower()

    def _check_source(self, source_url: str) -> bool:
        if not source_url:
            return False
        parsed = urlparse(source_url)
        return bool(parsed.scheme in {"http", "https"} and parsed.netloc)

    def _has_risk(self, has_copyright_risk: bool, signals: Dict[str, Any]) -> bool:
        if has_copyright_risk:
            return True
        return float(signals.get("noise_ratio") or 0.0) > max(0.5, self.max_noise_ratio + 0.15)

    def _material_score(self, relevance: float, usability: float, seo: float) -> float:
        score = relevance * 0.35 + usability * 0.45 + seo * 0.20
        return round(score * 100, 2)

    def _topic_hint(
        self,
        title: str,
        content: str,
        keywords: Optional[List[str]],
        signals: Dict[str, Any],
    ) -> str:
        hits = signals.get("keyword_hits") or []
        if hits:
            return str(hits[0])
        cleaned = re.sub(r"[【】\[\]（）()《》:：,，.!！？?？\-_\s]+", " ", title or "").strip()
        generic_titles = {
            "资讯",
            "新闻",
            "快讯",
            "动态",
            "公告",
            "行业资讯",
            "行业动态",
            "最新消息",
        }
        if cleaned and cleaned not in generic_titles and self._count_words(cleaned) >= 4:
            return cleaned[:24]
        first_sentence = next((s.strip() for s in re.split(r"[。！？!?；;\n]+", content or "") if s.strip()), "")
        first_sentence = re.sub(r"\s+", " ", first_sentence).strip()
        if first_sentence and self._count_words(first_sentence) >= 6:
            return first_sentence[:24]
        return ""

    def _gate_failures(
        self,
        *,
        base_relevance_score: float,
        base_usability_score: float,
        source_ok: bool,
        content_complete: bool,
        noise_ratio: float,
        has_copyright_risk: bool,
    ) -> List[str]:
        failures: List[str] = []
        if has_copyright_risk:
            failures.append("copyright_risk")
        if self.require_source_ok and not source_ok:
            failures.append("invalid_source")
        if self.require_content_complete and not content_complete:
            failures.append("content_incomplete")
        if noise_ratio > self.max_noise_ratio:
            failures.append("noise_too_high")
        if base_relevance_score < self.min_base_relevance_score:
            failures.append("low_base_relevance")
        if base_usability_score < self.min_base_usability_score:
            failures.append("low_base_usability")
        return failures

    def _build_reason(self, gate_failures: List[str], gate_result: str) -> str:
        if gate_result == "discard":
            return "未通过门禁：" + ", ".join(gate_failures) if gate_failures else "未通过门禁"
        return "通过 crawler 门禁，可传给 ScoringAgent"
    
    async def _evaluate_with_llm(
        self,
        title: str,
        content: str,
        source_url: Optional[str],
        target_keywords: Optional[List[str]]
    ) -> Dict[str, Any]:
        """使用 LLM 评估兼容字段。"""
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
        包含门禁字段与历史兼容字段的字典
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
