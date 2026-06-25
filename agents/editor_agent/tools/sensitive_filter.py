#!/usr/bin/env python3
"""
敏感词安全过滤器
基于 Sensitive-lexicon (github.com/konsheng/Sensitive-lexicon) 对文章进行安全审查。
"""

import os
import re
from typing import Dict, List, Optional


_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sensitive_lexicon")

_LEXICON_FILES = [
    "政治类型.txt",
    "反动词库.txt",
    "暴恐词库.txt",
    "色情词库.txt",
    "涉枪涉爆.txt",
]


class SensitiveFilter:
    """敏感词过滤器，使用编译正则一次性扫描全文。"""

    def __init__(self, lexicon_dir: Optional[str] = None, min_word_len: int = 2):
        self.lexicon_dir = lexicon_dir or _DATA_DIR
        self.min_word_len = min_word_len
        self._words: List[str] = []
        self._pattern: re.Pattern = re.compile(r"(?!)")
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        words: set = set()
        for fname in _LEXICON_FILES:
            fpath = os.path.join(self.lexicon_dir, fname)
            if not os.path.exists(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    w = line.strip()
                    if w and len(w) >= self.min_word_len:
                        words.add(w)
        if not words:
            self._loaded = True
            return
        self._words = sorted(words, key=lambda w: -len(w))
        escaped = [re.escape(w) for w in self._words]
        self._pattern = re.compile("|".join(escaped), re.IGNORECASE)
        self._loaded = True

    def check(self, text: str) -> Dict:
        if not self._loaded:
            self.load()
        if not text:
            return {"passed": True, "matched": [], "count": 0}
        matches = self._pattern.findall(text)
        seen: set = set()
        unique = []
        for m in matches:
            if m not in seen:
                seen.add(m)
                unique.append(m)
        return {
            "passed": len(unique) == 0,
            "matched": unique,
            "count": len(unique),
        }


_default_filter: Optional[SensitiveFilter] = None


def get_sensitive_filter() -> SensitiveFilter:
    global _default_filter
    if _default_filter is None:
        _default_filter = SensitiveFilter()
        _default_filter.load()
    return _default_filter
