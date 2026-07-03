#!/usr/bin/env python3
"""
关键词分析 V1 — 传统 Python 方案（jieba + TF-IDF，零 LLM 调用）。

参考 StanGirard/seo-audits-toolkit 的关键词密度分析思路：
- 中文分词 + 词频统计
- 关键词密度计算
- 关键词分布检查（标题/H2/首段/末段）
- 停用词过滤
- 零 token 消耗
"""

# 阅读建议：
# - 这是“规则引擎型”关键词分析器，适合先理解 SEO 检查的基础逻辑
# - 它不依赖大模型，因此结果稳定、成本低、调试方便
# - 它的输出通常会被 `SEOAgent` 再次包装成统一的 `keyword_result`

from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import yaml


def _deep_env_resolve(value: Any) -> Any:
    """递归展开配置中的环境变量引用。"""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            key = value[2:-1]
            return os.environ.get(key, "")
        return value
    if isinstance(value, dict):
        return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_env_resolve(v) for v in value]
    return value


# 中文停用词表
STOP_WORDS: set = {
    "的", "了", "和", "是", "在", "我", "有", "个", "人", "这",
    "不", "也", "就", "那", "你", "会", "对", "要", "来", "可以",
    "他", "她", "它", "们", "都", "把", "被", "让", "给", "从",
    "去", "到", "与", "但", "或", "而", "且", "为", "所", "如",
    "能", "上", "下", "中", "大", "小", "多", "少", "很", "更",
    "之", "一", "将", "以", "及", "可", "该", "其", "此", "等",
    "已", "还", "又", "只", "没", "才", "则", "如果", "因为",
    "所以", "然后", "因此", "此外", "另外", "同时", "例如",
    "而且", "但是", "不过", "虽然", "然而", "于是", "总之",
}


