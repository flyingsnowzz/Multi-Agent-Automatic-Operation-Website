#!/usr/bin/env python3
"""
Alt文本生成工具 - ImageAgent
为图片生成SEO友好的Alt文本描述

设计说明：
    Alt 文本是 HTML <img alt="..."> 属性的内容，对 SEO 和无障碍访问至关重要。
    本模块采用「规则拼接 + 质量校验」的方式生成 Alt 文本（不依赖 LLM），
    保证生成速度快、可预测、可控。

    生成策略：
      1. 主关键词放在最前面（SEO 权重最高位置）
      2. 拼接图片内容描述（截断到合理长度）
      3. 可选追加文章上下文
      4. 截断到 125 字符以内（Google 推荐上限）
      5. 质量校验：长度、关键词命中、避免泛指词、重复检测
"""

import json
import re
import html
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional


class AltTextGenerator:
    """Alt文本生成工具

    无状态工具类，可安全地在多处复用。
    所有方法均为纯函数式实现（不依赖外部状态），便于测试。
    """

    def __init__(self):
        # Google 推荐 Alt 文本不超过 125 字符，超过会被搜索引擎截断
        self.max_length = 125

    def generate(
        self,
        image_description: str,
        context: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        language: str = "chinese"
    ) -> Dict[str, Any]:
        """
        生成Alt文本（主入口）

        根据语言分发到中文/英文生成逻辑。language="auto" 时自动检测。

        Args:
            image_description: 图片内容描述
            context: 文章上下文/主题（可选，会拼进 Alt 提升相关性）
            keywords: 关键词列表，取第一个作为主关键词放进 Alt
            language: 语言 chinese/english/auto

        Returns:
            {
                alt_text:   生成的 Alt 文本,
                title:      图片标题（用于 title 属性，可比 Alt 长）,
                length:     Alt 文本长度,
                warnings:   质量警告列表,
                suggestions:SEO 优化建议列表
            }
        """
        # 规范化语言参数
        lang = (language or "chinese").strip().lower()
        # 自动检测语言时根据描述文本中是否含中文字符判断
        detected = self._detect_language(image_description)
        if lang in {"auto", "detect"}:
            lang = detected
        # 按语言分发
        if lang == "chinese":
            return self._generate_chinese(image_description, context, keywords)
        return self._generate_english(image_description, context, keywords)

    def _detect_language(self, text: str) -> str:
        """通过是否包含 CJK 统一汉字判断中英文"""
        if not text:
            return "chinese"
        # \u4e00-\u9fff 是 CJK 统一汉字基本区
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
        """生成中文Alt文本

        拼接顺序：主关键词 + 内容描述（截短）+ 上下文，用中文逗号连接。
        """
        # 主关键词取列表第一项
        keyword = keywords[0] if keywords else ""

        # 分步构建 Alt 文本的各部分
        alt_parts = []

        # 1. 关键词优先（SEO 权重最高的开头位置）
        if keyword:
            alt_parts.append(keyword)

        # 2. 图片描述（截短到 12 个词/字以内，避免 Alt 过长）
        desc_short = self._shorten_description(description, max_words=12)
        if desc_short:
            # 去重：避免和关键词重复
            if desc_short not in alt_parts:
                alt_parts.append(desc_short)

        # 3. 上下文（仅在简短时追加，防止喧宾夺主）
        if context and len(context) < 50:
            c = context.strip()
            if c and c not in alt_parts:
                alt_parts.append(c)

        # 用中文逗号拼接
        alt_text = "，".join(alt_parts) if alt_parts else (description.strip()[:50] if description else "")

        # 兜底保证关键词出现
        if keyword and keyword not in alt_text:
            alt_text = f"{keyword}，{alt_text}" if alt_text else keyword

        # 截断到最大长度
        if len(alt_text) > self.max_length:
            alt_text = alt_text[:self.max_length-3] + "..."

        # 生成图片标题（title 属性用）
        title = self._generate_title(alt_text, keyword)
        # 质量校验
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
        """生成英文Alt文本

        逻辑与中文版一致，仅分隔符改为英文逗号 + 空格。
        """
        keyword = keywords[0] if keywords else ""

        alt_parts = []

        if keyword and keyword.strip():
            alt_parts.append(keyword.strip())

        desc = (description or "").strip()
        # 大小写不敏感地去重
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
        """缩短描述到指定词数

        中文按连续汉字段切分，英文按单词切分。
        """
        text = (description or "").strip()
        if not text:
            return ""
        has_zh = any("\u4e00" <= c <= "\u9fff" for c in text)
        if has_zh:
            # 中文：提取所有连续汉字段，取前 max_words 个
            words = re.findall(r"[\u4e00-\u9fff]+", text)
            selected = words[:max_words]
            return "".join(selected)
        # 英文：提取单词（含撇号，如 don't）
        words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text)
        selected = words[:max_words]
        return " ".join(selected)

    def _generate_title(self, alt_text: str, keyword: str) -> str:
        """生成图片标题（用于 <img title="..."> 的 tooltip）

        标题限制比 Alt 宽松（100 字符以内），超出则截断。
        """
        if len(alt_text) < 100:
            return alt_text
        return alt_text[:97] + "..."

    def _check_alt_text(self, alt_text: str, keyword: str) -> List[str]:
        """质量校验，返回警告列表

        检查项：
          - 长度过短/过长
          - 是否包含主关键词
          - 是否使用了「图片」「image」等无意义泛指词
          - 是否存在过度重复
        """
        warnings = []

        # 长度检查
        if len(alt_text) < 20:
            warnings.append("Alt文本过短，建议至少20字符")
        elif len(alt_text) > self.max_length:
            warnings.append(f"Alt文本过长({len(alt_text)}字符)，建议{self.max_length}字符以内")

        # 关键词命中检查
        if keyword and keyword not in alt_text:
            warnings.append("Alt文本中未包含主关键词")

        # 泛指词检查（这类词对 SEO 无价值）
        if alt_text.count("图片") > 0 or alt_text.count("image") > 0:
            warnings.append("避免使用「图片」等泛指词汇")

        # 重复检查
        if self._has_excessive_repetition(alt_text):
            warnings.append("Alt文本重复字符过多")

        return warnings

    def _has_excessive_repetition(self, alt_text: str) -> bool:
        """检测文本是否存在过度重复（如「好好好好好好」或同一单词反复出现）"""
        text = (alt_text or "").strip()
        if len(text) < 20:
            return False
        # 中文：检测连续重复 4 次以上的同一字符
        if any("\u4e00" <= c <= "\u9fff" for c in text):
            if re.search(r"(.)\1{3,}", text):
                return True
            return False
        # 英文：检测连续重复的同一单词（至少 3 次）
        if re.search(r"\b([A-Za-z]{2,})(\s+\1){2,}\b", text, flags=re.IGNORECASE):
            return True
        # 英文：词级唯一率过低（去重后不足 45%）视为重复过多
        words = re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(words) >= 10:
            uniq = len(set(words))
            if uniq / max(len(words), 1) < 0.45:
                return True
        return False

    def _get_suggestions(self, alt_text: str, keyword: str, language: str) -> List[str]:
        """返回 SEO 优化建议"""
        suggestions = []

        # 关键词位置建议（开头权重最高）
        if keyword and alt_text.startswith(keyword):
            suggestions.append("✓ 关键词在开头，SEO效果最佳")
        elif keyword and keyword in alt_text[:30]:
            suggestions.append("✓ 关键词在前30字符内，效果良好")
        elif keyword:
            suggestions.append("建议将关键词放在Alt文本开头")

        # 描述充分性
        if len(alt_text) > 40:
            suggestions.append("✓ Alt文本足够描述性")

        if language == "chinese":
            suggestions.append("中文SEO建议使用中文Alt文本")

        return suggestions

    def generate_batch(
        self,
        images: List[Dict[str, Any]],
        context: str,
        keywords: List[str]
    ) -> List[Dict[str, Any]]:
        """批量生成Alt文本

        对一组图片逐个调用 generate()，并补充 position / original_description。

        Args:
            images: 图片列表，每项含 description / position / language 等
            context: 文章上下文
            keywords: 关键词列表

        Returns:
            每张图片的生成结果列表
        """
        results = []

        for i, img in enumerate(images):
            primary_kw = keywords[0] if keywords else ""

            result = self.generate(
                image_description=img.get("description", ""),
                context=context,
                keywords=[primary_kw],
                language=img.get("language", "chinese")
            )

            # 补充位置信息和原始描述，便于后续定位
            result["position"] = img.get("position", i + 1)
            result["original_description"] = img.get("description", "")

            results.append(result)

        return results

    def format_html(self, alt_result: Dict, class_name: str = "article-image") -> str:
        """格式化HTML图片标签

        将 generate() 的结果组装成完整的 <img> 标签，
        包含 src/alt/title/class/loading 属性。

        Args:
            alt_result: generate() 的结果（需额外提供 src 字段）
            class_name: CSS 类名

        Returns:
            合法的 HTML img 标签字符串
        """
        # 对 src 做安全校验，防止 XSS
        src_raw = alt_result.get("src", "#")
        src = self._sanitize_src(src_raw)
        # 对属性值做 HTML 转义
        alt = html.escape(str(alt_result.get("alt_text", "") or ""), quote=True)
        title = html.escape(str(alt_result.get("title", "") or ""), quote=True)
        cls = html.escape(str(class_name or ""), quote=True)

        html_out = f'<img src="{src}" alt="{alt}"'

        if title:
            html_out += f' title="{title}"'

        if class_name:
            html_out += f' class="{cls}"'

        # 懒加载，提升页面首屏性能
        html_out += ' loading="lazy"'
        html_out += '>'

        return html_out

    def _sanitize_src(self, src: Any) -> str:
        """对图片 src 做安全校验，仅允许 http(s) / 相对路径 / data URI

        防止 javascript: 等危险协议注入。
        """
        val = str(src or "").strip()
        if not val:
            return "#"
        # 相对路径
        if val.startswith("/"):
            return html.escape(val, quote=True)
        # data URI（内联图片）
        if val.startswith("data:image/"):
            return html.escape(val, quote=True)
        # 仅允许 http/https
        parsed = urlparse(val)
        if parsed.scheme in {"http", "https"}:
            return html.escape(val, quote=True)
        # 其它一律拒绝
        return "#"


# CrewAI Tool 包装
def get_alt_text_generator_tool():
    """返回CrewAI可用的Tool

    将 AltTextGenerator 包装成 CrewAI 框架可识别的 @tool，
    使其能被 CrewAI Agent 自动调用。
    """
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
        # CrewAI 工具入参是字符串，需把逗号分隔的关键词转为列表
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
    # 模块自测
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
