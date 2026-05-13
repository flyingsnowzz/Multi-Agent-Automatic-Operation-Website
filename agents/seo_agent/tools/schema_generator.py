#!/usr/bin/env python3
"""
Schema标记生成工具 - SEOAgent
生成结构化数据Schema标记（JSON-LD）
"""

import json
import re
from typing import Dict, List, Any, Optional
from datetime import datetime


class SchemaGenerator:
    """Schema标记生成工具"""
    
    def __init__(self):
        self.schema_types = {
            "article": "Article",
            "news": "NewsArticle",
            "blog": "BlogPosting",
            "faq": "FAQPage",
            "howto": "HowTo",
            "organization": "Organization",
            "breadcrumb": "BreadcrumbList"
        }
    
    def generate(
        self,
        article: Dict[str, Any],
        schema_type: str = "article"
    ) -> Dict[str, Any]:
        """
        生成Schema标记
        
        Args:
            article: 文章信息
            schema_type: Schema类型
            
        Returns:
            Schema标记数据
        """
        if schema_type == "article":
            return self._generate_article_schema(article)
        elif schema_type == "faq":
            return self._generate_faq_schema(article)
        elif schema_type == "howto":
            return self._generate_howto_schema(article)
        elif schema_type == "breadcrumb":
            return self._generate_breadcrumb_schema(article)
        else:
            return self._generate_article_schema(article)
    
    def _generate_article_schema(self, article: Dict) -> Dict:
        """生成Article Schema"""
        schema = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": article.get("title", ""),
            "description": article.get("meta_description", ""),
            "image": article.get("featured_image", ""),
            "author": {
                "@type": "Organization",
                "name": article.get("author", "商学院")
            },
            "publisher": {
                "@type": "Organization",
                "name": article.get("publisher", "商学院"),
                "logo": {
                    "@type": "ImageObject",
                    "url": article.get("logo_url", "")
                }
            },
            "datePublished": article.get("published_date", ""),
            "dateModified": article.get("modified_date", ""),
            "mainEntityOfPage": {
                "@type": "WebPage",
                "@id": article.get("url", "")
            },
            "articleSection": article.get("category", ""),
            "wordCount": article.get("word_count", 0)
        }
        
        # 添加关键词
        if article.get("keywords"):
            schema["keywords"] = ", ".join(article["keywords"])
        
        return schema
    
    def _generate_faq_schema(self, article: Dict) -> Dict:
        """生成FAQ Schema"""
        faq_items = article.get("faq_items", [])
        
        mainEntity = []
        for item in faq_items:
            mainEntity.append({
                "@type": "Question",
                "name": item.get("question", ""),
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": item.get("answer", "")
                }
            })
        
        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": mainEntity
        }
        
        return schema
    
    def _generate_howto_schema(self, article: Dict) -> Dict:
        """生成HowTo Schema"""
        steps = article.get("steps", [])
        
        howto_steps = []
        for i, step in enumerate(steps):
            howto_steps.append({
                "@type": "HowToStep",
                "name": step.get("title", f"步骤{i+1}"),
                "text": step.get("description", ""),
                "position": i + 1
            })
        
        schema = {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": article.get("title", ""),
            "description": article.get("description", ""),
            "image": article.get("featured_image", ""),
            "totalTime": f"PT{article.get('duration_minutes', 30)}M",
            "step": howto_steps,
            "supply": [
                {"@type": "HowToSupply", "name": item}
                for item in article.get("supplies", [])
            ],
            "tool": [
                {"@type": "HowToTool", "name": item}
                for item in article.get("tools", [])
            ]
        }
        
        return schema
    
    def _generate_breadcrumb_schema(self, article: Dict) -> Dict:
        """生成BreadcrumbList Schema"""
        items = article.get("breadcrumb_items", [])
        
        breadcrumb_list = []
        for i, item in enumerate(items):
            breadcrumb_list.append({
                "@type": "ListItem",
                "position": i + 1,
                "name": item.get("name", ""),
                "item": item.get("url", "")
            })
        
        schema = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": breadcrumb_list
        }
        
        return schema
    
    def generate_html(self, schema: Dict) -> str:
        """生成HTML中的JSON-LD脚本标签"""
        html = f'<script type="application/ld+json">\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n</script>'
        return html
    
    def generate_multiple(self, article: Dict) -> str:
        """
        生成多个Schema（Article + Breadcrumb等）
        
        Returns:
            HTML脚本标签
        """
        schemas = []
        
        # Article Schema
        schemas.append(self._generate_article_schema(article))
        
        # Breadcrumb（如果有）
        if article.get("breadcrumb_items"):
            schemas.append(self._generate_breadcrumb_schema(article))
        
        # FAQ（如果有）
        if article.get("faq_items"):
            schemas.append(self._generate_faq_schema(article))
        
        # 生成多个脚本标签
        html_parts = []
        for schema in schemas:
            html_parts.append(self.generate_html(schema))
        
        return "\n".join(html_parts)
    
    def validate_schema(self, schema: Dict) -> Dict[str, Any]:
        """
        验证Schema
        
        Args:
            schema: Schema数据
            
        Returns:
            验证结果
        """
        errors = []
        warnings = []
        
        # 检查必需字段
        schema_type = schema.get("@type", "")
        
        if schema_type == "Article":
            required = ["headline", "author", "publisher", "datePublished"]
            for field in required:
                if not schema.get(field):
                    errors.append(f"缺少必需字段: {field}")
        
        elif schema_type == "FAQPage":
            if not schema.get("mainEntity"):
                errors.append("FAQPage缺少mainEntity字段")
        
        elif schema_type == "HowTo":
            if not schema.get("step"):
                errors.append("HowTo缺少step字段")
        
        # 检查URL格式
        for field in ["url", "image", "@id"]:
            value = schema.get("mainEntityOfPage", {}).get(field) if field == "@id" else schema.get(field)
            if value and not value.startswith(("http://", "https://", "#")):
                warnings.append(f"{field}可能不是有效的URL: {value}")
        
        # 检查日期格式
        for date_field in ["datePublished", "dateModified"]:
            date_value = schema.get(date_field)
            if date_value:
                try:
                    datetime.fromisoformat(date_value.replace('Z', '+00:00'))
                except:
                    warnings.append(f"{date_field}日期格式可能不正确: {date_value}")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }


