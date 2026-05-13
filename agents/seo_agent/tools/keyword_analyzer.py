#!/usr/bin/env python3
"""
关键词分析工具 - SEOAgent
分析关键词密度、分布和LSI关键词
"""

import json
import re
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter


class KeywordAnalyzer:
    """关键词分析工具"""
    
    def __init__(self):
        self.stop_words = set([
            '的', '了', '和', '是', '在', '我', '有', '个', '人', '这',
            '不', '也', '就', '那', '你', '会', '对', '要', '来', '可以',
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'must', 'shall', 'can'
        ])
    
    def analyze(
        self,
        content: str,
        primary_keyword: str,
        secondary_keywords: Optional[List[str]] = None,
        language: str = "chinese"
    ) -> Dict[str, Any]:
        """
        分析关键词
        
        Args:
            content: 文章内容
            primary_keyword: 主关键词
            secondary_keywords: 次关键词列表
            language: 语言 chinese/english
            
        Returns:
            分析结果
        """
        if secondary_keywords is None:
            secondary_keywords = []
        
        if language == "chinese":
            return self._analyze_chinese(content, primary_keyword, secondary_keywords)
        else:
            return self._analyze_english(content, primary_keyword, secondary_keywords)
    
    def _analyze_chinese(self, content: str, primary: str, secondary: List[str]) -> Dict:
        """分析中文关键词"""
        # 清理内容
        clean_content = self._clean_text(content)
        
        # 计算密度
        primary_density = self._calculate_density_chinese(clean_content, primary)
        
        secondary_densities = {}
        for kw in secondary:
            secondary_densities[kw] = self._calculate_density_chinese(clean_content, kw)
        
        # 分析分布
        distribution = self._analyze_distribution_chinese(clean_content, primary)
        
        # 提取关键词
        extracted_keywords = self._extract_keywords_chinese(clean_content, top_n=20)
        
        # 提取LSI关键词
        lsi_keywords = self._extract_lsi_keywords(extracted_keywords, primary, top_n=10)
        
        # 评估和建议
        assessment = self._assess_keyword_optimization(primary_density, distribution, primary)
        
        return {
            "language": "chinese",
            "primary_keyword": primary,
            "primary_density": round(primary_density, 2),
            "secondary_densities": {k: round(v, 2) for k, v in secondary_densities.items()},
            "distribution": distribution,
            "extracted_keywords": extracted_keywords,
            "lsi_keywords": lsi_keywords,
            "assessment": assessment
        }
    
    def _analyze_english(self, content: str, primary: str, secondary: List[str]) -> Dict:
        """分析英文关键词"""
        words = self._extract_english_words(content)
        total_words = len(words)
        
        if total_words == 0:
            return {"error": "No content to analyze"}
        
        # 计算密度
        primary_lower = primary.lower()
        primary_count = sum(1 for w in words if w.lower() == primary_lower)
        primary_density = (primary_count / total_words) * 100
        
        secondary_densities = {}
        for kw in secondary:
            kw_lower = kw.lower()
            count = sum(1 for w in words if w.lower() == kw_lower)
            secondary_densities[kw] = (count / total_words) * 100
        
        # 分布分析
        distribution = self._analyze_distribution_english(content, primary)
        
        # 提取关键词
        extracted_keywords = self._extract_keywords_english(words, top_n=20)
        
        # LSI关键词
        lsi_keywords = self._extract_lsi_keywords(extracted_keywords, primary, top_n=10)
        
        # 评估
        assessment = self._assess_keyword_optimization(primary_density, distribution, primary)
        
        return {
            "language": "english",
            "total_words": total_words,
            "primary_keyword": primary,
            "primary_density": round(primary_density, 2),
            "primary_count": primary_count,
            "secondary_densities": {k: round(v, 2) for k, v in secondary_densities.items()},
            "distribution": distribution,
            "extracted_keywords": extracted_keywords,
            "lsi_keywords": lsi_keywords,
            "assessment": assessment
        }
    
    def _clean_text(self, text: str) -> str:
        """清理文本"""
        # 移除HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        # 移除代码块
        text = re.sub(r'```[\s\S]*?```', '', text)
        # 移除链接
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # 移除特殊字符
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)
        return text
    
    def _calculate_density_chinese(self, content: str, keyword: str) -> float:
        """计算中文关键词密度"""
        if not keyword:
            return 0
        
        # 中文字符总数
        chinese_chars = [c for c in content if '\u4e00' <= c <= '\u9fff']
        total_chars = len(chinese_chars)
        
        if total_chars == 0:
            return 0
        
        # 关键词出现次数
        keyword_len = len(keyword)
        count = content.count(keyword)
        
        # 密度 = (关键词字数 × 出现次数) / 总字数 × 100
        density = (keyword_len * count) / total_chars * 100
        return density
    
    def _analyze_distribution_chinese(self, content: str, keyword: str) -> Dict:
        """分析中文关键词分布"""
        sections = self._split_content_sections(content)
        
        distribution = {
            "title": keyword in content[:100],
            "first_paragraph": keyword in content[:500] if len(content) > 500 else keyword in content,
            "headings": [],
            "sections": {}
        }
        
        # 检查标题中
        headings = re.findall(r'#{1,6}\s+([^\n]+)', content)
        distribution["headings"] = [h for h in headings if keyword in h]
        
        # 检查各部分
        for i, section in enumerate(sections):
            section_keyword_count = section.count(keyword)
            distribution["sections"][f"section_{i+1}"] = {
                "has_keyword": section_keyword_count > 0,
                "count": section_keyword_count
            }
        
        # 检查结尾
        last_section = sections[-1] if sections else ""
        distribution["last_section"] = keyword in last_section if last_section else False
        
        return distribution
    
    def _analyze_distribution_english(self, content: str, keyword: str) -> Dict:
        """分析英文关键词分布"""
        keyword_lower = keyword.lower()
        content_lower = content.lower()
        
        paragraphs = content.split("\n\n")
        
        distribution = {
            "title": keyword_lower in content[:100].lower(),
            "first_paragraph": keyword_lower in content[:500].lower() if len(content) > 500 else keyword_lower in content_lower,
            "headings": [],
            "sections": {}
        }
        
        # 检查标题
        headings = re.findall(r'#{1,6}\s+([^\n]+)', content)
        distribution["headings"] = [h for h in headings if keyword_lower in h.lower()]
        
        # 检查各段落
        for i, para in enumerate(paragraphs):
            para_lower = para.lower()
            count = para_lower.count(keyword_lower)
            distribution["sections"][f"paragraph_{i+1}"] = {
                "has_keyword": count > 0,
                "count": count
            }
        
        # 检查结尾
        last_para = paragraphs[-1] if paragraphs else ""
        distribution["last_section"] = keyword_lower in last_para.lower() if last_para else False
        
        return distribution
    
    def _split_content_sections(self, content: str) -> List[str]:
        """分割内容为章节"""
        sections = []
        
        # 按标题分割
        parts = re.split(r'\n#{1,6}\s+', content)
        
        # 第一个部分是引言
        if parts:
            sections.append(parts[0])
        
        # 其余部分是各章节
        for part in parts[1:]:
            # 获取章节标题和内容
            match = re.match(r'([^\n]+)\n([\s\S]+)', part)
            if match:
                sections.append(match.group(2))
            else:
                sections.append(part)
        
        return [s for s in sections if s.strip()]
    
    def _extract_keywords_chinese(self, content: str, top_n: int = 20) -> List[str]:
        """提取中文关键词"""
        # 提取所有中文词
        words = []
        current_word = ""
        
        for char in content:
            if '\u4e00' <= char <= '\u9fff':
                current_word += char
            else:
                if len(current_word) >= 2 and current_word not in self.stop_words:
                    words.append(current_word)
                current_word = ""
        
        # 最后一个词
        if len(current_word) >= 2 and current_word not in self.stop_words:
            words.append(current_word)
        
        # 统计频率
        counter = Counter(words)
        return [word for word, count in counter.most_common(top_n)]
    
    def _extract_english_words(self, content: str) -> List[str]:
        """提取英文单词"""
        # 转小写并分词
        words = re.findall(r'[a-zA-Z]+', content.lower())
        # 过滤停用词
        return [w for w in words if w not in self.stop_words and len(w) > 2]
    
    def _extract_keywords_english(self, words: List[str], top_n: int = 20) -> List[str]:
        """提取英文关键词"""
        counter = Counter(words)
        return [word for word, count in counter.most_common(top_n)]
    
    def _extract_lsi_keywords(self, extracted: List[str], primary: str, top_n: int = 10) -> List[str]:
        """
        提取LSI关键词（潜在语义索引关键词）
        
        简化实现：选择与主关键词共现频率高的词
        """
        # 简化：直接返回高频词（排除主关键词）
        lsi = [kw for kw in extracted if primary not in kw][:top_n]
        return lsi
    
    def _assess_keyword_optimization(self, density: float, distribution: Dict, keyword: str) -> Dict:
        """评估关键词优化情况"""
        issues = []
        suggestions = []
        score = 100
        
        # 密度评估
        if density < 0.5:
            issues.append(f"关键词密度({density:.1f}%)过低")
            score -= 20
            suggestions.append("建议增加关键词出现次数")
        elif density > 3:
            issues.append(f"关键词密度({density:.1f}%)过高，可能被判定为堆砌")
            score -= 30
            suggestions.append("建议减少关键词出现次数")
        elif density < 1:
            suggestions.append("关键词密度可以适当提高")
        elif density > 2:
            suggestions.append("关键词密度偏高，建议适当降低")
        else:
            issues.append("关键词密度适中")
        
        # 分布评估
        if not distribution.get("title"):
            issues.append("关键词未出现在标题中")
            score -= 15
            suggestions.append("将关键词添加到标题开头")
        
        if not distribution.get("first_paragraph"):
            issues.append("关键词未出现在首段")
            score -= 10
            suggestions.append("在文章开头段落加入关键词")
        
        if not distribution.get("headings"):
            suggestions.append("考虑将关键词加入小标题")
        
        if not distribution.get("last_section"):
            suggestions.append("建议在结尾段落再次提及关键词")
        
        return {
            "score": max(0, score),
            "issues": issues,
            "suggestions": suggestions
        }


