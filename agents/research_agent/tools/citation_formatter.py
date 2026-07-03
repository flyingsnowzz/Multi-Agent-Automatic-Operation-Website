#!/usr/bin/env python3
"""
引用格式化工具 - ResearchAgent
将收集的资料格式化为标准引用格式
"""

import re
from typing import Dict, List, Any, Optional
from datetime import datetime
from enum import Enum


class CitationStyle(str, Enum):
    """引用样式枚举"""
    APA = "apa"
    MLA = "mla"
    CHICAGO = "chicago"
    GB_T7714 = "gb_t7714"  # 中国国家标准
    HARVARD = "harvard"


class CitationFormatter:
    """引用格式化工具"""
    
    def __init__(self, style: CitationStyle = CitationStyle.GB_T7714):
        """
        初始化格式化器
        
        Args:
            style: 引用样式，默认使用中国国家标准
        """
        self.style = style
    
    def format(self, source: Dict[str, Any]) -> str:
        """
        格式化单条引用
        
        Args:
            source: 来源信息字典
            
        Returns:
            格式化后的引用字符串
        """
        src = dict(source or {})
        src["authors"] = self._normalize_authors(src.get("authors"))

        if self.style == CitationStyle.GB_T7714:
            return self._format_gb_t7714(src)
        elif self.style == CitationStyle.APA:
            return self._format_apa(src)
        elif self.style == CitationStyle.MLA:
            return self._format_mla(src)
        elif self.style == CitationStyle.CHICAGO:
            return self._format_chicago(src)
        elif self.style == CitationStyle.HARVARD:
            return self._format_harvard(src)
        else:
            return str(src)
    
    def format_batch(self, sources: List[Dict]) -> List[str]:
        """
        批量格式化引用
        
        Args:
            sources: 来源信息列表
            
        Returns:
            格式化后的引用列表
        """
        return [self.format(source) for source in sources]
    
    def _format_gb_t7714(self, source: Dict) -> str:
        """
        GB/T 7714-2015 格式（中国国家标准）
        
        格式：
        [序号] 作者. 标题[J]. 期刊名, 年, 卷(期): 页码.
        [序号] 作者. 标题[M]. 出版地: 出版社, 年.
        [序号] 作者. 标题[EB/OL]. 网址, 更新日期/获取日期.
        """
        source_type = source.get('type', '').lower()
        
        if source_type == 'journal':
            # 期刊文章
            authors = self._format_authors_gb(source.get('authors', []))
            title = source.get('title', '')
            journal = source.get('journal', '')
            year = source.get('year', '')
            volume = source.get('volume', '')
            issue = source.get('issue', '')
            pages = source.get('pages', '')
            
            result = f"{authors}. {title}[J]"
            if journal:
                result += f". {journal}"
            if year:
                result += f", {year}"
            if volume:
                result += f", {volume}"
            if issue:
                result += f"({issue})"
            if pages:
                result += f": {pages}"
            result += "."
            
            return result
        
        elif source_type == 'book':
            # 图书
            authors = self._format_authors_gb(source.get('authors', []))
            title = source.get('title', '')
            location = source.get('location', '')
            publisher = source.get('publisher', '')
            year = source.get('year', '')
            
            result = f"{authors}. {title}[M]"
            if location and publisher:
                result += f". {location}: {publisher}"
            if year:
                result += f", {year}"
            result += "."
            
            return result
        
        elif source_type in ['web', 'online', 'website']:
            # 网络资源
            authors = self._format_authors_gb(source.get('authors', []))
            title = source.get('title', '')
            url = source.get('url', '')
            access_date = source.get('access_date', '')
            publish_date = source.get('publish_date', '')
            
            result = f"{authors}. {title}[EB/OL]"
            if url:
                result += f". {url}"
            if publish_date:
                result += f", {publish_date}"
            if access_date:
                result += f"/{access_date}"
            result += "."
            
            return result
        
        else:
            # 默认格式
            return f"{source.get('author', '')}. {source.get('title', '')}. {source.get('source', '')}."
    
    def _format_apa(self, source: Dict) -> str:
        """APA格式"""
        authors = self._format_authors_apa(source.get('authors', []))
        year = source.get('year', 'n.d.')
        title = source.get('title', '')
        source_type = source.get('type', '').lower()
        
        if source_type == 'journal':
            journal = source.get('journal', '')
            volume = source.get('volume', '')
            issue = source.get('issue', '')
            pages = source.get('pages', '')
            doi = source.get('doi', '')
            
            result = f"{authors} ({year}). {title}. {journal}, {volume}({issue}), {pages}"
            if doi:
                result += f". https://doi.org/{doi}"
            
            return result + "."
        
        elif source_type == 'book':
            location = source.get('location', '')
            publisher = source.get('publisher', '')
            
            result = f"{authors} ({year}). {title}. {location}: {publisher}."
            
            return result
        
        else:
            url = source.get('url', '')
            result = f"{authors} ({year}). {title}."
            if url:
                result += f" Retrieved from {url}"
            
            return result
    
    def _format_mla(self, source: Dict) -> str:
        """MLA格式"""
        authors = self._format_authors_mla(source.get('authors', []))
        title = source.get('title', '')
        container = source.get('journal', source.get('container', ''))
        volume = source.get('volume', '')
        issue = source.get('issue', '')
        year = source.get('year', '')
        pages = source.get('pages', '')
        url = source.get('url', '')
        
        result = f"{authors} \"{title}.\" {container}"
        if volume:
            result += f", vol. {volume}"
        if issue:
            result += f", no. {issue}"
        if year:
            result += f", {year}"
        if pages:
            result += f", pp. {pages}"
        result += "."
        
        if url:
            result += f" {url}."
        
        return result
    
    def _format_chicago(self, source: Dict) -> str:
        """Chicago格式"""
        authors = self._format_authors_chicago(source.get('authors', []))
        title = source.get('title', '')
        journal = source.get('journal', '')
        year = source.get('year', '')
        volume = source.get('volume', '')
        issue = source.get('issue', '')
        pages = source.get('pages', '')
        
        result = f"{authors} \"{title}.\""
        if journal:
            result += f" {journal}"
        if volume:
            result += f" {volume}"
        if issue:
            result += f", no. {issue}"
        if year:
            result += f" ({year})"
        if pages:
            result += f": {pages}"
        result += "."
        
        return result
    
    def _format_harvard(self, source: Dict) -> str:
        """Harvard格式"""
        authors = self._format_authors_harvard(source.get('authors', []))
        year = source.get('year', 'n.d.')
        title = source.get('title', '')
        journal = source.get('journal', '')
        volume = source.get('volume', '')
        issue = source.get('issue', '')
        pages = source.get('pages', '')
        
        result = f"{authors} ({year}) '{title}'"
        if journal:
            result += f", {journal}"
        if volume:
            result += f", {volume}"
        if issue:
            result += f"({issue})"
        if pages:
            result += f", pp. {pages}"
        result += "."
        
        return result
    
    def _format_authors_gb(self, authors: List[str]) -> str:
        """格式化作者（中国国家标准）"""
        if not authors:
            return ''
        if len(authors) <= 3:
            return ', '.join(authors)
        else:
            return ', '.join(authors[:3]) + ', 等'
    
    def _format_authors_apa(self, authors: List[str]) -> str:
        """格式化作者（APA）"""
        if not authors:
            return ''
        if len(authors) == 1:
            return authors[0]
        elif len(authors) == 2:
            return f"{authors[0]} & {authors[1]}"
        else:
            return f"{authors[0]}, et al."
    
    def _format_authors_mla(self, authors: List[str]) -> str:
        """格式化作者（MLA）"""
        if not authors:
            return ''
        if len(authors) == 1:
            return authors[0]
        elif len(authors) == 2:
            return f"{authors[0]} and {authors[1]}"
        else:
            return f"{authors[0]}, et al."
    
    def _format_authors_chicago(self, authors: List[str]) -> str:
        """格式化作者（Chicago）"""
        if not authors:
            return ''
        if len(authors) == 1:
            return authors[0]
        elif len(authors) == 2:
            return f"{authors[0]} and {authors[1]}"
        else:
            return f"{authors[0]} et al."
    
    def _format_authors_harvard(self, authors: List[str]) -> str:
        """格式化作者（Harvard）"""
        if not authors:
            return ''
        if len(authors) == 1:
            return authors[0]
        elif len(authors) == 2:
            return f"{authors[0]} and {authors[1]}"
        else:
            return f"{authors[0]} et al."

    def _normalize_authors(self, authors: Any) -> List[str]:
        if authors is None:
            return []
        if isinstance(authors, str):
            s = authors.strip()
            if not s:
                return []
            parts = re.split(r"[;,/，；]+", s)
            return [p.strip() for p in parts if p.strip()]
        if isinstance(authors, list):
            out: List[str] = []
            for a in authors:
                if a is None:
                    continue
                if isinstance(a, str):
                    if a.strip():
                        out.append(a.strip())
                else:
                    out.append(str(a))
            return out
        return [str(authors)]
    
    def extract_citations_from_text(self, text: str) -> List[str]:
        """
        从文本中提取引用标记
        
        Args:
            text: 文本内容
            
        Returns:
            提取的引用列表
        """
        # 匹配 [1], [2,3], [1-5] 等格式
        pattern = r'\[(\d+(?:[-,]\d+)*)\]'
        citations = re.findall(pattern, text)
        
        result = []
        for citation in citations:
            if '-' in citation:
                parts = citation.split('-')
                result.extend([str(i) for i in range(int(parts[0]), int(parts[1]) + 1)])
            else:
                result.extend(citation.split(','))
        
        return list(set(result))


