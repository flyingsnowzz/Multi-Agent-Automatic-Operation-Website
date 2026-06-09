#!/usr/bin/env python3
"""
语法检查工具 - EditorAgent
检查文本语法错误并提供修正建议
"""

import re
import json
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class ErrorType(str, Enum):
    """错误类型"""
    SPELLING = "spelling"
    GRAMMAR = "grammar"
    PUNCTUATION = "punctuation"
    STYLE = "style"
    CLARITY = "clarity"
    CONSISTENCY = "consistency"


@dataclass
class GrammarIssue:
    """语法问题"""
    start: int
    end: int
    text: str
    error_type: ErrorType
    message: str
    suggestion: str
    severity: str  # error/warning/info


class GrammarChecker:
    """语法检查工具"""
    
    def __init__(self, language: str = "chinese"):
        self.language = language
        
        # 中文常见错误模式
        self.chinese_patterns = [
            # 主谓不一致
            (r'们个', '个', '量词使用不当'),
            (r'的地得混用', '需要区分"的/地/得"', '助词使用错误'),
            # 标点错误
            (r'，，', '，', '连续逗号'),
            (r'。。', '。', '连续句号'),
            (r'——', '—', '破折号使用不当'),
            # 常见错别字
            (r'象像', '像', '"像"和"象"混淆'),
            (r'做作', '做', '"做"和"作"混淆'),
            (r'连接结', '连结', '词语混淆'),
        ]
        
        # 英文常见错误模式
        self.english_patterns = [
            # 主谓一致
            (r'\b(their|his|her)\s+\w+es\b', None, '主谓一致检查'),
            # 冠词使用
            (r'\ba\s+[aeiou]', 'an', '元音前应用an'),
            (r'\ban\s+[bcdfghjklmnpqrstvwxyz]', 'a', '辅音前应用a'),
            # 时态
            (r'\bhave\s+\w+ed\b', None, '检查时态'),
            # 介词
            (r'\bin\ Monday\b', 'on Monday', '具体日期用on'),
        ]
    
    def check(self, text: str) -> Dict[str, Any]:
        """
        检查文本语法
        
        Args:
            text: 要检查的文本
            
        Returns:
            检查结果
        """
        if self.language == "chinese":
            return self._check_chinese(text)
        else:
            return self._check_english(text)

    def _issues_to_patches(self, text: str, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        patches: List[Dict[str, Any]] = []
        for it in issues:
            if not isinstance(it, dict):
                continue
            suggestion = str(it.get("suggestion") or "")
            if not suggestion:
                continue
            try:
                start = int(it.get("start"))
                end = int(it.get("end"))
            except Exception:
                continue
            if start < 0 or end <= start or end > len(text):
                continue

            error_type = str(it.get("error_type") or "")
            severity = str(it.get("severity") or "warning")
            confidence = 0.6
            if error_type == ErrorType.PUNCTUATION.value:
                confidence = 0.9
            elif error_type == ErrorType.SPELLING.value:
                confidence = 0.9
            elif error_type == ErrorType.STYLE.value:
                confidence = 0.8
            elif error_type == ErrorType.GRAMMAR.value:
                confidence = 0.6

            if severity == "error":
                confidence = max(confidence, 0.8)

            if len(suggestion) > 20:
                continue

            patches.append(
                {
                    "start": start,
                    "end": end,
                    "replacement": suggestion,
                    "reason": error_type,
                    "confidence": confidence,
                }
            )
        return patches
    
    def _check_chinese(self, text: str) -> Dict[str, Any]:
        """检查中文文本"""
        issues = []
        
        # 检查常见错误模式
        for pattern, correction, message in self.chinese_patterns:
            matches = list(re.finditer(pattern, text))
            for match in matches:
                issues.append({
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(),
                    "error_type": ErrorType.GRAMMAR.value,
                    "message": message,
                    "suggestion": correction if correction else "",
                    "severity": "warning"
                })
        
        # 检查标点符号混用
        punctuation_issues = self._check_chinese_punctuation(text)
        issues.extend(punctuation_issues)
        
        # 检查重复字词
        repeat_issues = self._check_repeated_words(text)
        issues.extend(repeat_issues)
        
        # 统计
        error_count = sum(1 for i in issues if i['severity'] == 'error')
        warning_count = sum(1 for i in issues if i['severity'] == 'warning')
        patches = self._issues_to_patches(text, issues)
        
        return {
            "language": "chinese",
            "total_issues": len(issues),
            "error_count": error_count,
            "warning_count": warning_count,
            "issues": issues,
            "patches": patches,
            "summary": f"发现 {error_count} 个错误，{warning_count} 个警告"
        }
    
    def _check_english(self, text: str) -> Dict[str, Any]:
        """检查英文文本"""
        issues = []
        
        # 检查常见错误模式
        for pattern, correction, message in self.english_patterns:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for match in matches:
                issues.append({
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(),
                    "error_type": ErrorType.GRAMMAR.value,
                    "message": message,
                    "suggestion": correction if correction else "",
                    "severity": "warning"
                })
        
        # 检查常见拼写错误
        spelling_issues = self._check_common_spelling(text)
        issues.extend(spelling_issues)
        
        # 统计
        error_count = sum(1 for i in issues if i['severity'] == 'error')
        warning_count = sum(1 for i in issues if i['severity'] == 'warning')
        patches = self._issues_to_patches(text, issues)
        
        return {
            "language": "english",
            "total_issues": len(issues),
            "error_count": error_count,
            "warning_count": warning_count,
            "issues": issues,
            "patches": patches,
            "summary": f"Found {error_count} errors, {warning_count} warnings"
        }
    
    def _check_chinese_punctuation(self, text: str) -> List[Dict]:
        """检查中文标点"""
        issues = []
        
        # 连续标点
        consecutive_patterns = [
            (r'[，。：；][，。：；]+', '', '重复标点'),
            (r'！！+', '！', '连续感叹号'),
            (r'？？+', '？', '连续问号'),
        ]
        
        for pattern, suggestion, message in consecutive_patterns:
            matches = list(re.finditer(pattern, text))
            for match in matches:
                issues.append({
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(),
                    "error_type": ErrorType.PUNCTUATION.value,
                    "message": message,
                    "suggestion": suggestion,
                    "severity": "warning"
                })
        
        return issues
    
    def _check_repeated_words(self, text: str) -> List[Dict]:
        """检查重复字词"""
        issues = []
        
        # 匹配连续重复的字（2个以上）
        pattern = r'([\u4e00-\u9fff])\1{2,}'
        matches = list(re.finditer(pattern, text))
        
        for match in matches:
            issues.append({
                "start": match.start(),
                "end": match.end(),
                "text": match.group(),
                "error_type": ErrorType.STYLE.value,
                "message": "存在重复字符",
                "suggestion": match.group()[0],
                "severity": "warning"
            })
        
        return issues
    
    def _check_common_spelling(self, text: str) -> List[Dict]:
        """检查常见英文拼写错误"""
        common_misspellings = {
            r'\bteh\b': 'the',
            r'\brecieve\b': 'receive',
            r'\bpsychology\b': 'psychology',  # 已正确
            r'\bseperate\b': 'separate',
            r'\boccured\b': 'occurred',
            r'\buntill\b': 'until',
            r'\bbegining\b': 'beginning',
            r'\bwritting\b': 'writing',
            r'\bgoverment\b': 'government',
            r'\bdefinately\b': 'definitely',
        }
        
        issues = []
        for pattern, correction in common_misspellings.items():
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for match in matches:
                issues.append({
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(),
                    "error_type": ErrorType.SPELLING.value,
                    "message": "常见拼写错误",
                    "suggestion": correction,
                    "severity": "error"
                })
        
        return issues
    
    def auto_correct(self, text: str) -> str:
        """
        自动修正文本
        
        Args:
            text: 原始文本
            
        Returns:
            修正后的文本
        """
        corrected = text
        
        if self.language == "chinese":
            patterns = self.chinese_patterns
        else:
            patterns = self.english_patterns
        
        for pattern, correction, _ in patterns:
            if correction:
                corrected = re.sub(pattern, correction, corrected)
        
        return corrected


# CrewAI Tool 包装
def get_grammar_checker_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("grammar_checker")
    def grammar_checker_tool(text: str, language: str = "chinese") -> str:
        """
        检查文本的语法错误。
        
        Args:
            text: 要检查的文本内容
            language: 语言类型，可选 chinese/english
            
        Returns:
            JSON格式的检查结果
        """
        checker = GrammarChecker(language)
        result = checker.check(text)
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    return grammar_checker_tool


if __name__ == "__main__":
    # 测试
    checker = GrammarChecker("chinese")
    
    test_text = """
    清华大学经济管理学院是中国的顶尖商学院之一。
    
    该学院成立于1926年，拥有悠久的历史和卓越的学术传统。EMBA项目旨在培养具有国际视野的商业领袖。
    
    报考EMBA需要具备本科学历和一定的工作经验。
    """
    
    result = checker.check(test_text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
