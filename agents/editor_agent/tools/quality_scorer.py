#!/usr/bin/env python3
"""
质量评分工具 - EditorAgent
对文章进行多维度质量评分
"""

import json
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class ScoreDimension:
    """评分维度"""
    name: str
    score: float  # 0-100
    weight: float  # 权重
    issues: List[str]
    suggestions: List[str]


class QualityScorer:
    """文章质量评分工具"""
    
    def __init__(self):
        # 评分维度权重
        self.weights = {
            "content": 0.25,      # 内容质量
            "structure": 0.20,    # 结构清晰度
            "logic": 0.20,        # 逻辑连贯性
            "language": 0.15,     # 语言表达
            "seo": 0.10,          # SEO优化
            "brand": 0.10         # 品牌一致性
        }
    
    def score(self, article: Dict[str, Any], brand_guidelines: Optional[Dict] = None) -> Dict[str, Any]:
        """
        对文章进行质量评分
        
        Args:
            article: 文章内容，包含 title, content, meta 等
            brand_guidelines: 品牌指南（可选）
            
        Returns:
            评分结果
        """
        dimensions = []
        total_score = 0
        
        # 1. 内容质量评分
        content_score = self._score_content(article)
        dimensions.append(content_score)
        total_score += content_score.score * self.weights["content"]
        
        # 2. 结构评分
        structure_score = self._score_structure(article)
        dimensions.append(structure_score)
        total_score += structure_score.score * self.weights["structure"]
        
        # 3. 逻辑评分
        logic_score = self._score_logic(article)
        dimensions.append(logic_score)
        total_score += logic_score.score * self.weights["logic"]
        
        # 4. 语言评分
        language_score = self._score_language(article)
        dimensions.append(language_score)
        total_score += language_score.score * self.weights["language"]
        
        # 5. SEO评分
        seo_score = self._score_seo(article)
        dimensions.append(seo_score)
        total_score += seo_score.score * self.weights["seo"]
        
        # 6. 品牌一致性评分
        brand_score = self._score_brand(article, brand_guidelines or {})
        dimensions.append(brand_score)
        total_score += brand_score.score * self.weights["brand"]
        
        # 汇总
        return {
            "total_score": round(total_score, 1),
            "dimensions": [self._dim_to_dict(d) for d in dimensions],
            "issues": self._collect_all_issues(dimensions),
            "suggestions": self._collect_all_suggestions(dimensions),
            "grade": self._get_grade(total_score),
            "pass": total_score >= 75
        }
    
    def _score_content(self, article: Dict) -> ScoreDimension:
        """评分内容质量"""
        issues = []
        suggestions = []
        score = 100
        
        content = article.get("content", "")
        title = article.get("title", "")
        
        # 检查字数
        word_count = len(content)
        if word_count < 800:
            issues.append(f"文章字数({word_count})较少，建议≥800字")
            score -= (800 - word_count) * 0.02
        elif word_count > 5000:
            issues.append(f"文章字数({word_count})过多，建议≤5000字")
            score -= (word_count - 5000) * 0.01
        
        # 检查是否有实质性内容
        if len(set(content)) < 100:
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
        if "引言" not in content and "导语" not in content and len(content) > 500:
            suggestions.append("建议添加引言/导语部分")
        
        # 检查结语
        if "总结" not in content and "结论" not in content and len(content) > 1000:
            suggestions.append("建议添加总结/结论部分")
        
        return ScoreDimension(
            name="内容质量",
            score=max(0, min(100, score)),
            weight=self.weights["content"],
            issues=issues,
            suggestions=suggestions
        )
    
    def _score_structure(self, article: Dict) -> ScoreDimension:
        """评分结构清晰度"""
        issues = []
        suggestions = []
        score = 100
        
        content = article.get("content", "")
        
        # 检查标题层级
        h2_count = content.count("\n## ")
        h3_count = content.count("\n### ")
        
        if h2_count == 0:
            issues.append("缺少二级标题")
            score -= 15
        
        if h2_count > 0 and h3_count == 0:
            suggestions.append("建议添加三级标题细分内容")
        
        if h2_count > 10:
            issues.append("二级标题过多，建议精简")
            score -= 10
        
        # 检查段落长度
        paragraphs = content.split("\n\n")
        long_paragraphs = sum(1 for p in paragraphs if len(p) > 300)
        if long_paragraphs > 3:
            issues.append(f"{long_paragraphs}个段落过长(>300字)")
            suggestions.append("长段落建议拆分")
            score -= long_paragraphs * 3
        
        # 检查列表使用
        if content.count("\n- ") + content.count("\n1. ") < 2:
            suggestions.append("建议使用列表增强可读性")
        
        return ScoreDimension(
            name="结构清晰度",
            score=max(0, min(100, score)),
            weight=self.weights["structure"],
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
        
        return ScoreDimension(
            name="逻辑连贯性",
            score=max(0, min(100, score)),
            weight=self.weights["logic"],
            issues=issues,
            suggestions=suggestions
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
            name="语言表达",
            score=max(0, min(100, score)),
            weight=self.weights["language"],
            issues=issues,
            suggestions=suggestions
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
            density = content.count(primary_keyword) / max(len(content), 1) * 100
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
            name="SEO优化",
            score=max(0, min(100, score)),
            weight=self.weights["seo"],
            issues=issues,
            suggestions=suggestions
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
        
        # 检查禁用词
        for word in forbidden_words:
            if word in content:
                issues.append(f"使用了禁用词「{word}」")
                score -= 20
        
        # 检查是否使用正确人称
        preferred_person = brand_guidelines.get("preferred_person", "third")
        if preferred_person == "first" and "我们" not in content:
            suggestions.append("建议使用第一人称「我们」")
        elif preferred_person == "third" and "我们" in content:
            suggestions.append("建议使用第三人称")
        
        return ScoreDimension(
            name="品牌一致性",
            score=max(0, min(100, score)),
            weight=self.weights["brand"],
            issues=issues,
            suggestions=suggestions
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
    def quality_scorer_tool(article_json: str, brand_guidelines_json: str = "{}") -> str:
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
        
        scorer = QualityScorer()
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