# CrewAI Tool 包装
def get_keyword_analyzer_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("keyword_analyzer")
    def keyword_analyzer_tool(
        content: str,
        primary_keyword: str,
        secondary_keywords: str = "",
        language: str = "chinese"
    ) -> str:
        """
        分析文章中关键词的密度和分布。
        
        Args:
            content: 文章内容
            primary_keyword: 主关键词
            secondary_keywords: 次关键词列表，用逗号分隔
            language: 语言类型 chinese/english
            
        Returns:
            JSON格式的分析结果
        """
        secondary = [k.strip() for k in secondary_keywords.split(',') if k.strip()]
        
        analyzer = KeywordAnalyzer()
        result = analyzer.analyze(content, primary_keyword, secondary, language)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    return keyword_analyzer_tool


if __name__ == "__main__":
    # 测试
    analyzer = KeywordAnalyzer()
    
    content = """
    EMBA项目是高级管理人员的首选教育方式。选择EMBA项目时，需要考虑多个因素。
    
    ## EMBA选择标准
    
    首先是学校的排名和声誉。顶尖商学院的EMBA项目更有价值。
    
    ## 师资力量
    
    优秀的师资是EMBA项目的核心。
    
    ## 总结
    
    选择EMBA项目需要综合考虑多方面因素。
    """
    
    result = analyzer.analyze(content, "EMBA", ["选择", "商学院"], "chinese")
    print(json.dumps(result, ensure_ascii=False, indent=2))
