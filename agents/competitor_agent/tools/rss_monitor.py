#!/usr/bin/env python3

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
try:
    import feedparser
except Exception:
    feedparser = None


class RSSMonitor:
    def __init__(
        self,
        state_path: str = "logs/competitor_agent/rss_state.json",
        max_concurrency: int = 5,
        timeout_seconds: float = 20.0,
        retries: int = 1,
    ):
        self.state_path = state_path
        self.max_concurrency = max(1, int(max_concurrency))
        self.retries = max(0, int(retries))
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))
        self._state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path or not os.path.exists(self.state_path):
            return {"feeds": {}}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("feeds"), dict):
                return data
        except Exception:
            return {"feeds": {}}
        return {"feeds": {}}

    def _save_state(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _get_feed_state(self, url: str) -> Dict[str, Any]:
        feeds = self._state.setdefault("feeds", {})
        if url not in feeds or not isinstance(feeds.get(url), dict):
            feeds[url] = {}
        return feeds[url]

    def _update_feed_state(self, url: str, last_seen_link: str, last_seen_published: Optional[str]) -> None:
        s = self._get_feed_state(url)
        s["last_seen_link"] = last_seen_link
        if last_seen_published:
            s["last_seen_published"] = last_seen_published
        s["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def _clean_summary(self, summary: str, max_length: int = 200) -> str:
        summary = re.sub(r"<[^>]+>", "", summary or "")
        summary = " ".join(summary.split())
        if len(summary) > max_length:
            return summary[: max_length - 3] + "..."
        return summary

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        from email.utils import parsedate_to_datetime

        s = str(date_str).strip()
        try:
            dt = parsedate_to_datetime(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

        try:
            s2 = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

        formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]
        for fmt in formats:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        return None

    async def _get_with_retries(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                resp = await self.http_client.get(url, follow_redirects=True)
                resp.raise_for_status()
                return resp.text, resp.headers.get("content-type"), resp.status_code, None
            except Exception as e:
                last_error = str(e)
                if attempt >= self.retries:
                    break
                await asyncio.sleep(0.3 * (attempt + 1))
        return None, None, None, last_error

    async def fetch_feed(self, url: str, limit: int = 10) -> Dict[str, Any]:
        if feedparser is None:
            return {"success": False, "error": "feedparser_missing", "feed_url": url, "entries": []}
        text, content_type, status_code, error = await self._get_with_retries(url)
        if text is None:
            return {"success": False, "error": error or "fetch_failed", "feed_url": url, "entries": []}

        feed = feedparser.parse(text)
        entries: List[Dict[str, Any]] = []
        for entry in (feed.entries or [])[:limit]:
            published = entry.get("published") or entry.get("updated") or ""
            published_dt = self._parse_date(published)
            entries.append(
                {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": self._clean_summary(entry.get("summary", "")),
                    "published": published,
                    "published_dt": published_dt.isoformat() if published_dt else None,
                    "author": entry.get("author", ""),
                    "tags": [tag.term for tag in entry.get("tags", []) if getattr(tag, "term", None)],
                }
            )

        def sort_key(e: Dict[str, Any]) -> float:
            dt = self._parse_date(e.get("published") or "") or None
            return dt.timestamp() if dt else 0.0

        entries.sort(key=sort_key, reverse=True)

        return {
            "success": True,
            "feed_title": (feed.feed or {}).get("title", ""),
            "feed_url": url,
            "entries": entries,
            "entry_count": len(entries),
            "last_build_date": (feed.feed or {}).get("updated", ""),
            "content_type": content_type,
            "status_code": status_code,
        }

    async def fetch_multiple(self, urls: List[str], limit_per_feed: int = 5) -> Dict[str, Any]:
        sem = asyncio.Semaphore(self.max_concurrency)

        async def _one(u: str) -> Dict[str, Any]:
            async with sem:
                return await self.fetch_feed(u, limit_per_feed)

        tasks = [_one(u) for u in urls if u]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        all_entries: List[Dict[str, Any]] = []
        for r in results:
            if r.get("success"):
                for e in r.get("entries", []):
                    e["source"] = r.get("feed_title", r.get("feed_url"))
                    all_entries.append(e)

        def sort_key(e: Dict[str, Any]) -> float:
            dt = self._parse_date(e.get("published") or "") or None
            return dt.timestamp() if dt else 0.0

        all_entries.sort(key=sort_key, reverse=True)

        return {
            "success": True,
            "feeds_count": len(urls),
            "successful_feeds": sum(1 for r in results if r.get("success")),
            "failed_feeds": sum(1 for r in results if not r.get("success")),
            "total_entries": len(all_entries),
            "entries": all_entries[:50],
            "feeds": results,
        }

    async def filter_new_entries(self, *, url: str, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        state = self._get_feed_state(url)
        last_seen_link = state.get("last_seen_link") or ""
        last_seen_published = state.get("last_seen_published") or ""
        last_dt = self._parse_date(last_seen_published)

        def entry_dt(e: Dict[str, Any]) -> Optional[datetime]:
            dt_raw = e.get("published_dt")
            if isinstance(dt_raw, str) and dt_raw:
                try:
                    return datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
                except Exception:
                    pass
            return self._parse_date(e.get("published") or "")

        entries_sorted = list(entries or [])
        entries_sorted.sort(key=lambda e: (entry_dt(e).timestamp() if entry_dt(e) else 0.0), reverse=True)

        new_entries: List[Dict[str, Any]] = []
        for e in entries_sorted:
            if not isinstance(e, dict):
                continue
            link = e.get("link") or ""
            dt = entry_dt(e)
            if link and link == last_seen_link:
                continue
            if last_dt and dt and dt <= last_dt:
                continue
            new_entries.append(e)

        if entries_sorted:
            first = entries_sorted[0]
            self._update_feed_state(url=url, last_seen_link=first.get("link") or "", last_seen_published=first.get("published_dt") or first.get("published") or None)

        return new_entries

    async def check_updates(self, url: str, since: Optional[str] = None) -> Dict[str, Any]:
        if since:
            since_dt = self._parse_date(since)
        else:
            since_dt = datetime.now(timezone.utc) - timedelta(days=1)

        result = await self.fetch_feed(url, limit=50)
        if not result.get("success"):
            return result

        new_entries = []
        for entry in result.get("entries", []):
            entry_dt = self._parse_date(entry.get("published") or "")
            if entry_dt and since_dt and entry_dt > since_dt:
                new_entries.append(entry)

        return {
            "success": True,
            "feed_url": url,
            "feed_title": result.get("feed_title", ""),
            "new_entries_count": len(new_entries),
            "new_entries": new_entries,
            "check_time": datetime.now(timezone.utc).isoformat(),
            "since": since or since_dt.isoformat(),
        }

    async def discover_rss(self, website_url: str) -> List[str]:
        parsed = urlparse(website_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        discovered: List[str] = []

        try:
            resp = await self.http_client.get(website_url, follow_redirects=True)
            if resp.status_code == 200 and "html" in (resp.headers.get("content-type") or ""):
                html = resp.text or ""
                for m in re.finditer(
                    r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/(rss\+xml|atom\+xml)["\'][^>]+href=["\']([^"\']+)["\']',
                    html,
                    flags=re.IGNORECASE,
                ):
                    href = m.group(2)
                    if href:
                        discovered.append(urljoin(base_url + "/", href))
        except Exception:
            pass

        common_paths = ["/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/blog/feed", "/news/feed"]
        for path in common_paths:
            url = base_url + path
            try:
                r = await self.http_client.head(url, follow_redirects=True)
                if r.status_code == 200:
                    ct = r.headers.get("content-type", "")
                    if any(x in ct for x in ["xml", "rss", "atom"]):
                        discovered.append(url)
            except Exception:
                continue

        uniq = list(dict.fromkeys(discovered))
        return uniq
    
    def analyze_content_patterns(
        self,
        entries: List[Dict]
    ) -> Dict[str, Any]:
        """
        分析内容模式
        
        Args:
            entries: 内容条目列表
            
        Returns:
            分析结果
        """
        if not entries:
            return {"success": False, "error": "No entries to analyze"}
        
        # 提取关键词
        all_words = []
        for entry in entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = title + " " + summary
            all_words.extend(text.split())
        
        # 词频统计（简单实现）
        word_freq = {}
        for word in all_words:
            word = word.strip("，。！？、；：""''（）")
            if len(word) >= 2:
                word_freq[word] = word_freq.get(word, 0) + 1
        
        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:20]
        
        # 标题模式分析
        title_patterns = {
            "has_number": sum(1 for e in entries if any(c.isdigit() for c in e.get("title", ""))),
            "has_question": sum(1 for e in entries if "？" in e.get("title", "") or "?" in e.get("title", "")),
            "has_list": sum(1 for e in entries if any(w in e.get("title", "") for w in ["Top", "十大", "5个", "7个"])),
        }
        
        return {
            "success": True,
            "total_entries": len(entries),
            "top_keywords": [{"word": w, "count": c} for w, c in top_words],
            "title_patterns": title_patterns,
            "avg_title_length": sum(len(e.get("title", "")) for e in entries) / len(entries),
            "avg_summary_length": sum(len(e.get("summary", "")) for e in entries) / len(entries)
        }
    
    async def close(self):
        await self.http_client.aclose()


# CrewAI Tool 包装
def get_rss_monitor_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("rss_monitor")
    def rss_monitor_tool(
        action: str,
        url: str = "",
        urls: str = "",
        since: str = "",
        limit: int = 10
    ) -> str:
        """
        监控竞品RSS订阅源，发现新内容。
        
        Args:
            action: 操作类型 fetch/fetch_multiple/check_updates/discover/analyze
            url: 单个RSS源URL
            urls: 多个RSS源URL，用换行分隔
            since: 检查更新时间之后的内容 YYYY-MM-DD格式
            limit: 获取条目数量
            
        Returns:
            JSON格式的监控结果
        """
        import asyncio
        
        monitor = RSSMonitor()
        
        async def run():
            if action == "fetch":
                return await monitor.fetch_feed(url, limit)
            
            elif action == "fetch_multiple":
                url_list = [u.strip() for u in urls.split("\n") if u.strip()]
                return await monitor.fetch_multiple(url_list, limit)
            
            elif action == "check_updates":
                return await monitor.check_updates(url, since or None)
            
            elif action == "discover":
                discovered = await monitor.discover_rss(url)
                return {
                    "success": True,
                    "website": url,
                    "discovered_feeds": discovered
                }
            
            elif action == "analyze":
                # 需要先获取内容再分析
                result = await monitor.fetch_feed(url, limit)
                if result.get("success"):
                    return monitor.analyze_content_patterns(result.get("entries", []))
                return result
            
            else:
                return {"success": False, "error": f"未知操作: {action}"}
        
        try:
            result = asyncio.run(run())
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            asyncio.run(monitor.close())
    
    return rss_monitor_tool


if __name__ == "__main__":
    # 测试
    import asyncio
    
    async def test():
        monitor = RSSMonitor()
        
        # 测试获取
        result = await monitor.fetch_feed(
            "https://example.com/feed",
            limit=5
        )
        
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        await monitor.close()
    
    asyncio.run(test())
