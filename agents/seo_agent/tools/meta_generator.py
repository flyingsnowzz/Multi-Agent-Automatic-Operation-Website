#!/usr/bin/env python3
"""
Meta标签生成工具 - SEOAgent
生成优化的Meta Title和Description
"""

import json
import os
import re
import html
from typing import Dict, List, Any, Optional

import yaml


class MetaGenerator:
    """Meta标签生成工具"""
    
    def __init__(self, config_path: str = "agents/seo_agent/config.yaml", brand_path: str = "config/brand_guidelines.yaml"):
        # 标题模板
        self.title_templates = [
            "{keyword}：{modifier}{value_proposition}",
            "{value_proposition} - {keyword}完整指南",
            "{modifier}{keyword}最全解读",
            "{keyword}攻略|{modifier}必看",
        ]
        self.config_path = config_path
        self.brand_path = brand_path
        self.config = self._load_config()
        self.brand_name = self._load_brand_name() or "TechAI Insight"

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
        meta_cfg = (self.config or {}).get("meta") if isinstance(self.config, dict) else {}
        title_cfg = (meta_cfg.get("title") or {}) if isinstance(meta_cfg, dict) else {}
        min_len = int(title_cfg.get("min_length", 30) or 30)
        max_len = int(title_cfg.get("max_length", 60) or 60)
        include_brand = bool(title_cfg.get("include_brand", True))
        brand_position = str(title_cfg.get("brand_position", "suffix") or "suffix")
        brand_format = str(title_cfg.get("brand_format", "| {brand}") or "| {brand}")

        # 如果文章标题合适，直接使用
        base_title = article_title.strip()
        if base_title and len(base_title) <= max_len and (keyword in base_title or language != "chinese"):
            out = base_title
            if include_brand and self.brand_name and self.brand_name not in out:
                out = self._apply_brand(out, brand_position, brand_format)
            if len(out) > max_len:
                out = out[: max_len - 3] + "..."
            return out
        
        # 提取价值主张
        value_proposition = self._extract_value_proposition(article_title, keyword, secondary)
        
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

        if include_brand and self.brand_name and self.brand_name not in meta_title:
            meta_title = self._apply_brand(meta_title, brand_position, brand_format)

        if len(meta_title) < min_len:
            meta_title = meta_title + f" | {self.brand_name}" if include_brand and self.brand_name and self.brand_name not in meta_title else meta_title

        if len(meta_title) > max_len:
            meta_title = meta_title[: max_len - 3] + "..."
        
        return meta_title
    
    def _generate_description(self, content: str, keyword: str, secondary: List[str], language: str) -> str:
        """生成Meta Description"""
        meta_cfg = (self.config or {}).get("meta") if isinstance(self.config, dict) else {}
        desc_cfg = (meta_cfg.get("description") or {}) if isinstance(meta_cfg, dict) else {}
        min_len = int(desc_cfg.get("min_length", 120) or 120)
        max_len = int(desc_cfg.get("max_length", 160) or 160)
        include_keyword = bool(desc_cfg.get("include_keyword", True))

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
        if include_keyword and keyword and keyword not in description:
            description = f"{keyword}：{description}" if language == "chinese" else f"{keyword} - {description}"
        
        # 限制在150-160字符
        if len(description) > max_len:
            description = description[: max_len - 3] + "..."
        elif len(description) < min_len:
            # 如果太短，添加更多信息
            description = description + "。了解更多关于" + keyword + "的内容。"
        
        return description
    
    def _extract_value_proposition(self, title: str, keyword: str, secondary: List[str]) -> str:
        """从标题中提取价值主张"""
        clean_title = str(title or "")
        remove_list = [keyword] + list(secondary or [])
        for kw in remove_list:
            if kw:
                clean_title = clean_title.replace(kw, " ")
        clean_title = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", clean_title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()
        
        # 提取有意义的词
        words = re.findall(r"[\u4e00-\u9fff]{2,}|\d{4}", clean_title)
        out = "".join(words[:3]).strip()
        return out[:12]

    def _apply_brand(self, base: str, position: str, fmt: str) -> str:
        brand = self.brand_name
        if not brand:
            return base
        frag = fmt.format(brand=brand)
        if position == "prefix":
            return f"{frag}{base}"
        return f"{base}{frag}"
    
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
        meta_cfg = (self.config or {}).get("meta") if isinstance(self.config, dict) else {}
        og_cfg = (meta_cfg.get("og") or {}) if isinstance(meta_cfg, dict) else {}
        og_type = str(og_cfg.get("type") or "article")
        return {
            "og:title": title,
            "og:description": description,
            "og:type": og_type,
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

        meta_cfg = (self.config or {}).get("meta") if isinstance(self.config, dict) else {}
        title_cfg = (meta_cfg.get("title") or {}) if isinstance(meta_cfg, dict) else {}
        desc_cfg = (meta_cfg.get("description") or {}) if isinstance(meta_cfg, dict) else {}
        t_min = int(title_cfg.get("min_length", 30) or 30)
        t_max = int(title_cfg.get("max_length", 60) or 60)
        d_min = int(desc_cfg.get("min_length", 120) or 120)
        d_max = int(desc_cfg.get("max_length", 160) or 160)

        if len(title) < t_min:
            warnings.append(f"Title过短，建议≥{t_min}字符")
        elif len(title) > t_max:
            warnings.append(f"Title过长，可能被截断，建议≤{t_max}字符")

        if len(description) < d_min:
            warnings.append(f"Description过短，建议≥{d_min}字符")
        elif len(description) > d_max:
            warnings.append(f"Description过长，可能被截断，建议≤{d_max}字符")
        
        return warnings
    
    def generate_html(self, meta: Dict) -> str:
        """生成HTML Meta标签"""
        html_parts = []
        
        # Title
        html_parts.append(f'<title>{html.escape(str(meta.get("meta_title") or ""), quote=False)}</title>')
        
        # Meta Description
        html_parts.append(f'<meta name="description" content="{html.escape(str(meta.get("meta_description") or ""), quote=True)}">')
        
        # Keywords (可选，已不太重要)
        # html_parts.append(f'<meta name="keywords" content="{",".join(keywords)}">')
        
        # Open Graph
        for name, content in meta.get("og_tags", {}).items():
            if content:  # 只输出有内容的
                html_parts.append(f'<meta property="{html.escape(str(name), quote=True)}" content="{html.escape(str(content), quote=True)}">')
        
        # Twitter
        for name, content in meta.get("twitter_tags", {}).items():
            if content:
                html_parts.append(f'<meta name="{html.escape(str(name), quote=True)}" content="{html.escape(str(content), quote=True)}">')
        
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
