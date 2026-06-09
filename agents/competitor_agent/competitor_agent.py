import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import yaml

from agents.competitor_agent.tools.gap_analyzer import GapAnalyzer
from agents.competitor_agent.tools.rss_monitor import RSSMonitor


def _deep_env_resolve(value: Any) -> Any:
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


class CompetitorAgent:
    def __init__(self, config_path: str = "agents/competitor_agent/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _get_our_domain(self) -> str:
        return ((self.config or {}).get("own_site") or {}).get("domain") or os.environ.get("OWN_DOMAIN") or ""

    def _get_state_path(self) -> str:
        state_path = (((self.config or {}).get("monitoring") or {}).get("state") or {}).get("path") or ""
        return state_path or "logs/competitor_agent/rss_state.json"

    def _get_own_content_path(self) -> str:
        p = (((self.config or {}).get("monitoring") or {}).get("own_content") or {}).get("path") or ""
        return p or "data/own_contents.json"

    def _load_own_contents(self) -> Tuple[Dict[str, Any], List[str], List[str]]:
        path = self._get_own_content_path()
        if not os.path.exists(path):
            return (
                {"titles": [], "total_words": 0, "article_count": 0, "content_types": []},
                [],
                [f"own_content_file_missing:{path}"],
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return (
                {"titles": [], "total_words": 0, "article_count": 0, "content_types": []},
                [],
                [f"own_content_file_invalid:{path}:{str(e)}"],
            )

        if not isinstance(data, list):
            return (
                {"titles": [], "total_words": 0, "article_count": 0, "content_types": []},
                [],
                [f"own_content_file_not_list:{path}"],
            )

        titles: List[str] = []
        content_types: List[str] = []
        total_words = 0
        keywords: List[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("title"):
                titles.append(str(item["title"]))
            if item.get("content_type"):
                content_types.append(str(item["content_type"]))
            wc = item.get("word_count")
            if isinstance(wc, int):
                total_words += wc
            kw = item.get("keywords")
            if isinstance(kw, list):
                keywords.extend([str(k) for k in kw if isinstance(k, (str, int))])
            elif isinstance(kw, str):
                keywords.extend([k.strip() for k in kw.split(",") if k.strip()])

        own_content = {
            "titles": titles,
            "total_words": total_words,
            "article_count": len(titles),
            "content_types": list(dict.fromkeys(content_types)),
        }
        uniq_kw = list(dict.fromkeys([k for k in keywords if k]))
        return own_content, uniq_kw, []

    async def execute(self, **overrides: Any) -> Dict[str, Any]:
        cfg = self.config or {}
        monitoring = cfg.get("monitoring") or {}
        competitors = overrides.get("competitors") or cfg.get("competitors") or []

        max_concurrency = int((monitoring.get("concurrency") or {}).get("max") or 5)
        timeout_seconds = float((monitoring.get("http") or {}).get("timeout_seconds") or 20.0)
        retries = int((monitoring.get("http") or {}).get("retries") or 1)
        limit_per_feed = int((monitoring.get("rss") or {}).get("limit_per_feed") or 20)

        state_path = overrides.get("state_path") or self._get_state_path()
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

        rss = RSSMonitor(
            state_path=state_path,
            max_concurrency=max_concurrency,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )

        errors: List[str] = []
        try:
            own_content, own_keywords, own_errors = self._load_own_contents()
            errors.extend(own_errors)

            feed_urls: List[str] = []
            competitor_meta: Dict[str, Dict[str, str]] = {}
            for c in competitors:
                if not isinstance(c, dict):
                    continue
                rss_url = c.get("rss_url") or ""
                if rss_url:
                    feed_urls.append(rss_url)
                    competitor_meta[rss_url] = {
                        "name": str(c.get("name") or rss_url),
                        "domain": str(c.get("domain") or ""),
                    }

            fetched = await rss.fetch_multiple(feed_urls, limit_per_feed=limit_per_feed)
            if not fetched.get("success"):
                return {
                    "success": False,
                    "workflow": "competitor",
                    "timestamp": datetime.now().isoformat(),
                    "error": fetched.get("error") or "rss_fetch_failed",
                }

            new_by_feed: Dict[str, List[Dict[str, Any]]] = {}
            for feed in fetched.get("feeds", []):
                if not isinstance(feed, dict):
                    continue
                url = feed.get("feed_url") or ""
                if not url:
                    continue
                new_entries = await rss.filter_new_entries(url=url, entries=feed.get("entries") or [])
                if new_entries:
                    new_by_feed[url] = new_entries

            competitor_contents: List[Dict[str, Any]] = []
            competitor_keywords: List[List[str]] = []

            analyzer = GapAnalyzer(our_domain=self._get_our_domain())

            for url, entries in new_by_feed.items():
                titles = [e.get("title") or "" for e in entries if isinstance(e, dict)]
                comp = {
                    "titles": titles,
                    "total_words": 0,
                    "article_count": len(titles),
                    "content_types": [],
                    "source": competitor_meta.get(url, {}).get("name") or url,
                    "domain": competitor_meta.get(url, {}).get("domain") or "",
                }
                competitor_contents.append(comp)

                kw = analyzer.extract_keywords_from_entries(entries)
                competitor_keywords.append(kw)

            gap_result = analyzer.analyze(
                own_content=own_content,
                competitor_contents=competitor_contents,
                own_keywords=own_keywords,
                competitor_keywords=competitor_keywords,
            )

            content_plan = analyzer.generate_content_plan(
                opportunities=gap_result.get("opportunities") or [],
                keyword_cluster=gap_result.get("keyword_cluster") or {},
            )

            return {
                "success": True,
                "workflow": "competitor",
                "timestamp": datetime.now().isoformat(),
                "competitors_count": len(feed_urls),
                "rss": {
                    "feeds_count": fetched.get("feeds_count"),
                    "successful_feeds": fetched.get("successful_feeds"),
                    "failed_feeds": fetched.get("failed_feeds"),
                    "new_entries_count": sum(len(v) for v in new_by_feed.values()),
                    "new_entries_by_feed": {
                        url: {
                            "competitor": competitor_meta.get(url) or {"name": url, "domain": ""},
                            "entries": entries,
                        }
                        for url, entries in new_by_feed.items()
                    },
                },
                "analysis": {
                    "gap_result": gap_result,
                    "content_plan": content_plan,
                },
                "errors": errors,
            }
        finally:
            await rss.close()

