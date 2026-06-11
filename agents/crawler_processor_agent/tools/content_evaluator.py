"""
Content Evaluator Tool

评估爬虫内容的质量、相关性和SEO潜力。
支持规则评估和LLM深度评估。
"""

import re
import json
from typing import Dict, List, Any, Optional
from crewai.tools import tool


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
        self.copyright_risk_cfg = self.config.get("copyright_risk") or {}
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
            word_count = self._count_words(content)
            readability = self._readability(content)
            
            if self.use_llm:
                llm_result = await self._evaluate_with_llm(
                    title, content, source_url, target_keywords
                )
                return {
                    "success": True,
                    "quality_score": self._score_0_100(llm_result.get("quality_score", 50)),
                    "relevance_score": self._score_0_100(llm_result.get("relevance_score", 50)),
                    "seo_potential_score": self._score_0_100(llm_result.get("seo_potential_score", 50)),
                    "word_count": word_count,
                    "readability_score": readability,
                    "has_copyright_risk": self._check_copyright(content),
                    "details": llm_result.get("details", {})
                }
            else:
                # 规则评估
                quality = self._rule_quality(content, word_count)
                relevance = self._rule_relevance(title, content, target_keywords)
                seo = self._rule_seo(content, target_keywords, word_count)
                
                return {
                    "success": True,
                    "quality_score": quality,
                    "relevance_score": relevance,
                    "seo_potential_score": seo,
                    "word_count": word_count,
                    "readability_score": readability,
                    "has_copyright_risk": self._check_copyright(content),
                    "details": {
                        "grammar_score": self._rule_grammar_score(content),
                        "originality_score": self._rule_originality_score(content),
                        "information_density": self._rule_information_density(content),
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
    
    def _score_0_100(self, value: Any) -> float:
        """Normalize score-like values to the crawler's 0-100 scale."""
        try:
            score = float(value)
        except Exception:
            return 0.0
        return max(min(score, 100.0), 0.0)

    def _readability(self, text: str) -> float:
        """可读性得分（0-100，简化版）"""
        words = self._count_words(text)
        if words < 100:
            return 30.0
        elif words < 500:
            return 60.0
        elif words < 2000:
            return 80.0
        else:
            return 90.0
    
    def _rule_quality(self, content: str, word_count: int) -> float:
        """基于规则的质量得分（简化）"""
        score = 50.0  # 基础分
        
        # 字数加分
        if word_count > 500:
            score += 20.0
        
        # 段落数加分
        if content.count('\n') > 3:
            score += 10.0
        
        # 外部链接加分
        if 'http' in content:
            score += 10.0
        
        # 图片加分
        if '![' in content or '<img' in content:
            score += 10.0

        return min(score, 100.0)
    
    def _rule_relevance(self, title: str, content: str, keywords: Optional[List[str]]) -> float:
        """基于规则的相关性得分（简化）"""
        if not keywords:
            return 50.0
        
        title_l = title.lower()
        content_l = content.lower()
        
        matched = sum(1 for k in keywords if k.lower() in title_l or k.lower() in content_l)
        return max(min((matched / len(keywords)) * 100.0, 100.0), 0.0)
    
    def _rule_seo(self, content: str, keywords: Optional[List[str]], word_count: int) -> float:
        """基于规则的SEO潜力得分（简化）"""
        score = 50.0
        
        # 关键词密度检查
        if keywords:
            for k in keywords:
                density = content.lower().count(k.lower()) / max(word_count, 1)
                if 0.01 <= density <= 0.03:
                    score += 10.0
        
        # 内容长度加分
        if 500 <= word_count <= 3000:
            score += 20.0

        return min(score, 100.0)
    
    def _check_copyright(self, content: str) -> bool:
        cfg = self.copyright_risk_cfg if isinstance(self.copyright_risk_cfg, dict) else {}
        enabled = bool(cfg.get("check_enabled", False))
        if not enabled:
            return False
        keywords = cfg.get("risk_keywords") or []
        patterns = cfg.get("risk_patterns") or []
        text = content or ""
        for k in keywords:
            if k and str(k) in text:
                return True
        for p in patterns:
            try:
                if re.search(str(p), text):
                    return True
            except Exception:
                continue
        return False

    def _rule_grammar_score(self, content: str) -> float:
        text = content or ""
        if not text.strip():
            return 0.0
        bad = len(re.findall(r"[�]", text))
        total = max(len(text), 1)
        score = (1.0 - (bad / total)) * 100.0
        return max(min(score, 100.0), 0.0)

    def _rule_originality_score(self, content: str) -> float:
        text = re.sub(r"\s+", "", content or "")
        if not text:
            return 0.0
        unique_ratio = len(set(text)) / max(len(text), 1)
        return max(min(unique_ratio * 100.0, 100.0), 0.0)

    def _rule_information_density(self, content: str) -> float:
        text = content or ""
        words = self._count_words(text)
        if words <= 0:
            return 0.0
        punct = len(re.findall(r"[，。！？；：,.!?;:]", text))
        score = min((punct / words) * 1000.0, 100.0)
        return max(min(score, 100.0), 0.0)
    
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
        1. 质量得分（0-100，考虑语法、原创性、信息密度）
        2. 相关性得分（0-100，考虑与目标关键词的相关性）
        3. SEO潜力得分（0-100，考虑关键词布局、内容长度、可读性）
        
        标题：{title}
        内容：{content[:2000]}...  # 截断，避免超限
        目标关键词：{target_keywords or '无'}
        
        返回 JSON 格式：
        {{
            "quality_score": 80,
            "relevance_score": 70,
            "seo_potential_score": 75,
            "details": {{
                "grammar_score": 90,
                "originality_score": 80,
                "information_density": 70
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