# CrewAI Tool 包装
def get_citation_formatter_tool():
    """返回CrewAI可用的Tool"""
    try:
        from crewai.tools import tool
    except Exception:
        class _FallbackTool:
            def __init__(self, func):
                self.func = func

            def __call__(self, *args, **kwargs):
                return self.func(*args, **kwargs)

            def run(self, *args, **kwargs):
                return self.func(*args, **kwargs)

        def tool(*_args, **_kwargs):
            def decorator(func):
                return _FallbackTool(func)
            return decorator
    
    @tool("citation_formatter")
    def citation_formatter_tool(sources_json: str, style: str = "gb_t7714") -> str:
        """
        将资料格式化为标准引用格式。
        
        Args:
            sources_json: 来源信息列表的JSON字符串
            style: 引用样式，可选：apa, mla, chicago, gb_t7714, harvard
            
        Returns:
            格式化后的引用列表
        """
        import json
        try:
            sources = json.loads(sources_json)
        except Exception as e:
            return f"ERROR: invalid_sources_json: {e}"

        if not isinstance(sources, list):
            return "ERROR: sources_json_must_be_list"
        if not all(isinstance(x, dict) for x in sources):
            return "ERROR: sources_json_items_must_be_object"
        
        try:
            citation_style = CitationStyle(style)
        except ValueError:
            citation_style = CitationStyle.GB_T7714
        
        formatter = CitationFormatter(citation_style)
        citations = formatter.format_batch(sources)
        
        return '\n'.join([f"[{i+1}] {c}" for i, c in enumerate(citations)])
    
    return citation_formatter_tool


if __name__ == "__main__":
    # 测试
    sources = [
        {
            'type': 'journal',
            'authors': ['张三', '李四', '王五'],
            'title': '高管教育发展趋势研究',
            'journal': '管理世界',
            'year': '2024',
            'volume': '40',
            'issue': '3',
            'pages': '45-58'
        },
        {
            'type': 'web',
            'authors': ['赵六'],
            'title': '2024年MBA就业报告',
            'url': 'https://example.com/report',
            'publish_date': '2024-05',
            'access_date': '2024-06-01'
        }
    ]
    
    formatter = CitationFormatter(CitationStyle.GB_T7714)
    for i, citation in enumerate(formatter.format_batch(sources)):
        print(f"[{i+1}] {citation}")
