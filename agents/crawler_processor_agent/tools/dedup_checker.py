"""
Deduplication Checker Tool

去重检测（与已发布内容对比）。
支持余弦相似度、Jaccard相似度、Levenshtein距离三种算法。
"""

import re
from html import unescape
from typing import Dict, List, Any, Optional, Tuple
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
        self.published_db_config = self.config.get("published_db_config") or self.config.get("published_content_db") or {}
        self.published_limit = int(self.config.get("published_limit") or 2000)

    def _validate_identifier(self, name: str) -> bool:
        if not isinstance(name, str) or not name:
            return False
        return re.fullmatch(r"[A-Za-z0-9_]+", name) is not None

    def _quote_mysql_ident(self, name: str) -> str:
        return f"`{name}`"

    async def check(
        self,
        title: str,
        content: str,
        source_url: Optional[str] = None,
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
                published_articles, warn = await self._query_published_articles(limit=self.published_limit)
            else:
                warn = None

            if source_url:
                matched = self._match_by_url(source_url, published_articles)
                if matched is not None:
                    return {
                        "success": True,
                        "is_duplicate": True,
                        "similarity_score": 1.0,
                        "matched_article": matched,
                        "details": {"match_type": "url", "warning": warn},
                    }

            title_norm = self._normalize_title(title)
            if title_norm:
                matched = self._match_by_title_norm(title_norm, published_articles)
                if matched is not None:
                    return {
                        "success": True,
                        "is_duplicate": True,
                        "similarity_score": 1.0,
                        "matched_article": matched,
                        "details": {"match_type": "title_norm", "warning": warn},
                    }

            clean_title = self._normalize_text_for_similarity(title)
            clean_content = self._normalize_text_for_similarity(content)

            # 优化：如果是 cosine 算法，提前分词计算当前文章的 TF 向量
            current_tf = None
            if algorithm == "cosine" or not algorithm:
                current_tf = self._token_tf(f"{clean_title} {clean_content}")

            max_similarity = 0.0
            matched_article = None

            for article in published_articles:
                if algorithm == "cosine" or not algorithm:
                    # 优化已发布文章分词词频 TF 缓存
                    if "_tf_cache" not in article:
                        pub_title = self._normalize_text_for_similarity(article.get("title", ""))
                        pub_content = self._normalize_text_for_similarity(article.get("content", ""))
                        article["_tf_cache"] = self._token_tf(f"{pub_title} {pub_content}")
                    sim = self._cosine_similarity(current_tf, article["_tf_cache"])
                else:
                    sim = self._calculate_similarity(
                        clean_title,
                        clean_content,
                        self._normalize_text_for_similarity(article.get("title", "")),
                        self._normalize_text_for_similarity(article.get("content", "")),
                        algorithm
                    )
                if sim > max_similarity:
                    max_similarity = sim
                    matched_article = article

            is_duplicate = max_similarity >= threshold

            details = {}
            if matched_article:
                details["title_similarity"] = self._cosine_similarity(clean_title, self._normalize_text_for_similarity(matched_article.get("title", "")))
                details["content_similarity"] = self._cosine_similarity(clean_content, self._normalize_text_for_similarity(matched_article.get("content", "")))
                details["match_type"] = "similarity"
                if warn:
                    details["warning"] = warn

            return {
                "success": True,
                "is_duplicate": is_duplicate,
                "similarity_score": round(max_similarity, 4),
                "matched_article": matched_article,
                "details": details
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _query_published_articles(self, *, limit: int = 2000) -> Tuple[List[Dict], Optional[str]]:
        cfg = self.published_db_config or {}
        db_type = (cfg.get("type") or "").strip().lower()
        if not db_type:
            return [], "published_db_config_missing"
        if db_type == "mysql":
            return await self._query_mysql_published(cfg, limit=limit)
        if db_type == "mongodb":
            return await self._query_mongodb_published(cfg, limit=limit)
        return [], f"published_db_type_not_supported:{db_type}"

    async def _query_mysql_published(self, cfg: Dict[str, Any], *, limit: int) -> Tuple[List[Dict], Optional[str]]:
        try:
            import aiomysql
        except Exception as e:
            return [], f"aiomysql_missing:{str(e)}"

        table = str(cfg.get("table") or "")
        title_field = str(cfg.get("title_field") or "title")
        content_field = str(cfg.get("content_field") or "content")
        url_field = str(cfg.get("source_url_field") or "source_url")

        if not self._validate_identifier(table):
            return [], "invalid_identifier:table"
        for f in (title_field, content_field, url_field):
            if f and not self._validate_identifier(f):
                return [], f"invalid_identifier:field:{f}"

        host = cfg.get("host") or "localhost"
        port = int(cfg.get("port") or 3306)
        database = cfg.get("database") or ""
        user = cfg.get("user") or ""
        password = cfg.get("password") or ""
        charset = cfg.get("charset") or "utf8mb4"

        select_fields = [title_field, content_field]
        if url_field:
            select_fields.append(url_field)
        q_fields = ", ".join(self._quote_mysql_ident(f) for f in select_fields)
        q_table = self._quote_mysql_ident(table)

        query = f"SELECT {q_fields} FROM {q_table} ORDER BY id DESC LIMIT %s"
        try:
            conn = await aiomysql.connect(host=host, port=port, user=user, password=password, db=database, charset=charset)
        except Exception as e:
            return [], f"mysql_connect_failed:{str(e)}"

        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, (int(limit),))
                rows = await cursor.fetchall()
        except Exception as e:
            return [], f"mysql_query_failed:{str(e)}"
        finally:
            conn.close()

        articles: List[Dict[str, Any]] = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            articles.append(
                {
                    "title": r.get(title_field) or "",
                    "content": r.get(content_field) or "",
                    "source_url": (r.get(url_field) or "") if url_field else "",
                }
            )
        return articles, None

    async def _query_mongodb_published(self, cfg: Dict[str, Any], *, limit: int) -> Tuple[List[Dict], Optional[str]]:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except Exception as e:
            return [], f"motor_missing:{str(e)}"

        host = cfg.get("host") or "localhost"
        port = int(cfg.get("port") or 27017)
        database = cfg.get("database") or ""
        collection_name = cfg.get("collection") or cfg.get("table") or ""
        title_field = str(cfg.get("title_field") or "title")
        content_field = str(cfg.get("content_field") or "content")
        url_field = str(cfg.get("source_url_field") or "source_url")

        if not collection_name:
            return [], "collection_missing"

        uri = f"mongodb://{host}:{port}"
        try:
            client = AsyncIOMotorClient(uri)
            db = client[database]
            col = db[collection_name]
            projection = {title_field: 1, content_field: 1}
            if url_field:
                projection[url_field] = 1
            cursor = col.find({}, projection=projection).sort("_id", -1).limit(int(limit))
            docs = await cursor.to_list(length=int(limit))
        except Exception as e:
            return [], f"mongo_query_failed:{str(e)}"
        finally:
            try:
                client.close()
            except Exception:
                pass

        articles: List[Dict[str, Any]] = []
        for d in docs or []:
            if not isinstance(d, dict):
                continue
            articles.append(
                {
                    "title": d.get(title_field) or "",
                    "content": d.get(content_field) or "",
                    "source_url": (d.get(url_field) or "") if url_field else "",
                }
            )
        return articles, None

    def _normalize_url(self, url: str) -> str:
        """
        URL 归一化处理：
        1. 去除尾部的空格和斜杠 /
        2. 剔除常见的广告和追踪查询参数（如 utm_*, ref, spm, click_id, client 等）
        """
        if not url:
            return ""
        url = url.strip()
        
        # 拆分 URL 与 Query String
        if "?" in url:
            try:
                base, query = url.split("?", 1)
                params = []
                for pair in query.split("&"):
                    if not pair:
                        continue
                    parts = pair.split("=", 1)
                    key = parts[0].strip().lower()
                    # 常见营销/追踪/客户端参数前缀
                    ignored_prefixes = ("utm_", "spm", "ref", "click", "client", "fbclid", "gclid")
                    if any(key.startswith(p) for p in ignored_prefixes):
                        continue
                    params.append(pair)
                
                # 重新拼接并排序参数以保证顺序一致性
                if params:
                    params.sort()
                    url = f"{base}?{'&'.join(params)}"
                else:
                    url = base
            except Exception:
                pass
        
        # 统一去除末尾斜杠
        if url.endswith("/"):
            url = url[:-1]
            
        return url

    def _match_by_url(self, url: str, articles: List[Dict]) -> Optional[Dict]:
        url_norm = self._normalize_url(url)
        if not url_norm:
            return None
        for a in articles:
            if not isinstance(a, dict):
                continue
            if self._normalize_url(a.get("source_url") or "") == url_norm:
                return a
        return None

    def _normalize_title(self, title: str) -> str:
        t = unescape(title or "").strip().lower()
        t = re.sub(r"\s+", "", t)
        t = re.sub(r"[`~!@#$%^&*()_\-+=\[\]{}\\|;:'\",.<>/?，。！？；：、】【（）《》“”‘’、\s]+", "", t)
        return t

    def _match_by_title_norm(self, title_norm: str, articles: List[Dict]) -> Optional[Dict]:
        for a in articles:
            if not isinstance(a, dict):
                continue
            if self._normalize_title(a.get("title", "")) == title_norm:
                return a
        return None

    def _strip_html(self, text: str) -> str:
        t = unescape(text or "")
        t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", t)
        t = re.sub(r"(?s)<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _normalize_text_for_similarity(self, text: str) -> str:
        return self._strip_html(text)

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

    def _token_tf(self, text: str) -> Dict[str, int]:
        """中文分词(Jieba)与英文正则词频统计"""
        t = (text or "").lower()
        try:
            import jieba
            raw_tokens = jieba.cut(t)
            tokens = []
            for tok in raw_tokens:
                tok = tok.strip()
                if not tok:
                    continue
                # 过滤并仅保留有效的英文单词/数字，以及有效的中文词汇
                if re.match(r"^[a-z0-9]+$", tok) or re.match(r"^[\u4e00-\u9fff]+$", tok):
                    tokens.append(tok)
        except Exception:
            # 降级回退到 unigram 中文单字分词 + 英文正则分词
            en = re.findall(r"[a-z0-9]+", t)
            zh = re.findall(r"[\u4e00-\u9fff]", t)
            tokens = en + zh

        tf: Dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1
        return tf

    def _cosine_similarity(self, val1: Any, val2: Any) -> float:
        tf1 = val1 if isinstance(val1, dict) else self._token_tf(val1)
        tf2 = val2 if isinstance(val2, dict) else self._token_tf(val2)
        all_tokens = set(tf1.keys()) | set(tf2.keys())

        dot = sum(tf1.get(c, 0) * tf2.get(c, 0) for c in all_tokens)
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
    source_url: Optional[str] = None,
    published_articles: Optional[List[Dict]] = None,
    threshold: Optional[float] = None,
    algorithm: Optional[str] = None,
    config: Optional[Dict] = None
) -> Dict[str, Any]:
    """去重检测便捷函数"""
    checker = DedupChecker(config)
    return await checker.check(title, content, source_url, published_articles, threshold, algorithm)


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
