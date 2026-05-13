#!/usr/bin/env python3
"""
可读性检查工具 - WriterAgent
检查文章的可读性评分和建议
"""

import re
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class ReadabilityResult:
    """可读性检查结果"""
    score: float  # 0-100
    grade: str  # 年级水平
    avg_sentence_length: float  # 平均句子长度
    avg_word_length: float  # 平均词长度
    difficult_words: int  # 难词数量
    paragraph_count: int  # 段落数量
    suggestions: List[str]  # 建议
    issues: List[str]  # 问题


class ReadabilityChecker:
    """可读性检查工具"""
    
    def __init__(self):
        # 中文常用字表（简化判断）
        self.common_chinese_chars = set('的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那得于着下自之年过发后作里用道行所然家作方女多知去让现中年先这些年来面么起诉法地张部与文元总何神户直件期市直第新动目合回平代内信表化老给世先直场报又交品论出没从头正确地上女几开手还分资单向所建男儿王见问将目总水无论口气区放界做间查单密记围较才图话丰心关内划验关注农随属合群限七革己入列马先式色活板平系各共次常图将式线长本素少精度广业集专设网造务报月验电工频视众布始存元况述技北派索转令护送察府典坐竞控费均减救兴授坚省快显评养踏错散望半穿戴店奋珍脏择赢邮卖损辣女丹】
        
        # 英文常见词
        self.common_english_words = set('the,be,to,of,and,a,in,that,have,i,it,for,not,on,with,he,as,you,do,at,this,but,his,by,from,they,we,say,her,she,or,an,will,my,one,all,would,there,their,what,so,up,out,if,about,who,get,which,go,me,when,make,can,like,time,no,just,him,know,take,people,into,year,your,good,some,could,them,see,other,than,then,now,look,only,come,its,over,think,also,back,after,use,two,how,our,work,first,well,way,even,new,wan,since,because,most,us')
    
    def check(self, text: str, language: str = "auto") -> ReadabilityResult:
        """
        检查文本可读性
        
        Args:
            text: 文本内容
            language: 语言 auto/chinese/english
            
        Returns:
            可读性检查结果
        """
        if language == "auto":
            # 自动检测语言
            chinese_ratio = self._count_chinese_chars(text) / max(len(text), 1)
            language = "chinese" if chinese_ratio > 0.3 else "english"
        
        if language == "chinese":
            return self._check_chinese(text)
        else:
            return self._check_english(text)
    
    def _check_chinese(self, text: str) -> ReadabilityResult:
        """检查中文文本可读性"""
        # 清理文本
        text = self._clean_text(text)
        
        # 统计
        char_count = len(text)
        chinese_chars = self._count_chinese_chars(text)
        
        # 按标点分割句子
        sentences = self._split_sentences(text)
        sentence_count = max(len(sentences), 1)
        
        # 统计难词（非常用汉字）
        difficult_words = char_count - chinese_chars - len([c for c in text if c in self.common_punctuation])
        
        # 平均句子长度（字符数）
        avg_sentence_length = char_count / sentence_count
        
        # 计算可读性分数（0-100）
        # 分数越高越易读
        score = self._calculate_chinese_score(avg_sentence_length, difficult_words, sentence_count)
        
        # 确定年级水平
        grade = self._get_chinese_grade(score)
        
        # 生成建议
        suggestions = self._generate_chinese_suggestions(avg_sentence_length, difficult_words, sentence_count)
        
        # 问题列表
        issues = self._find_chinese_issues(avg_sentence_length, difficult_words, sentence_count)
        
        return ReadabilityResult(
            score=score,
            grade=grade,
            avg_sentence_length=avg_sentence_length,
            avg_word_length=difficult_words / max(sentence_count, 1),
            difficult_words=difficult_words,
            paragraph_count=text.count('\n\n') + 1,
            suggestions=suggestions,
            issues=issues
        )
    
    def _check_english(self, text: str) -> ReadabilityResult:
        """检查英文文本可读性（Flesch Reading Ease）"""
        # 清理文本
        text = self._clean_text(text)
        
        # 统计
        words = text.split()
        word_count = len(words)
        sentences = self._split_sentences(text)
        sentence_count = max(len(sentences), 1)
        syllable_count = sum(self._count_syllables(word) for word in words)
        
        # 平均句子长度
        avg_sentence_length = word_count / sentence_count
        
        # 平均每词音节数
        avg_syllables_per_word = syllable_count / max(word_count, 1)
        
        # Flesch Reading Ease Score
        score = 206.835 - 1.015 * avg_sentence_length - 84.6 * avg_syllables_per_word
        score = max(0, min(100, score))  # 限制在0-100
        
        # 确定年级水平
        grade = self._get_english_grade(score)
        
        # 统计难词
        difficult_words = sum(1 for word in words if len(word) > 6 and word.lower() not in self.common_english_words)
        
        # 生成建议
        suggestions = self._generate_english_suggestions(avg_sentence_length, avg_syllables_per_word, difficult_words)
        
        # 问题列表
        issues = self._find_english_issues(avg_sentence_length, avg_syllables_per_word, difficult_words)
        
        return ReadabilityResult(
            score=score,
            grade=grade,
            avg_sentence_length=avg_sentence_length,
            avg_word_length=avg_syllables_per_word,
            difficult_words=difficult_words,
            paragraph_count=text.count('\n\n') + 1,
            suggestions=suggestions,
            issues=issues
        )
    
    def _clean_text(self, text: str) -> str:
        """清理文本"""
        # 移除多余空白
        text = re.sub(r'\s+', ' ', text)
        # 移除HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        return text.strip()
    
    common_punctuation = set('，。！？；：""''（）【】《》、')
    
    def _count_chinese_chars(self, text: str) -> int:
        """统计中文字符数"""
        return sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    
    def _split_sentences(self, text: str) -> List[str]:
        """分割句子"""
        # 中英文标点
        sentences = re.split(r'[。！？；\n]+', text)
        return [s.strip() for s in sentences if s.strip()]
    
    def _count_syllables(self, word: str) -> int:
        """估算英文单词的音节数"""
        word = word.lower()
        count = 0
        vowels = 'aeiouy'
        prev_vowel = False
        
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel
        
        # 修正一些规则
        if word.endswith('e'):
            count -= 1
        if word.endswith('le') and len(word) > 2 and word[-3] not in vowels:
            count += 1
        if count == 0:
            count = 1
        
        return count
    
    def _calculate_chinese_score(self, avg_sentence_len: float, difficult_words: int, sentence_count: int) -> float:
        """计算中文可读性分数"""
        # 基于句子长度和难词比例
        sentence_score = max(0, 100 - (avg_sentence_len - 20) * 2)  # 20字句子最理想
        difficulty_ratio = difficult_words / max(sentence_count, 1)
        difficulty_score = max(0, 100 - difficulty_ratio * 5)
        
        # 综合分数
        score = sentence_score * 0.6 + difficulty_score * 0.4
        return min(100, max(0, score))
    
    def _get_chinese_grade(self, score: float) -> str:
        """根据分数确定中文年级水平"""
        if score >= 90:
            return "小学"
        elif score >= 80:
            return "初中"
        elif score >= 70:
            return "高中"
        elif score >= 60:
            return "大学"
        else:
            return "研究生+"
    
    def _get_english_grade(self, score: float) -> str:
        """根据Flesch分数确定年级水平"""
        if score >= 90:
            return "5th Grade (Elementary)"
        elif score >= 80:
            return "6th Grade"
        elif score >= 70:
            return "7th-8th Grade"
        elif score >= 60:
            return "High School"
        elif score >= 50:
            return "College"
        else:
            return "Graduate"
    
    def _generate_chinese_suggestions(self, avg_len: float, difficult: int, sentences: int) -> List[str]:
        """生成中文建议"""
        suggestions = []
        
        if avg_len > 30:
            suggestions.append(f"句子平均长度{avg_len:.0f}字较长，建议控制在20字以内")
        elif avg_len < 10:
            suggestions.append(f"句子平均长度{avg_len:.0f}字较短，可适当增加信息量")
        
        if difficult > sentences * 2:
            suggestions.append("难词比例较高，建议使用更通俗的表达")
        
        if sentences < 5:
            suggestions.append("段落较少，建议增加分段以提高可读性")
        
        if not suggestions:
            suggestions.append("可读性良好，继续保持")
        
        return suggestions
    
    def _generate_english_suggestions(self, avg_len: float, avg_syllables: float, difficult: int) -> List[str]:
        """生成英文建议"""
        suggestions = []
        
        if avg_len > 25:
            suggestions.append(f"Average sentence length ({avg_len:.1f} words) is high, aim for 15-20")
        
        if avg_syllables > 1.7:
            suggestions.append("Consider using simpler words to improve readability")
        
        if difficult > 10:
            suggestions.append(f"Found {difficult} complex words, consider simplifying")
        
        if not suggestions:
            suggestions.append("Readability is good, maintain this style")
        
        return suggestions
    
    def _find_chinese_issues(self, avg_len: float, difficult: int, sentences: int) -> List[str]:
        """找出中文问题"""
        issues = []
        
        if avg_len > 40:
            issues.append("句子过长，影响阅读体验")
        if difficult > sentences * 3:
            issues.append("生僻词使用过多")
        if sentences < 3:
            issues.append("段落太少")
        
        return issues
    
    def _find_english_issues(self, avg_len: float, avg_syllables: float, difficult: int) -> List[str]:
        """找出英文问题"""
        issues = []
        
        if avg_len > 30:
            issues.append("Sentences too long")
        if avg_syllables > 2.0:
            issues.append("Words too complex")
        if difficult > len([]) * 0.3:
            issues.append("Too many complex words")
        
        return issues
    
    def to_dict(self, result: ReadabilityResult) -> Dict:
        """转换为字典"""
        return {
            "score": result.score,
            "grade": result.grade,
            "avg_sentence_length": round(result.avg_sentence_length, 2),
            "avg_word_length": round(result.avg_word_length, 2),
            "difficult_words": result.difficult_words,
            "paragraph_count": result.paragraph_count,
            "suggestions": result.suggestions,
            "issues": result.issues
        }


# CrewAI Tool 包装
def get_readability_checker_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("readability_checker")
    def readability_checker_tool(text: str, language: str = "auto") -> str:
        """
        检查文章的可读性评分。
        
        Args:
            text: 要检查的文章内容
            language: 语言类型，可选 auto/chinese/english
            
        Returns:
            JSON格式的可读性分析结果
        """
        checker = ReadabilityChecker()
        result = checker.check(text, language)
        return json.dumps(checker.to_dict(result), ensure_ascii=False, indent=2)
    
    return readability_checker_tool


if __name__ == "__main__":
    # 测试
    checker = ReadabilityChecker()
    
    # 中文测试
    chinese_text = """
    清华大学经济管理学院是中国的顶尖商学院之一。该学院成立于1926年，拥有悠久的历史和卓越的学术传统。
    
    EMBA项目是清华大学经济管理学院的重要组成部分。该项目旨在培养具有国际视野的商业领袖。
    课程设置包括战略管理、财务管理、市场营销等多个模块。学生可以通过案例分析、企业实践等方式提升管理能力。
    报考EMBA需要具备本科学历和一定的工作经验。
    """
    
    result = checker.check(chinese_text, "chinese")
    print(json.dumps(checker.to_dict(result), ensure_ascii=False, indent=2))
