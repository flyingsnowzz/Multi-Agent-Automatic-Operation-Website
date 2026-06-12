"""
Content Evaluator Tool

评估爬虫内容是否适合作为后续 TopicAgent 的素材输入。
默认只做规则评估，不负责选题评分、不负责发布、不负责重写。
"""

import json
import re
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional

from crewai.tools import tool


class ContentEvaluator:
    """素材评估器"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.min_word_count = int(self.config.get("min_word_count", 100) or 100)
        self.max_word_count = int(self.config.get("max_word_count", 5000) or 5000)
        self.copyright_risk_cfg = self.config.get("copyright_risk") or {}
        self.domain_blacklist = set(str(d).strip().lower() for d in (self.config.get("domain_blacklist") or []) if str(d).strip())

    async def evaluate(
        self,
        title: str,
        content: str,
        source_url: Optional[str] = None,
        target_keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        try:
            clean_title = (title or "").strip()
            clean_content = (content or "").strip()
            normalized_content = self._normalize_text(clean_content)
            word_count = self._count_words(normalized_content)

            source_ok, source_reason = self._check_source(source_url or "")
            has_risk, risk_reason = self._check_risk(clean_title, clean_content, source_url or "")
            topic_hint = self._extract_topic_hint(clean_title, clean_content)

            completeness, completeness_reason = self._score_completeness(clean_title, clean_content, normalized_content)
            density, density_reason = self._score_information_density(normalized_content)
            readability, readability_reason = self._score_readability(clean_content, normalized_content)
            material_value, material_reason = self._score_material_value(topic_hint, clean_title, normalized_content, target_keywords or [])

            material_score = round(
                completeness + density + readability + material_value,
                1,
            )
            material_score = max(0.0, min(100.0, material_score))

            reasons = [r for r in [completeness_reason, density_reason, readability_reason, material_reason] if r]
            if not source_ok and source_reason:
                reasons.append(source_reason)
            if has_risk and risk_reason:
                reasons.append(risk_reason)
            if not topic_hint:
                reasons.append("无法提炼出明确 topic")

            return {
                "success": True,
                "material_score": material_score,
                "has_risk": has_risk,
                "source_ok": source_ok,
                "topic_hint": topic_hint,
                "reason": "；".join(reasons[:4]),
                "word_count": word_count,
                "details": {
                    "completeness_score": round(completeness, 1),
                    "information_density_score": round(density, 1),
                    "readability_score": round(readability, 1),
                    "material_value_score": round(material_value, 1),
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _normalize_text(self, text: str) -> str:
        t = text or ""
        t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", t)
        t = re.sub(r"(?s)<[^>]+>", " ", t)
        t = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", t)
        t = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", t)
        t = re.sub(r"`[^`]+`", " ", t)
        t = re.sub(r"\s+", " ", t)
        return t.strip()

    def _count_words(self, text: str) -> int:
        chinese = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
        english = len(re.findall(r"\b[a-zA-Z]+\b", text or ""))
        return chinese + english

    def _check_source(self, source_url: str) -> tuple[bool, str]:
        url = (source_url or "").strip()
        if not url:
            return False, "source_url 缺失"
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "source_url 解析失败"
        if parsed.scheme not in {"http", "https"}:
            return False, "source_url 协议非法"
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False, "source_url 域名缺失"
        if "." not in host or " " in host:
            return False, "source_url 域名格式异常"
        if host in self.domain_blacklist:
            return False, "source_url 命中域名黑名单"
        return True, ""

    def _check_risk(self, title: str, content: str, source_url: str) -> tuple[bool, str]:
        text = f"{title}\n{content}\n{source_url}"
        builtin_patterns = [
            (r"未经授权不得转载|未经许可不得转载|版权所有|侵权必究|保留所有权利", "强版权声明"),
            (r"招商加盟|加微信|联系电话|扫码咨询|立即购买|推广合作|商务合作", "广告或推广话术"),
            (r"点击进入|备用网址|最新网址|全网最低|稳赚不赔", "疑似垃圾站/营销文案"),
            (r"(转载自|来源于).{0,12}(网络|互联网|本站|搜狐|网易|今日头条)", "高风险转载提示"),
        ]
        for pattern, reason in builtin_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True, reason

        cfg = self.copyright_risk_cfg if isinstance(self.copyright_risk_cfg, dict) else {}
        if bool(cfg.get("check_enabled", False)):
            for k in cfg.get("risk_keywords") or []:
                if k and str(k) in text:
                    return True, "配置版权风险关键词命中"
            for p in cfg.get("risk_patterns") or []:
                try:
                    if re.search(str(p), text):
                        return True, "配置版权风险模式命中"
                except Exception:
                    continue
        return False, ""

    def _score_completeness(self, title: str, content: str, normalized_content: str) -> tuple[float, str]:
        score = 0.0
        reasons: List[str] = []
        if title.strip():
            score += 8.0
        else:
            reasons.append("标题缺失")

        wc = self._count_words(normalized_content)
        if wc >= max(self.min_word_count, 200):
            score += 12.0
        elif wc >= 120:
            score += 7.0
            reasons.append("正文偏短")
        else:
            reasons.append("正文明显过短")

        paragraph_parts = [p.strip() for p in re.split(r"\n\s*\n", content or "") if p.strip()]
        if len(paragraph_parts) >= 3:
            score += 6.0
        elif len(paragraph_parts) >= 2:
            score += 3.0
            reasons.append("段落结构较弱")
        else:
            reasons.append("段落结构不足")

        if re.fullmatch(r"(目录|摘要|概述|简介|导航|点击查看).{0,40}", normalized_content):
            reasons.append("正文像目录或摘要")
        else:
            score += 4.0

        return max(0.0, min(30.0, score)), "、".join(reasons[:2])

    def _score_information_density(self, normalized_content: str) -> tuple[float, str]:
        score = 0.0
        reasons: List[str] = []
        patterns = {
            "facts": r"\d+[%万亿千百]|根据|数据显示|统计|研究|报告",
            "views": r"认为|指出|建议|分析|判断",
            "cases": r"例如|比如|案例|实践|经验",
            "steps": r"首先|其次|然后|最后|步骤|方法",
            "conclusion": r"总结|结论|因此|由此可见",
        }
        matched = 0
        for pat in patterns.values():
            if re.search(pat, normalized_content):
                matched += 1
        score += matched * 5.0

        wc = self._count_words(normalized_content)
        if wc >= 300:
            score += 5.0

        empty_phrases = len(re.findall(r"非常重要|值得关注|不言而喻|众所周知|很多很多|很多人都知道", normalized_content))
        if empty_phrases >= 3:
            score -= 6.0
            reasons.append("空话套话偏多")
        if matched < 2:
            reasons.append("有效信息密度不足")
        return max(0.0, min(30.0, score)), "、".join(reasons[:2])

    def _score_readability(self, content: str, normalized_content: str) -> tuple[float, str]:
        score = 20.0
        reasons: List[str] = []

        bad_chars = len(re.findall(r"[�\ufffd]", content or ""))
        if bad_chars > 0:
            score -= 10.0
            reasons.append("存在乱码")

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content or "") if p.strip()]
        duplicate_para = 0
        seen = set()
        for p in paragraphs:
            key = re.sub(r"\s+", "", p)
            if len(key) < 20:
                continue
            if key in seen:
                duplicate_para += 1
            seen.add(key)
        if duplicate_para > 0:
            score -= min(duplicate_para * 4.0, 8.0)
            reasons.append("存在重复段落")

        ad_insertions = len(re.findall(r"扫码|微信|公众号|QQ|电话|购买|下单|优惠", content or ""))
        if ad_insertions >= 3:
            score -= 8.0
            reasons.append("广告插入明显")

        if normalized_content.count("http://") + normalized_content.count("https://") > 5:
            score -= 4.0
            reasons.append("链接过多")

        return max(0.0, min(20.0, score)), "、".join(reasons[:2])

    def _extract_topic_hint(self, title: str, content: str) -> str:
        title = (title or "").strip()
        normalized = self._normalize_text(content or "")
        generic_title = re.search(r"^(资讯|新闻|公告|快讯|动态|行业观察|行业资讯|综合报道)$", title)
        candidate = title
        if not candidate or generic_title or len(candidate) < 6:
            lead = " ".join(re.split(r"\n\s*\n", content or "")[:2])
            lead = self._normalize_text(lead)
            lead = re.sub(r"^.{0,12}(导语|摘要|前言)[:：]?", "", lead)
            candidate = (title + " " + lead).strip()

        candidate = re.sub(r"\s+", " ", candidate)
        candidate = re.sub(r"[\"'“”‘’【】\[\]（）()<>《》]", "", candidate).strip()
        if len(candidate) > 36:
            candidate = candidate[:36].rstrip("，。；：,.;: ")
        if len(candidate) < 6:
            return ""
        if self._is_low_signal_topic(candidate):
            return ""
        return candidate

    def _is_low_signal_topic(self, text: str) -> bool:
        candidate = (text or "").strip()
        if not candidate:
            return True

        normalized = re.sub(r"[，。；：,.;:!?！？、/\\|_\-]+", " ", candidate)
        tokens = [t for t in normalized.split() if t]
        generic_terms = {"资讯", "新闻", "公告", "快讯", "动态", "行业", "行业资讯", "综合", "报道"}

        if tokens:
            unique_tokens = set(tokens)
            if len(tokens) >= 4 and len(unique_tokens) <= 2:
                return True
            if unique_tokens and all(token in generic_terms for token in unique_tokens):
                return True
            most_common = max(tokens.count(token) for token in unique_tokens)
            if len(tokens) >= 5 and (most_common / len(tokens)) >= 0.6:
                return True

        compact = re.sub(r"\s+", "", candidate)
        if compact:
            repeated_chunks = re.findall(r"(.{1,4})\1{2,}", compact)
            if repeated_chunks:
                return True

            generic_compact = re.sub(r"(资讯|新闻|公告|快讯|动态|行业观察|行业资讯|综合报道)", "", compact)
            if not generic_compact:
                return True

        return False

    def _score_material_value(self, topic_hint: str, title: str, normalized_content: str, target_keywords: List[str]) -> tuple[float, str]:
        score = 0.0
        reasons: List[str] = []
        if topic_hint:
            score += 10.0
        else:
            reasons.append("无法提炼明确 topic")

        if len(title.strip()) >= 8:
            score += 4.0
        if self._count_words(normalized_content) >= 250:
            score += 3.0

        matched_keywords = 0
        combined = f"{title} {normalized_content}".lower()
        for kw in target_keywords or []:
            k = str(kw).strip().lower()
            if k and k in combined:
                matched_keywords += 1
        if matched_keywords > 0:
            score += min(matched_keywords * 3.0, 6.0)
        else:
            reasons.append("与目标主题线索弱")
        return max(0.0, min(20.0, score)), "、".join(reasons[:2])


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
        包含 success, material_score, has_risk, source_ok, topic_hint, reason 的字典
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
