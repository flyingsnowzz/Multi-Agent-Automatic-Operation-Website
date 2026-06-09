#!/usr/bin/env python3
"""
Alt文本生成工具 - ImageAgent
为图片生成SEO友好的Alt文本描述
"""

import json
import re
import html
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional


class AltTextGenerator:
    """Alt文本生成工具"""
    
    def __init__(self):
        self.max_length = 125  # Google推荐125字符以内
    
    def generate(
        self,
        image_description: str,
        context: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        language: str = "chinese"
    ) -> Dict[str, Any]:
        """
        生成Alt文本
        
        Args:
            image_description: 图片内容描述
            context: 文章上下文
            keywords: 关键词列表
            language: 语言
            
        Returns:
            生成的Alt文本和建议
        """
        lang = (language or "chinese").strip().lower()
        detected = self._detect_language(image_description)
        if lang in {"auto", "detect"}:
            lang = detected
        if lang == "chinese":
            return self._generate_chinese(image_description, context, keywords)
        return self._generate_english(image_description, context, keywords)

    def _detect_language(self, text: str) -> str:
        if not text:
            return "chinese"
        has_zh = any("\u4e00" <= c <= "\u9fff" for c in text)
        if has_zh:
            return "chinese"
        return "english"
    
    def _generate_chinese(
        self,
        description: str,
        context: Optional[str],
        keywords: Optional[List[str]]
    ) -> Dict:
        """生成中文Alt文本"""
        # 提取关键词
        keyword = keywords[0] if keywords else ""
        
        # 构建Alt文本
        alt_parts = []
        
        if keyword:
            alt_parts.append(keyword)
        
        # 添加描述
        desc_short = self._shorten_description(description, max_words=12)
        if desc_short:
            if desc_short not in alt_parts:
                alt_parts.append(desc_short)
        
        # 添加上下文
        if context and len(context) < 50:
            c = context.strip()
            if c and c not in alt_parts:
                alt_parts.append(c)
        
        # 组合
        alt_text = "，".join(alt_parts) if alt_parts else (description.strip()[:50] if description else "")
        
        # 确保包含关键词
        if keyword and keyword not in alt_text:
            alt_text = f"{keyword}，{alt_text}" if alt_text else keyword
        
        # 截断过长文本
        if len(alt_text) > self.max_length:
            alt_text = alt_text[:self.max_length-3] + "..."
        
        # 生成标题
        title = self._generate_title(alt_text, keyword)
        
        # 检查
        warnings = self._check_alt_text(alt_text, keyword)
        
        return {
            "alt_text": alt_text,
            "title": title,
            "length": len(alt_text),
            "warnings": warnings,
            "suggestions": self._get_suggestions(alt_text, keyword, "chinese")
        }
    
    def _generate_english(
        self,
        description: str,
        context: Optional[str],
        keywords: Optional[List[str]]
    ) -> Dict:
        """生成英文Alt文本"""
        keyword = keywords[0] if keywords else ""
        
        # 构建Alt文本
        alt_parts = []
        
        if keyword and keyword.strip():
            alt_parts.append(keyword.strip())
        
        desc = (description or "").strip()
        if desc and desc.lower() not in [p.lower() for p in alt_parts]:
            alt_parts.append(desc)
        
        if context:
            c = context.strip()
            if c and c.lower() not in [p.lower() for p in alt_parts]:
                alt_parts.append(c)
        
        alt_text = ", ".join(alt_parts) if alt_parts else (description or "").strip()
        
        # 截断
        if len(alt_text) > self.max_length:
            alt_text = alt_text[:self.max_length-3] + "..."
        
        title = self._generate_title(alt_text, keyword)
        warnings = self._check_alt_text(alt_text, keyword)
        
        return {
            "alt_text": alt_text,
            "title": title,
            "length": len(alt_text),
            "warnings": warnings,
            "suggestions": self._get_suggestions(alt_text, keyword, "english")
        }
    
    def _shorten_description(self, description: str, max_words: int = 8) -> str:
        """缩短描述"""
        text = (description or "").strip()
        if not text:
            return ""
        has_zh = any("\u4e00" <= c <= "\u9fff" for c in text)
        if has_zh:
            words = re.findall(r"[\u4e00-\u9fff]+", text)
            selected = words[:max_words]
            return "".join(selected)
        words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text)
        selected = words[:max_words]
        return " ".join(selected)
    
    def _generate_title(self, alt_text: str, keyword: str) -> str:
        """生成图片标题（用于tooltip）"""
        # 标题可以比Alt长
        if len(alt_text) < 100:
            return alt_text
        
        # 截断
        return alt_text[:97] + "..."
    
    def _check_alt_text(self, alt_text: str, keyword: str) -> List[str]:
        """检查Alt文本"""
        warnings = []
        
        # 长度检查
        if len(alt_text) < 20:
            warnings.append("Alt文本过短，建议至少20字符")
        elif len(alt_text) > self.max_length:
            warnings.append(f"Alt文本过长({len(alt_text)}字符)，建议{self.max_length}字符以内")
        
        # 关键词检查
        if keyword and keyword not in alt_text:
            warnings.append("Alt文本中未包含主关键词")
        
        # 内容检查
        if alt_text.count("图片") > 0 or alt_text.count("image") > 0:
            warnings.append("避免使用「图片」等泛指词汇")
        
        # 重复检查
        if self._has_excessive_repetition(alt_text):
            warnings.append("Alt文本重复字符过多")
        
        return warnings

    def _has_excessive_repetition(self, alt_text: str) -> bool:
        text = (alt_text or "").strip()
        if len(text) < 20:
            return False
        if any("\u4e00" <= c <= "\u9fff" for c in text):
            if re.search(r"(.)\1{3,}", text):
                return True
            return False
        if re.search(r"\b([A-Za-z]{2,})(\s+\1){2,}\b", text, flags=re.IGNORECASE):
            return True
        words = re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(words) >= 10:
            uniq = len(set(words))
            if uniq / max(len(words), 1) < 0.45:
                return True
        return False
    
    def _get_suggestions(self, alt_text: str, keyword: str, language: str) -> List[str]:
        """获取建议"""
        suggestions = []
        
        # 关键词位置
        if keyword and alt_text.startswith(keyword):
            suggestions.append("✓ 关键词在开头，SEO效果最佳")
        elif keyword and keyword in alt_text[:30]:
            suggestions.append("✓ 关键词在前30字符内，效果良好")
        elif keyword:
            suggestions.append("建议将关键词放在Alt文本开头")
        
        # 描述性
        if len(alt_text) > 40:
            suggestions.append("✓ Alt文本足够描述性")
        
        # 语言建议
        if language == "chinese":
            suggestions.append("中文SEO建议使用中文Alt文本")
        
        return suggestions
    
    def generate_batch(
        self,
        images: List[Dict[str, Any]],
        context: str,
        keywords: List[str]
    ) -> List[Dict[str, Any]]:
        """
        批量生成Alt文本
        
        Args:
            images: 图片列表，每个包含 description, position 等
            context: 文章上下文
            keywords: 关键词列表
            
        Returns:
            批量结果
        """
        results = []
        
        for i, img in enumerate(images):
            # 确定关键词
            primary_kw = keywords[0] if keywords else ""
            
            # 生成Alt文本
            result = self.generate(
                image_description=img.get("description", ""),
                context=context,
                keywords=[primary_kw],
                language=img.get("language", "chinese")
            )
            
            result["position"] = img.get("position", i + 1)
            result["original_description"] = img.get("description", "")
            
            results.append(result)
        
        return results
    
    def format_html(self, alt_result: Dict, class_name: str = "article-image") -> str:
        """
        格式化HTML图片标签
        
        Args:
            alt_result: generate()的结果
            class_name: CSS类名
            
        Returns:
            HTML img标签
        """
        src_raw = alt_result.get("src", "#")
        src = self._sanitize_src(src_raw)
        alt = html.escape(str(alt_result.get("alt_text", "") or ""), quote=True)
        title = html.escape(str(alt_result.get("title", "") or ""), quote=True)
        cls = html.escape(str(class_name or ""), quote=True)
        
        html_out = f'<img src="{src}" alt="{alt}"'
        
        if title:
            html_out += f' title="{title}"'
        
        if class_name:
            html_out += f' class="{cls}"'
        
        html_out += ' loading="lazy"'
        html_out += '>'
        
        return html_out

    def _sanitize_src(self, src: Any) -> str:
        val = str(src or "").strip()
        if not val:
            return "#"
        if val.startswith("/"):
            return html.escape(val, quote=True)
        if val.startswith("data:image/"):
            return html.escape(val, quote=True)
        parsed = urlparse(val)
        if parsed.scheme in {"http", "https"}:
            return html.escape(val, quote=True)
        return "#"


# CrewAI Tool 包装
def get_alt_text_generator_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("alt_text_generator")
    def alt_text_generator_tool(
        image_description: str,
        context: str = "",
        keywords: str = "",
        language: str = "auto"
    ) -> str:
        """
        为图片生成SEO友好的Alt文本描述。
        
        Args:
            image_description: 图片内容描述
            context: 图片所在文章的上下文/主题
            keywords: 关键词列表，用逗号分隔
            language: 语言 chinese/english
            
        Returns:
            JSON格式的Alt文本和建议
        """
        keyword_list = [k.strip() for k in keywords.split(',') if k.strip()]
        
        generator = AltTextGenerator()
        result = generator.generate(
            image_description=image_description,
            context=context,
            keywords=keyword_list,
            language=language
        )
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    return alt_text_generator_tool


if __name__ == "__main__":
    # 测试
    generator = AltTextGenerator()
    
    result = generator.generate(
        image_description="商务人士在现代化办公室使用笔记本电脑工作",
        context="EMBA项目选择指南",
        keywords=["EMBA选择", "商学院"],
        language="chinese"
    )
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    print("\n" + "="*60)
    print(generator.format_html(result))