class KeywordAnalyzerV1:
    """传统 Python 关键词分析器 — V1 方案。"""

    def __init__(self, config_path: str = "agents/seo_agent/config.yaml"):
        """加载 SEO 配置。

        虽然 V1 不调用 LLM，但它仍依赖配置文件里的阈值：
        - 关键词密度上下限
        - 标题/H2/首段等布局要求
        """
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """读取配置文件，不存在时返回空配置。"""
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    # ── 分词 ────────────────────────────────────────────
    def _segment(self, text: str) -> List[str]:
        """中文分词，优先用 jieba，降级到简单切分。"""
        try:
            import jieba
            return [t.strip() for t in jieba.lcut(text) if t.strip()]
        except ImportError:
            return re.findall(r"[\u4e00-\u9fff]{2,}", text)

    def _meaningful_tokens(self, tokens: List[str]) -> List[str]:
        """过滤停用词、纯数字和过短 token。

        这样做的目的是减少噪声词对候选关键词和 LSI 提取的干扰。
        """
        out: List[str] = []
        for t in tokens:
            if t in STOP_WORDS:
                continue
            if re.fullmatch(r"\d+", t):
                continue
            if len(t) < 2:
                continue
            if not re.search(r"[\u4e00-\u9fffA-Za-z]", t):
                continue
            out.append(t)
        return out

    # ── 统计 ────────────────────────────────────────────
    def _total_word_count(self, text: str) -> int:
        """估算文章词数。

        中文按字统计，英文按单词统计。
        这是 SEO 场景里常见的近似处理，足够用于关键词密度计算。
        """
        chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
        english = len(re.findall(r"\b[a-zA-Z]+\b", text))
        return chinese + english

    def _keyword_count(self, text: str, keyword: str) -> int:
        """统计目标关键词出现次数。"""
        if not keyword:
            return 0
        return text.count(keyword)

    def _density(self, occurrences: int, kw_len: int, total_words: int) -> float:
        """计算关键词密度。

        公式：
        (出现次数 × 关键词长度) / 总词数 × 100
        """
        if total_words == 0:
            return 0.0
        return (occurrences * max(kw_len, 1) / total_words) * 100.0

    # ── TF-IDF 提取候选词 ──────────────────────────────
    def _extract_candidates(self, text: str, top_n: int = 30) -> List[str]:
        """基于词频提取候选关键词（简化 TF 方案）。

        这里没有做真正的多文档 TF-IDF，而是针对单篇文章用“高频词近似候选词”。
        对 SEO 初筛场景来说足够实用。
        """
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"```[\s\S]*?```", "", clean)
        clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
        clean = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", clean)

        tokens = self._segment(clean)
        meaningful = self._meaningful_tokens(tokens)
        counter = Counter(meaningful)
        return [w for w, _ in counter.most_common(top_n)]

    # ── 分布检查 ────────────────────────────────────────
    def _check_distribution(
        self, content: str, keyword: str
    ) -> Dict[str, Any]:
        """检查关键词在关键位置的分布。

        重点检查：
        - 标题附近
        - 首段
        - Markdown 标题（尤其 H2）
        - 结尾段落
        """
        in_title = keyword in (content[:100] or "")
        in_first_para = keyword in (content[:500] if len(content) > 500 else content)

        headings = re.findall(r"#{1,6}\s+([^\n]+)", content)
        heading_matches = [h for h in headings if keyword in h]

        # 末段
        paragraphs = content.split("\n\n")
        last_para = paragraphs[-1] if paragraphs else ""
        in_last = keyword in last_para

        return {
            "title": in_title,
            "first_paragraph": in_first_para,
            "headings": heading_matches,
            "heading_count": len(heading_matches),
            "last_section": in_last,
        }

    # ── LSI 词 ──────────────────────────────────────────
    def _extract_lsi(
        self, tokens: List[str], keyword: str, top_n: int = 10
    ) -> List[str]:
        """提取 LSI 语义相关词（基于共现窗口）。

        实现方式：
        - 找到主关键词 token
        - 向左右各看一个窗口
        - 统计共现频率高的词
        """
        if not keyword:
            return []
        kw_tokens = self._segment(keyword)
        kw_set = set(t for t in kw_tokens if t not in STOP_WORDS and len(t) >= 2)
        if not kw_set:
            return []

        window = 8
        scores: Counter = Counter()
        for i, tok in enumerate(tokens):
            if tok in kw_set:
                left = max(0, i - window)
                right = min(len(tokens), i + window + 1)
                for t in tokens[left:right]:
                    if t in kw_set or t in STOP_WORDS or len(t) < 2:
                        continue
                    scores[t] += 1

        return [w for w, _ in scores.most_common(top_n)]

    # ── 评分 ────────────────────────────────────────────
    def _assess(
        self,
        keyword: str,
        density: float,
        distribution: Dict[str, Any],
        candidates: List[str],
        lsi_words: List[str],
    ) -> Dict[str, Any]:
        """把统计结果翻译成 SEO 评分与建议。

        这一步非常关键，因为上游只是“数据”，这里才变成“可执行结论”。
        """
        seo_cfg = (self.config or {}).get("seo") if isinstance(self.config, dict) else {}
        kd = (seo_cfg.get("keyword_density") or {}) if isinstance(seo_cfg, dict) else {}
        primary_cfg = (kd.get("primary") or {}) if isinstance(kd, dict) else {}
        min_d = float(primary_cfg.get("min", 1.0))
        max_d = float(primary_cfg.get("max", 2.5))

        issues: List[str] = []
        passed: List[str] = []
        suggestions: List[str] = []
        score = 100

        if density < min_d:
            issues.append(f"density{density:.1f}%低于{min_d}%")
            score -= 20
            suggestions.append(f"适当增加'{keyword}'的出现次数")
        elif density > max_d:
            issues.append(f"density{density:.1f}%超过{max_d}%")
            score -= 30
            suggestions.append(f"减少'{keyword}'或替换为同义词")
        else:
            passed.append(f"density{density:.1f}%在{min_d}%-{max_d}%范围内")

        if not distribution["title"]:
            issues.append("标题不含关键词")
            score -= 15
            suggestions.append("在标题中加入关键词")
        else:
            passed.append("标题包含关键词")

        if not distribution["first_paragraph"]:
            issues.append("首段不含关键词")
            score -= 10
            suggestions.append("在文章开头段落加入关键词")
        else:
            passed.append("首段包含关键词")

        if distribution["heading_count"] < 2:
            issues.append(f"仅{distribution['heading_count']}个H2含关键词")
            score -= 10
            suggestions.append("在更多小标题中加入关键词或其变体")
        else:
            passed.append(f"{distribution['heading_count']}个H2包含关键词")

        if not distribution["last_section"]:
            suggestions.append("建议在结尾段落再次提及关键词")

        return {
            "score": max(0, score),
            "issues": issues,
            "passed_checks": passed,
            "suggestions": suggestions,
            "candidates": candidates[:15],
            "lsi_words": lsi_words[:10],
        }

    # ── 主入口 ──────────────────────────────────────────
    def analyze(
        self,
        content: str,
        target_keyword: str,
    ) -> Dict[str, Any]:
        """分析文章关键词。

        Args:
            content: 文章正文（Markdown）
            target_keyword: 目标关键词（从 TopicAgent 或文章标题推导）

        Returns:
            {
                "primary_keyword": str,
                "density": float,
                "occurrences": int,
                "total_words": int,
                "distribution": {...},
                "candidates": [...],
                "lsi_words": [...],
                "assessment": {...},
            }
        """
        # 这是对外主入口，负责把“统计、检查、评分”串成完整流程。
        total_words = self._total_word_count(content)
        kw_len = len(target_keyword)
        occurrences = self._keyword_count(content, target_keyword)
        density = self._density(occurrences, kw_len, total_words)

        distribution = self._check_distribution(content, target_keyword)

        clean = re.sub(r"<[^>]+>", "", content)
        clean = re.sub(r"```[\s\S]*?```", "", clean)
        clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
        clean = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", clean)
        tokens = self._segment(clean)
        meaningful = self._meaningful_tokens(tokens)

        candidates = self._extract_candidates(content)
        lsi_words = self._extract_lsi(meaningful, target_keyword)

        assessment = self._assess(
            keyword=target_keyword,
            density=density,
            distribution=distribution,
            candidates=candidates,
            lsi_words=lsi_words,
        )

        return {
            "primary_keyword": target_keyword,
            "density": round(density, 2),
            "occurrences": occurrences,
            "total_words": total_words,
            "distribution": distribution,
            "candidates": candidates[:20],
            "lsi_words": lsi_words[:10],
            "assessment": assessment,
            "analyzer": "v1_traditional",
        }
