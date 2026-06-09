#!/usr/bin/env python3
"""
Schema标记生成工具 - SEOAgent
生成结构化数据Schema标记（JSON-LD）
"""

import json
import os
import re
from typing import Dict, List, Any, Optional
from datetime import datetime
from urllib.parse import urlparse

import yaml


class SchemaGenerator:
    """Schema标记生成工具"""
    
    def __init__(self, config_path: str = "agents/seo_agent/config.yaml", brand_path: str = "config/brand_guidelines.yaml"):
        self.config_path = config_path
        self.brand_path = brand_path
        self.config = self._load_config()
        self.brand_name = self._load_brand_name() or "TechAI Insight"
        self.schema_types = {
            "article": "Article",
            "news": "NewsArticle",
            "blog": "BlogPosting",
            "faq": "FAQPage",
            "howto": "HowTo",
            "organization": "Organization",
            "breadcrumb": "BreadcrumbList"
        }

    def _deep_env_resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            if value.startswith("${") and value.endswith("}"):
                key = value[2:-1]
                return os.environ.get(key, "")
            return value
        if isinstance(value, dict):
            return {k: self._deep_env_resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._deep_env_resolve(v) for v in value]
        return value

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path or not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return self._deep_env_resolve(raw)

    def _load_brand_name(self) -> str:
        if not self.brand_path or not os.path.exists(self.brand_path):
            return ""
        with open(self.brand_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw = self._deep_env_resolve(raw)
        if isinstance(raw, dict):
            return str(raw.get("brand_name") or "").strip()
        return ""
    
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
        schema_type = (schema_type or "article").strip().lower()
        if schema_type in {"article", "blog", "news"}:
            schema_class = self.schema_types.get(schema_type, "Article")
            return self._generate_article_schema(article, schema_class=schema_class)
        elif schema_type == "faq":
            return self._generate_faq_schema(article)
        elif schema_type == "howto":
            return self._generate_howto_schema(article)
        elif schema_type == "breadcrumb":
            return self._generate_breadcrumb_schema(article)
        else:
            return self._generate_article_schema(article, schema_class="Article")
    
    def _generate_article_schema(self, article: Dict, *, schema_class: str = "Article") -> Dict:
        """生成Article Schema"""
        meta = (article or {}).get("meta") or {}
        publisher_name = article.get("publisher") or meta.get("publisher") or self.brand_name
        author_name = article.get("author") or meta.get("author") or publisher_name
        logo_url = article.get("logo_url") or meta.get("logo_url") or ""
        url = article.get("url") or meta.get("url") or ""
        featured_image = article.get("featured_image") or article.get("featured_image_url") or meta.get("featured_image_url") or ""
        description = (
            article.get("meta_description")
            or meta.get("meta_description")
            or meta.get("seo_description")
            or article.get("description")
            or ""
        )
        schema = {
            "@context": "https://schema.org",
            "@type": schema_class,
            "headline": article.get("title", ""),
            "description": description,
            "image": featured_image,
            "author": {
                "@type": "Organization",
                "name": author_name
            },
            "publisher": {
                "@type": "Organization",
                "name": publisher_name,
                "logo": {
                    "@type": "ImageObject",
                    "url": logo_url
                }
            },
            "datePublished": article.get("published_date", ""),
            "dateModified": article.get("modified_date", ""),
            "mainEntityOfPage": {
                "@type": "WebPage",
                "@id": url
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
        
        schema_type = schema.get("@type", "")

        if schema_type in {"Article", "BlogPosting", "NewsArticle"}:
            required = ["headline", "author", "publisher", "datePublished", "mainEntityOfPage"]
            for field in required:
                if not schema.get(field):
                    errors.append(f"缺少必需字段: {field}")

            author = schema.get("author") if isinstance(schema.get("author"), dict) else {}
            publisher = schema.get("publisher") if isinstance(schema.get("publisher"), dict) else {}
            if not (author or {}).get("name"):
                errors.append("缺少必需字段: author.name")
            if not (publisher or {}).get("name"):
                errors.append("缺少必需字段: publisher.name")
            logo = (publisher or {}).get("logo") if isinstance((publisher or {}).get("logo"), dict) else {}
            if not (logo or {}).get("url"):
                errors.append("缺少必需字段: publisher.logo.url")
            elif not self._is_absolute_url(str((logo or {}).get("url"))):
                errors.append(f"publisher.logo.url不是绝对URL: {(logo or {}).get('url')}")
            main = schema.get("mainEntityOfPage") if isinstance(schema.get("mainEntityOfPage"), dict) else {}
            if not main.get("@id"):
                errors.append("缺少必需字段: mainEntityOfPage.@id")
            if not schema.get("image"):
                errors.append("缺少必需字段: image")
        
        elif schema_type == "FAQPage":
            if not schema.get("mainEntity"):
                errors.append("FAQPage缺少mainEntity字段")
        
        elif schema_type == "HowTo":
            if not schema.get("step"):
                errors.append("HowTo缺少step字段")
        
        url_fields = []
        if schema.get("image"):
            url_fields.append(("image", schema.get("image")))
        main = schema.get("mainEntityOfPage") if isinstance(schema.get("mainEntityOfPage"), dict) else {}
        if main.get("@id"):
            url_fields.append(("mainEntityOfPage.@id", main.get("@id")))
        if schema.get("url"):
            url_fields.append(("url", schema.get("url")))

        for name, value in url_fields:
            if not value:
                errors.append(f"缺少必需字段: {name}")
                continue
            if isinstance(value, list):
                for v in value:
                    if not self._is_absolute_url(str(v)):
                        errors.append(f"{name}不是绝对URL: {v}")
                continue
            if not self._is_absolute_url(str(value)):
                errors.append(f"{name}不是绝对URL: {value}")
        
        # 检查日期格式
        for date_field in ["datePublished", "dateModified"]:
            date_value = schema.get(date_field)
            if date_value:
                try:
                    datetime.fromisoformat(date_value.replace('Z', '+00:00'))
                except Exception:
                    errors.append(f"{date_field}日期格式不正确: {date_value}")
            elif date_field == "datePublished" and schema_type in {"Article", "BlogPosting", "NewsArticle"}:
                errors.append("缺少必需字段: datePublished")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }

    def _is_absolute_url(self, value: str) -> bool:
        v = (value or "").strip()
        if not v:
            return False
        p = urlparse(v)
        return p.scheme in {"http", "https"} and bool(p.netloc)


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
            schema_type: Schema类型，可选 article/blog/news/faq/howto/breadcrumb
            
        Returns:
            JSON格式的Schema数据
        """
        try:
            article = json.loads(article_json)
        except Exception as e:
            return json.dumps({"success": False, "error": f"invalid_article_json: {e}"}, ensure_ascii=False, indent=2)
        if not isinstance(article, dict):
            return json.dumps({"success": False, "error": "article_json_must_be_object"}, ensure_ascii=False, indent=2)
        
        generator = SchemaGenerator()
        schema = generator.generate(article, schema_type)
        
        result = {
            "success": True,
            "schema_json": schema,
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
