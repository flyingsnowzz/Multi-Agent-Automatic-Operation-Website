#!/usr/bin/env python3
"""
Meta标签生成工具 - SEOAgent
生成优化的Meta Title和Description
"""

import json
import re
from typing import Dict, List, Any, Optional


class MetaGenerator:
    """Meta标签生成工具"""
    
    def __init__(self):
        # 标题模板
        self.title_templates = [
            "{keyword}：{modifier}{value_proposition}",
            "{value_proposition} - {keyword}完整指南",
            "{modifier}{keyword}最全解读",
            "{keyword}攻略|{modifier}必看",
        ]
        
        # 品牌词
        self.brand_name = "商学院"
    
    def generate(
        self,
        title: str,
        content: str,
        primary_keyword: str,
        secondary_keywords: Optional[List[str]] = None,
        language: str = "chinese"
    ) -> Dict[str, Any]:
        """
        生成Meta标签
        
        Args:
            title: 文章标题
            content: 文章内容
            primary_keyword: 主关键词
            secondary_keywords: 次关键词列表
            language: 语言
            
        Returns:
            生成的Meta标签
        """
        if secondary_keywords is None:
            secondary_keywords = []
        
        # 生成Meta Title
        meta_title = self._generate_title(title, primary_keyword, secondary_keywords, language)
        
        # 生成Meta Description
        meta_description = self._generate_description(content, primary_keyword, secondary_keywords, language)
        
        # 生成Open Graph标签
        og_tags = self._generate_og_tags(meta_title, meta_description)
        
        # 生成Twitter Card标签
        twitter_tags = self._generate_twitter_tags(meta_title, meta_description)
        
        return {
            "meta_title": meta_title,
            "meta_description": meta_description,
            "title_length": len(meta_title),
            "description_length": len(meta_description),
            "og_tags": og_tags,
            "twitter_tags": twitter_tags,
            "warnings": self._check_warnings(meta_title, meta_description)
        }
    
    def _generate_title(self, article_title: str, keyword: str, secondary: List[str], language: str) -> str:
        """生成Meta Title"""
        # 如果文章标题合适，直接使用
        if len(article_title) <= 30 and keyword in article_title:
            return f"{article_title}|{self.brand_name}"
        
        # 提取价值主张
        value_proposition = self._extract_value_proposition(article_title, secondary)
        
        # 使用模板生成
        modifiers = ["完整指南", "全面解析", "必读攻略", "一文读懂"]
        
        if language == "chinese":
            template = self.title_templates[0]
            modifier = modifiers[0] if not value_proposition else ""
            meta_title = template.format(
                keyword=keyword,
                modifier=modifier,
                value_proposition=value_proposition
            )
        else:
            meta_title = f"{keyword}: Complete Guide | {self.brand_name}"
        
        # 确保不超过60字符
        if len(meta_title) > 60:
            meta_title = meta_title[:57] + "..."
        
        return meta_title
    
    def _generate_description(self, content: str, keyword: str, secondary: List[str], language: str) -> str:
        """生成Meta Description"""
        # 提取前两段
        paragraphs = content.split("\n\n")
        first_content = ""
        
        for para in paragraphs:
            # 跳过标题行
            if para.startswith("#"):
                continue
            # 跳过空段落
            if not para.strip():
                continue
            first_content = para
            break
        
        # 如果没有内容，使用标题
        if not first_content:
            first_content = content[:300]
        
        # 清理
        description = self._clean_description(first_content)
        
        # 确保包含关键词
        if keyword not in description and secondary:
            # 在开头添加关键词
            description = f"{keyword}：{description}"
        
        # 限制在150-160字符
        if len(description) > 160:
            description = description[:157] + "..."
        elif len(description) < 100:
            # 如果太短，添加更多信息
            description = description + "。了解更多关于" + keyword + "的内容。"
        
        return description
    
    def _extract_value_proposition(self, title: str, secondary: List[str]) -> str:
        """从标题中提取价值主张"""
        # 移除关键词
        clean_title = title
        for kw in [title] + secondary:
            clean_title = clean_title.replace(kw, "").strip()
        
        # 提取有意义的词
        words = re.findall(r'[\u4e00-\u9fff]+', clean_title)
        if words:
            return "".join(words[:3])
        
        return ""
    
    def _clean_description(self, text: str) -> str:
        """清理描述文本"""
        # 移除HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        
        # 移除Markdown格式
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        
        # 移除代码块
        text = re.sub(r'```[\s\S]*?```', '', text)
        
        # 移除多余空白
        text = re.sub(r'\s+', ' ', text).strip()
        
        # 移除开头的标题符号
        text = re.sub(r'^#{1,6}\s+', '', text)
        
        return text[:200]
    
    def _generate_og_tags(self, title: str, description: str) -> Dict[str, str]:
        """生成Open Graph标签"""
        return {
            "og:title": title,
            "og:description": description,
            "og:type": "article",
            "og:site_name": self.brand_name,
            "article:published_time": "",  # 需要填充
            "article:modified_time": "",   # 需要填充
            "article:section": "",         # 需要填充
        }
    
    def _generate_twitter_tags(self, title: str, description: str) -> Dict[str, str]:
        """生成Twitter Card标签"""
        return {
            "twitter:card": "summary_large_image",
            "twitter:title": title,
            "twitter:description": description,
        }
    
    def _check_warnings(self, title: str, description: str) -> List[str]:
        """检查警告"""
        warnings = []
        
        # Title检查
        if len(title) < 20:
            warnings.append("Title过短，建议30-60字符")
        elif len(title) > 60:
            warnings.append("Title过长，可能被截断，建议≤60字符")
        
        # Description检查
        if len(description) < 100:
            warnings.append("Description过短，建议150-160字符")
        elif len(description) > 160:
            warnings.append("Description过长，可能被截断，建议≤160字符")
        
        return warnings
    
    def generate_html(self, meta: Dict) -> str:
        """生成HTML Meta标签"""
        html_parts = []
        
        # Title
        html_parts.append(f'<title>{meta["meta_title"]}</title>')
        
        # Meta Description
        html_parts.append(f'<meta name="description" content="{meta["meta_description"]}">')
        
        # Keywords (可选，已不太重要)
        # html_parts.append(f'<meta name="keywords" content="{",".join(keywords)}">')
        
        # Open Graph
        for name, content in meta.get("og_tags", {}).items():
            if content:  # 只输出有内容的
                html_parts.append(f'<meta property="{name}" content="{content}">')
        
        # Twitter
        for name, content in meta.get("twitter_tags", {}).items():
            if content:
                html_parts.append(f'<meta name="{name}" content="{content}">')
        
        return "\n".join(html_parts)