# CrewAI Tool 包装
def get_schema_generator_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("schema_generator")
    def schema_generator_tool(article_json: str, schema_type: str = "article") -> str:
        """
        生成结构化数据Schema标记（JSON-LD）。
        
        Args:
            article_json: 文章信息的JSON字符串
            schema_type: Schema类型，可选 article/faq/howto/breadcrumb
            
        Returns:
            JSON格式的Schema数据
        """
        article = json.loads(article_json)
        
        generator = SchemaGenerator()
        schema = generator.generate(article, schema_type)
        
        result = {
            "schema": schema,
            "html": generator.generate_html(schema),
            "validation": generator.validate_schema(schema)
        }
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    return schema_generator_tool


if __name__ == "__main__":
    # 测试
    generator = SchemaGenerator()
    
    # Article Schema测试
    article = {
        "title": "EMBA项目选择完整指南",
        "meta_description": "本文为您详细介绍如何选择适合自己的EMBA项目",
        "featured_image": "https://example.com/image.jpg",
        "author": "商学院",
        "publisher": "商学院",
        "url": "https://example.com/emba-guide",
        "published_date": "2024-01-15",
        "modified_date": "2024-01-20",
        "category": "EMBA",
        "keywords": ["EMBA", "商学院", "高管教育"],
        "word_count": 2500
    }
    
    result = generator.generate(article, "article")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n" + "="*60)
    print(generator.generate_html(result))
