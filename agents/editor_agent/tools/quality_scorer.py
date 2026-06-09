#!/usr/bin/env python3
"""
质量评分工具 - EditorAgent
对文章进行多维度质量评分
"""

import json
import re
from html import unescape
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ScoreDimension:
    """评分维度"""
    name: str
    score: float  # 0-100
    weight: float  # 权重
    issues: List[Any]
    suggestions: List[str]


class QualityScorer:
    """文章质量评分工具"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.weights = (
            ((self.config.get("quality_scoring") or {}).get("weights") or {})
            if isinstance(self.config, dict)
            else {}
        )
        if not self.weights:
            self.weights = {
                "content_quality": 0.30,
                "logical_clarity": 0.20,
                "language_expression": 0.20,
                "seo_optimization": 0.15,
                "brand_consistency": 0.15,
            }
        self.pass_threshold = float(((self.config.get("quality") or {}).get("pass_threshold") or 75))
        self.prohibited_cfg = (self.config.get("brand_consistency") or {}).get("prohibited_words") or {}
        self.auto_fix_cfg = (self.config.get("execution") or {}).get("auto_fix") or {}
        self._patches: List[Dict[str, Any]] = []
    
    def score(self, article: Dict[str, Any], brand_guidelines: Optional[Dict] = None) -> Dict[str, Any]:
        """
        对文章进行质量评分
        
        Args:
            article: 文章内容，包含 title, content, meta 等
            brand_guidelines: 品牌指南（可选）
            
        Returns:
            评分结果
        """
        article = article or {}
        content = (
            article.get("content_md")
            or article.get("content")
            or article.get("content_html")
            or ""
        )
        normalized = dict(article)
        normalized["content"] = content
        normalized["word_count"] = self._count_words(self._strip_markdown(str(content)))
        normalized["primary_keyword"] = article.get("primary_keyword") or ""

        self._patches = []
        dimensions: List[ScoreDimension] = []
        total_score = 0.0
        
        content_score = self._score_content(normalized)
        dimensions.append(content_score)
        total_score += content_score.score * float(self.weights.get("content_quality") or 0.0)
        
        logical_score = self._score_logic(normalized)
        dimensions.append(logical_score)
        total_score += logical_score.score * float(self.weights.get("logical_clarity") or 0.0)
        
        language_score = self._score_language(normalized)
        dimensions.append(language_score)
        total_score += language_score.score * float(self.weights.get("language_expression") or 0.0)
        
        seo_score = self._score_seo(normalized)
        dimensions.append(seo_score)
        total_score += seo_score.score * float(self.weights.get("seo_optimization") or 0.0)
        
        brand_score = self._score_brand(normalized, brand_guidelines or {})
        dimensions.append(brand_score)
        total_score += brand_score.score * float(self.weights.get("brand_consistency") or 0.0)
        
        dim_map = {
            "content_quality": content_score,
            "logical_clarity": logical_score,
            "language_expression": language_score,
            "seo_optimization": seo_score,
            "brand_consistency": brand_score,
        }

        issues_found = self._collect_issues_found(dim_map)
        suggestions = self._collect_all_suggestions(dimensions)
        overall = round(total_score, 1)

        return {
            "success": True,
            "quality_score": {
                "overall": overall,
                "dimensions": {k: round(v.score, 1) for k, v in dim_map.items()},
            },
            "issues_found": issues_found,
            "patches": list(self._patches),
            "suggestions": suggestions,
            "grade": self._get_grade(total_score),
            "pass": overall >= self.pass_threshold,
        }
    
    def _score_content(self, article: Dict) -> ScoreDimension:
        """评分内容质量"""
        issues = []
        suggestions = []
        score = 100
        
        content = article.get("content", "")
        title = article.get("title", "")
        
        # 检查字数
        word_count = int(article.get("word_count") or 0)
        if word_count < 800:
            issues.append(f"文章字数({word_count})较少，建议≥800字")
            score -= (800 - word_count) * 0.02
        elif word_count > 5000:
            issues.append(f"文章字数({word_count})过多，建议≤5000字")
            score -= (word_count - 5000) * 0.01
        
        # 检查是否有实质性内容
        if len(set(self._strip_markdown(content))) < 100:
            issues.append("文章内容重复度过高")
            score -= 20
        
        # 检查标题
        if len(title) < 10:
            issues.append("标题过短")
            score -= 10
        elif len(title) > 50:
            issues.append("标题过长，建议≤50字")
            score -= 5
        
        # 检查引言
        if "引言" not in content and "导语" not in content and word_count > 500:
            suggestions.append("建议添加引言/导语部分")
        
        # 检查结语
        if "总结" not in content and "结论" not in content and word_count > 1000:
            suggestions.append("建议添加总结/结论部分")
        
        return ScoreDimension(
            name="content_quality",
            score=max(0, min(100, score)),
            weight=float(self.weights.get("content_quality") or 0.0),
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_logic(self, article: Dict) -> ScoreDimension:
        """评分逻辑连贯性"""
        issues = []
        suggestions = []
        score = 100
        
        content = article.get("content", "")
        
        # 检查过渡词
        transition_words = ["首先", "其次", "然后", "接着", "最后", "因此", "所以", "然而", "但是", "此外", "另外", "与此同时"]
        transition_count = sum(content.count(word) for word in transition_words)
        
        if transition_count < 3:
            suggestions.append("过渡词使用较少，建议增加")
            score -= (3 - transition_count) * 5
        
        # 检查因果关系
        cause_words = ["因为", "由于", "导致", "所以", "因此", "于是"]
        effect_words = ["结果", "于是", "导致", "造成", "使得"]
        
        has_cause = any(word in content for word in cause_words)
        has_effect = any(word in content for word in effect_words)
        
        if has_cause and not has_effect:
            suggestions.append("使用因果连词时建议补充结果")
        if has_effect and not has_cause:
            suggestions.append("描述结果时建议补充原因")
        
        # 检查重复内容
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        if len(lines) > 5:
            similar_lines = self._find_similar_lines(lines)
            if similar_lines > 2:
                issues.append(f"存在{similar_lines}个相似段落")
                score -= similar_lines * 5
        
        md = self._strip_markdown(content)
        h2_count = md.count("\n## ")
        if h2_count == 0 and int(article.get("word_count") or 0) > 500:
            issues.append("缺少二级标题")
            score -= 10

        return ScoreDimension(
            name="logical_clarity",
            score=max(0, min(100, score)),
            weight=float(self.weights.get("logical_clarity") or 0.0),
            issues=issues,
            suggestions=suggestions,
        )
    
    def _score_language(self, article: Dict) -> ScoreDimension:
        """评分语言表达"""
        issues = []
        suggestions = []
        score = 100
        
        content = article.get("content", "")
        
        # 检查口语化表达
        colloquial_patterns = [
            (r'非常非常', '建议简化'),
            (r'特别特别', '建议简化'),
            (r'真的真的', '建议简化'),
        ]
        
        for pattern, _ in colloquial_patterns:
            if re.search(pattern, content):
                issues.append("存在重复修饰")
                score -= 5
                break
        
        # 检查绝对化表达
        absolute_patterns = [r'必须', r'绝对', r'100%', r'一定不']
        absolute_count = sum(len(re.findall(p, content)) for p in absolute_patterns)
        if absolute_count > 2:
            issues.append(f"存在{absolute_count}处绝对化表达")
            score -= absolute_count * 2
        
        # 检查中英文混用
        mixed_count = len(re.findall(r'[a-zA-Z]{5,}', content))
        if mixed_count > 20:
            suggestions.append(f"存在{mixed_count}处英文，建议过长英文词加中文注释")
            score -= 5
        
        return ScoreDimension(
            name="language_expression",
            score=max(0, min(100, score)),
            weight=float(self.weights.get("language_expression") or 0.0),
            issues=issues,
            suggestions=suggestions,
        )
    
    def _score_seo(self, article: Dict) -> ScoreDimension:
        """评分SEO优化"""
        issues = []
        suggestions = []
        score = 100
        
        title = article.get("title", "")
        content = article.get("content", "")
        meta_description = article.get("meta_description", "")
        primary_keyword = article.get("primary_keyword", "")
        clean = self._strip_markdown(content)
        word_count = int(article.get("word_count") or 0)
        
        # 关键词检查
        if primary_keyword:
            # 标题中是否包含关键词
            if primary_keyword not in title:
                issues.append("主关键词未出现在标题中")
                score -= 15
            
            # 首段是否包含关键词
            first_paragraph = content.split("\n\n")[0] if content else ""
            if primary_keyword not in first_paragraph:
                issues.append("主关键词未出现在首段")
                score -= 10
            
            # 关键词密度
            density = self._keyword_density(clean, str(primary_keyword), word_count)
            if density < 0.5:
                issues.append(f"关键词密度({density:.1f}%)偏低，建议1-2.5%")
                score -= 10
            elif density > 3:
                issues.append(f"关键词密度({density:.1f}%)过高，可能被判定为堆砌")
                score -= 15
        else:
            suggestions.append("建议设置主关键词")
            score -= 20
        
        # Meta描述检查
        if not meta_description:
            suggestions.append("建议添加Meta描述")
            score -= 10
        elif len(meta_description) < 100:
            suggestions.append("Meta描述过短，建议150-160字")
            score -= 5
        
        return ScoreDimension(
            name="seo_optimization",
            score=max(0, min(100, score)),
            weight=float(self.weights.get("seo_optimization") or 0.0),
            issues=issues,
            suggestions=suggestions,
        )
    
    def _score_brand(self, article: Dict, brand_guidelines: Dict) -> ScoreDimension:
        """评分品牌一致性"""
        issues = []
        suggestions = []
        score = 100
        
        content = article.get("content", "")
        
        # 品牌调性检查
        required_tones = brand_guidelines.get("tones", [])
        forbidden_words = brand_guidelines.get("forbidden_words", [])

        cfg = self.prohibited_cfg if isinstance(self.prohibited_cfg, dict) else {}
        if bool(cfg.get("enabled", False)):
            forbidden_words = list(set(forbidden_words + list(cfg.get("words") or [])))
        action = str(cfg.get("action") or "flag")
        allow_fix = bool(self.auto_fix_cfg.get("enabled", False)) and bool(self.auto_fix_cfg.get("fix_prohibited_words", False))
        
        # 检查禁用词
        for word in forbidden_words:
            if word in content:
                issues.append(f"使用了禁用词「{word}」")
                score -= 20
                if action == "auto_fix" and allow_fix:
                    for m in re.finditer(re.escape(str(word)), content):
                        self._patches.append(
                            {
                                "start": int(m.start()),
                                "end": int(m.end()),
                                "replacement": "",
                                "reason": "prohibited_word",
                                "confidence": 0.9,
                            }
                        )
        
        # 检查是否使用正确人称
        preferred_person = brand_guidelines.get("preferred_person", "third")
        if preferred_person == "first" and "我们" not in content:
            suggestions.append("建议使用第一人称「我们」")
        elif preferred_person == "third" and "我们" in content:
            suggestions.append("建议使用第三人称")
        
        return ScoreDimension(
            name="brand_consistency",
            score=max(0, min(100, score)),
            weight=float(self.weights.get("brand_consistency") or 0.0),
            issues=issues,
            suggestions=suggestions,
        )
    
    def _find_similar_lines(self, lines: List[str], threshold: float = 0.8) -> int:
        """找出相似行数"""
        similar_count = 0
        for i, line1 in enumerate(lines):
            for line2 in lines[i+1:]:
                if len(line1) > 20 and len(line2) > 20:
                    similarity = self._calculate_similarity(line1, line2)
                    if similarity > threshold:
                        similar_count += 1
        return min(similar_count, len(lines) // 3)
    
    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """计算两个字符串的相似度"""
        set1 = set(s1)
        set2 = set(s2)
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0
    
    def _get_grade(self, score: float) -> str:
        """根据分数确定等级"""
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"
    
    def _dim_to_dict(self, dim: ScoreDimension) -> Dict:
        """将评分维度转为字典"""
        return {
            "name": dim.name,
            "score": round(dim.score, 1),
            "weight": dim.weight,
            "issues": dim.issues,
            "suggestions": dim.suggestions
        }

    def _strip_markdown(self, text: str) -> str:
        t = unescape(text or "")
        t = re.sub(r"(?is)```.*?```", " ", t)
        t = re.sub(r"`[^`]+`", " ", t)
        t = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", t)
        t = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", t)
        t = re.sub(r"(?s)<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _count_words(self, text: str) -> int:
        t = text or ""
        en = re.findall(r"[a-zA-Z0-9]+", t)
        zh = re.findall(r"[\u4e00-\u9fff]", t)
        return len(en) + len(zh)

    def _keyword_density(self, clean_text: str, keyword: str, word_count: int) -> float:
        kw = (keyword or "").strip()
        if not kw:
            return 0.0
        denom = max(word_count, 1)
        hits = clean_text.count(kw)
        return hits / denom * 100.0

    def _collect_issues_found(self, dim_map: Dict[str, ScoreDimension]) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        for k, dim in dim_map.items():
            for msg in dim.issues:
                text = str(msg) if msg is not None else ""
                t = "issue"
                severity = "warning"
                if text.startswith("使用了禁用词"):
                    t = "禁用词"
                    severity = "critical"
                elif "关键词密度" in text and "过高" in text:
                    t = "关键词密度过高（>3%）"
                    severity = "critical"
                elif "字数" in text and "较少" in text:
                    t = "字数不足"
                elif "事实错误" in text:
                    t = "事实错误"
                    severity = "critical"
                issues.append({"type": t, "severity": severity, "dimension": k, "message": text})
        return issues
    
    def _collect_all_issues(self, dimensions: List[ScoreDimension]) -> List[str]:
        """收集所有问题"""
        issues = []
        for dim in dimensions:
            issues.extend([f"[{dim.name}]{issue}" for issue in dim.issues])
        return issues
    
    def _collect_all_suggestions(self, dimensions: List[ScoreDimension]) -> List[str]:
        """收集所有建议"""
        suggestions = []
        for dim in dimensions:
            suggestions.extend([f"[{dim.name}]{sug}" for sug in dim.suggestions])
        return suggestions


# CrewAI Tool 包装
def get_quality_scorer_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("quality_scorer")
    def quality_scorer_tool(article_json: str, brand_guidelines_json: str = "{}", config_json: str = "{}") -> str:
        """
        对文章进行多维度质量评分。
        
        Args:
            article_json: 文章内容的JSON字符串，需包含 title, content 字段
            brand_guidelines_json: 品牌指南的JSON字符串（可选）
            
        Returns:
            JSON格式的评分结果
        """
        article = json.loads(article_json)
        brand_guidelines = json.loads(brand_guidelines_json)
        cfg = json.loads(config_json)
        
        scorer = QualityScorer(config=cfg)
        result = scorer.score(article, brand_guidelines)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    return quality_scorer_tool


if __name__ == "__main__":
    # 测试
    scorer = QualityScorer()
    
    article = {
        "title": "EMBA项目选择指南：如何选择适合自己的商学院",
        "content": """
        # EMBA项目选择指南
        
        ## 引言
        EMBA（高级管理人员工商管理硕士）项目是许多企业中高层管理者提升管理能力的重要途径。
        
        ## 选择标准
        1. 学校排名和声誉
        2. 师资力量
        3. 课程设置
        4. 校友资源
        5. 学费性价比
        
        ## 注意事项
        选择时需要考虑自己的职业规划，不要盲目追求排名。
        
        ## 总结
        选择适合自己的EMBA项目需要综合考虑多方面因素。
        """,
        "primary_keyword": "EMBA选择",
        "meta_description": "本文为您详细介绍如何选择适合自己的EMBA项目，从多个维度进行分析。"
    }
    
    result = scorer.score(article)
    print(json.dumps(result, ensure_ascii=False, indent=2))