# CrewAI Tool 包装
def get_meta_generator_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("meta_generator")
    def meta_generator_tool(
        title: str,
        content: str,
        primary_keyword: str,
        secondary_keywords: str = "",
        language: str = "chinese"
    ) -> str:
        """
        生成优化的Meta标签（Title、Description、Open Graph等）。
        
        Args:
            title: 文章标题
            content: 文章内容摘要（前500字）
            primary_keyword: 主关键词
            secondary_keywords: 次关键词，用逗号分隔
            language: 语言 chinese/english
            
        Returns:
            JSON格式的Meta标签
        """
        secondary = [k.strip() for k in secondary_keywords.split(',') if k.strip()]
        
        generator = MetaGenerator()
        result = generator.generate(title, content, primary_keyword, secondary, language)
        result["html"] = generator.generate_html(result)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    return meta_generator_tool


if __name__ == "__main__":
    # 测试
    generator = MetaGenerator()
    
    result = generator.generate(
        title="EMBA项目选择指南：如何选择适合自己的商学院",
        content="""
        EMBA（高级管理人员工商管理硕士）项目是许多企业中高层管理者提升管理能力的重要途径。
        
        选择EMBA项目时，需要考虑多个因素：学校排名、师资力量、课程设置、校友资源、学费等。
        
        本文将为您详细介绍如何选择适合自己的EMBA项目。
        """,
        primary_keyword="EMBA选择",
        secondary_keywords=["EMBA项目", "商学院"],
        language="chinese"
    )
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
