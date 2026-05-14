"""
Deduplication Checker Tool

去重检测（与已发布内容对比）。
支持余弦相似度、Jaccard相似度、Levenshtein距离三种算法。
"""

from typing import Dict, List, Any, Optional
from crewai.tools import tool


class DedupChecker:
    """去重检测器"""

    def __init__(self, config: Optional[Dict] = None):
        """
        初始化去重检测器。

        Args:
            config: 去重配置，包含：
                - threshold: 相似度阈值（0-1，默认0.8）
                - algorithm: 算法（cosine/jaccard/levenshtein，默认cosine）
        """
        self.config = config or {}
        self.threshold = self.config.get("threshold", 0.8)
        self.algorithm = self.config.get("algorithm", "cosine")

    async def check(
        self,
        title: str,
        content: str,
        published_articles: Optional[List[Dict]] = None,
        threshold: Optional[float] = None,
        algorithm: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        去重检测。

        Args:
            title: 待检测内容标题
            content: 待检测内容正文
            published_articles: 已发布文章列表，每项含 title/content 字段
            threshold: 相似度阈值（可选）
            algorithm: 算法名称（可选）

        Returns:
            {
                "success": true,
                "is_duplicate": false,
                "similarity_score": 0.3,
                "matched_article": null,
                "details": {"title_similarity": 0.2, "content_similarity": 0.4}
            }
        """
        try:
            threshold = threshold or self.threshold
            algorithm = algorithm or self.algorithm

            if published_articles is None:
                published_articles = await self._query_published_articles()

            max_similarity = 0.0
            matched_article = None

            for article in published_articles:
                sim = self._calculate_similarity(
                    title, content,
                    article.get("title", ""),
                    article.get("content", ""),
                    algorithm
                )
                if sim > max_similarity:
                    max_similarity = sim
                    matched_article = article

            is_duplicate = max_similarity >= threshold

            details = {}
            if matched_article:
                details["title_similarity"] = self._cosine_similarity(
                    title, matched_article.get("title", "")
                )
                details["content_similarity"] = self._cosine_similarity(
                    content, matched_article.get("content", "")
                )

            return {
                "success": True,
                "is_duplicate": is_duplicate,
                "similarity_score": round(max_similarity, 4),
                "matched_article": matched_article,
                "details": details
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _query_published_articles(self) -> List[Dict]:
        """
        查询已发布文章。
        TODO: 对接CMS数据库（MySQL/MongoDB），查询已发布文章的 title + content。
        目前返回空列表。
        """
        return []

    def _calculate_similarity(
        self,
        title1: str, content1: str,
        title2: str, content2: str,
        algorithm: str
    ) -> float:
        """计算两篇文章的综合相似度"""
        text1 = f"{title1} {content1}"
        text2 = f"{title2} {content2}"

        if algorithm == "jaccard":
            return self._jaccard_similarity(text1, text2)
        elif algorithm == "levenshtein":
            return self._levenshtein_similarity(text1, text2)
        else:
            return self._cosine_similarity(text1, text2)

    def _cosine_similarity(self, text1: str, text2: str) -> float:
        """余弦相似度（字符级TF向量）"""
        def char_tf(text: str) -> Dict[str, int]:
            tf = {}
            for ch in text:
                tf[ch] = tf.get(ch, 0) + 1
            return tf

        tf1 = char_tf(text1)
        tf2 = char_tf(text2)
        all_chars = set(tf1.keys()) | set(tf2.keys())

        dot = sum(tf1.get(c, 0) * tf2.get(c, 0) for c in all_chars)
        norm1 = sum(v ** 2 for v in tf1.values()) ** 0.5
        norm2 = sum(v ** 2 for v in tf2.values()) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def _jaccard_similarity(self, text1: str, text2: str) -> float:
        """Jaccard相似度（字符级集合）"""
        set1 = set(text1)
        set2 = set(text2)
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0

    def _levenshtein_similarity(self, text1: str, text2: str) -> float:
        """Levenshtein相似度（1 - 归一化编辑距离）"""
        # 长文本截断避免O(n*m)过大
        t1 = text1[:500]
        t2 = text2[:500]
        len1, len2 = len(t1), len(t2)

        if len1 == 0 or len2 == 0:
            return 0.0

        # 动态规划
        prev = list(range(len2 + 1))
        for i in range(1, len1 + 1):
            curr = [i] + [0] * len2
            for j in range(1, len2 + 1):
                if t1[i - 1] == t2[j - 1]:
                    curr[j] = prev[j - 1]
                else:
                    curr[j] = min(prev[j], curr[j - 1], prev[j - 1]) + 1
            prev = curr

        edit_distance = prev[len2]
        max_len = max(len1, len2)
        return 1.0 - (edit_distance / max_len)


@tool
def get_dedup_checker_tool(config: Optional[Dict] = None) -> DedupChecker:
    """获取去重检测器工具"""
    return DedupChecker(config)


async def check_duplicate(
    title: str,
    content: str,
    published_articles: Optional[List[Dict]] = None,
    threshold: Optional[float] = None,
    algorithm: Optional[str] = None,
    config: Optional[Dict] = None
) -> Dict[str, Any]:
    """去重检测便捷函数"""
    checker = DedupChecker(config)
    return await checker.check(title, content, published_articles, threshold, algorithm)


if __name__ == "__main__":
    import asyncio

    async def test():
        # 模拟已发布文章
        articles = [
            {"title": "测试标题", "content": "这是测试内容。" * 50},
            {"title": "另一篇文章", "content": "完全不同的内容。" * 50},
        ]
        result = await check_duplicate(
            title="测试标题2",
            content="这是测试内容。" * 30
        )
        print(result)
        # 带已发布文章对比
        result2 = await check_duplicate(
            title="测试标题",
            content="这是测试内容。" * 50,
            published_articles=articles
        )
        print(result2)

    asyncio.run(test())
