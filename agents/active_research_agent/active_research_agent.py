"""Find and score online topic candidates before article generation.

This agent is intentionally cheap in v1:
- it reads a manually maintained keyword YAML file;
- it queries RSS feeds, mainly Google News RSS;
- it deduplicates candidates locally;
- it gives a transparent rule-based topic_score.

It does not call WriterAgent, ImageAgent, CMS, or any paid LLM provider. The
next implementation step can feed accepted candidates into ResearchAgent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlparse, urlunparse

import httpx
import yaml


LOG = logging.getLogger("active_research.agent")
ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_KEYWORD_CONFIG = ROOT / "config" / "active_research_keywords.yaml"
DEFAULT_USER_AGENT = "MultiAgentActiveResearch/1.0"


@dataclass
class ActiveResearchCandidate:
    """One discovered online topic candidate with traceable scoring details."""

    title: str
    url: str
    source_name: str
    source_url: str
    source_type: str
    original_url: str
    original_url_status: str
    keyword: str
    keyword_group: str
    published_at: Optional[str]
    summary: str
    fetched_at: str
    dedup_key: str
    topic_score: float
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _env_int(name: str, default: int) -> int:
    """Read an integer environment setting with a safe fallback."""

    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float environment setting with a safe fallback."""

    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment setting from common .env spellings."""

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _clean_text(value: Any) -> str:
    """Normalize feed text into a compact single-line string."""

    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _canonical_url(value: str) -> str:
    """Return a stable URL form so repeated RSS results collapse together."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    query_parts = []
    for part in parsed.query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0].lower()
        if key.startswith("utm_") or key in {"from", "spm", "fbclid", "gclid"}:
            continue
        query_parts.append(part)
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "&".join(query_parts),
            "",
        )
    )


def _url_host(value: str) -> str:
    """Return the normalized host for a URL."""

    return urlparse(str(value or "")).netloc.lower()


def _is_google_news_url(value: str) -> bool:
    """Return true when a URL is a Google News discovery/intermediate URL."""

    host = _url_host(value)
    return host == "news.google.com" or host.endswith(".news.google.com")


def _google_news_article_id(value: str) -> str:
    """Extract the opaque Google News article id from /articles/ or /read/ URLs."""

    parsed = urlparse(str(value or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[-2] in {"articles", "read"}:
        return parts[-1]
    return ""


def _extract_google_decode_params(html: str) -> Tuple[str, str]:
    """Extract Google News decode signature and timestamp from an article page."""

    sig_match = re.search(r'data-n-a-sg="([^"]+)"', html or "")
    ts_match = re.search(r'data-n-a-ts="([^"]+)"', html or "")
    if not sig_match or not ts_match:
        return "", ""
    return sig_match.group(1), ts_match.group(1)


def _title_key(title: str) -> str:
    """Build a dedup-friendly title key from Chinese/English visible text."""

    text = re.sub(r"\s+", "", str(title or "").lower())
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def _strip_source_suffix(title: str, source_name: str = "") -> str:
    """Remove RSS display suffixes such as " - news.sjtu.edu.cn" from titles."""

    text = _clean_text(title)
    source = _clean_text(source_name)
    suffixes = []
    if source:
        suffixes.extend([source, source.replace("www.", "")])
    suffixes.extend(["sohu.com", "163.com", "news.sjtu.edu.cn"])
    for suffix in suffixes:
        suffix = re.escape(suffix)
        text = re.sub(rf"\s*[-_—–｜|]\s*{suffix}\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _parse_feed_datetime(value: Any) -> Optional[datetime]:
    """Parse RSS/Atom published dates into timezone-aware datetimes."""

    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, IndexError, OverflowError):
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ActiveResearchAgent:
    """Discover keyword-scoped online topics for the future active pipeline."""

    def __init__(
        self,
        *,
        keyword_config_path: Optional[Path] = None,
        lookback_hours: Optional[int] = None,
        min_topic_score: Optional[float] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.keyword_config_path = Path(keyword_config_path or os.environ.get("ACTIVE_RESEARCH_KEYWORDS_PATH") or DEFAULT_KEYWORD_CONFIG)
        self.lookback_hours = lookback_hours or _env_int("ACTIVE_RESEARCH_LOOKBACK_HOURS", 48)
        self.min_topic_score = min_topic_score if min_topic_score is not None else _env_float("ACTIVE_RESEARCH_MIN_TOPIC_SCORE", 75)
        self.http_client = http_client

    def load_keyword_config(self) -> Dict[str, Any]:
        """Load manually maintained keyword groups from YAML."""

        if not self.keyword_config_path.exists():
            raise FileNotFoundError(f"keyword config not found: {self.keyword_config_path}")
        data = yaml.safe_load(self.keyword_config_path.read_text(encoding="utf-8")) or {}
        groups = data.get("keyword_groups") or {}
        if not isinstance(groups, dict) or not groups:
            raise ValueError("keyword_groups must be a non-empty mapping")
        return data

    def iter_keywords(self, config: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
        """Return (group, keyword) pairs; groups do not affect scoring weight."""

        data = config or self.load_keyword_config()
        out: List[Tuple[str, str]] = []
        for group, values in (data.get("keyword_groups") or {}).items():
            if not isinstance(values, list):
                continue
            for value in values:
                keyword = str(value or "").strip()
                if keyword:
                    out.append((str(group), keyword))
        if not out:
            raise ValueError("no active research keywords configured")
        return out

    def google_news_rss_url(self, keyword: str) -> str:
        """Build the Google News RSS search URL for one configured keyword."""

        return (
            "https://news.google.com/rss/search?"
            f"q={quote(keyword)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        )

    def rss_urls(self, config: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str, str]]:
        """Return (group, keyword, url) tuples to fetch this discovery round."""

        data = config or self.load_keyword_config()
        urls = [(group, keyword, self.google_news_rss_url(keyword)) for group, keyword in self.iter_keywords(data)]
        for item in data.get("rss_sources") or []:
            if isinstance(item, str) and item.strip():
                urls.append(("rss_sources", "manual_rss", item.strip()))
            elif isinstance(item, dict) and item.get("url"):
                urls.append((str(item.get("group") or "rss_sources"), str(item.get("keyword") or "manual_rss"), str(item["url"]).strip()))
        return urls

    async def discover(self, *, limit: int = 20, include_below_threshold: bool = False) -> List[ActiveResearchCandidate]:
        """Fetch RSS sources, deduplicate entries, score them, and return ranked candidates."""

        config = self.load_keyword_config()
        urls = self.rss_urls(config)
        client = self.http_client or httpx.AsyncClient(timeout=30.0, headers={"User-Agent": DEFAULT_USER_AGENT})
        close_client = self.http_client is None
        try:
            batches = await asyncio.gather(
                *[self._fetch_rss(client, group=group, keyword=keyword, url=url) for group, keyword, url in urls],
                return_exceptions=True,
            )
        finally:
            if close_client:
                await client.aclose()

        candidates: List[ActiveResearchCandidate] = []
        for batch in batches:
            if isinstance(batch, Exception):
                LOG.warning("rss_fetch_failed error=%s", batch)
                continue
            candidates.extend(batch)

        deduped = self._dedupe_candidates(candidates)
        if not include_below_threshold:
            deduped = [item for item in deduped if item.topic_score >= self.min_topic_score]
        return sorted(deduped, key=lambda item: (item.topic_score, item.published_at or ""), reverse=True)[:limit]

    async def _fetch_rss(
        self,
        client: httpx.AsyncClient,
        *,
        group: str,
        keyword: str,
        url: str,
    ) -> List[ActiveResearchCandidate]:
        """Fetch one RSS feed and convert entries into scored candidates."""

        response = await client.get(url)
        response.raise_for_status()
        now = datetime.now(timezone.utc)
        fetched_at = now.isoformat()
        items: List[ActiveResearchCandidate] = []

        for entry in self._parse_rss_entries(response.text):
            raw_title = _clean_text(entry.get("title"))
            link = _canonical_url(entry.get("link") or "")
            if not raw_title or not link:
                continue
            published = _parse_feed_datetime(entry.get("published") or entry.get("updated"))
            if published and published < now - timedelta(hours=self.lookback_hours):
                continue
            summary = _clean_text(entry.get("summary"))
            source_name = self._source_name(entry, link)
            source_url = _canonical_url(entry.get("source_url") or "")
            title = _strip_source_suffix(raw_title, source_name)
            original_url, original_url_status, warnings = await self._resolve_original_url(
                client,
                discovery_url=link,
                source_url=source_url,
            )
            score, breakdown, reasons = self._score_candidate(
                title=title,
                summary=summary,
                keyword=keyword,
                published_at=published,
                source_name=source_name,
                now=now,
            )
            items.append(
                ActiveResearchCandidate(
                    title=title,
                    url=link,
                    source_name=source_name,
                    source_url=source_url,
                    source_type="google_news_rss" if "news.google.com" in url else "rss",
                    original_url=original_url,
                    original_url_status=original_url_status,
                    keyword=keyword,
                    keyword_group=group,
                    published_at=published.isoformat() if published else None,
                    summary=summary,
                    fetched_at=fetched_at,
                    dedup_key=self._dedup_key(title=title, url=link),
                    topic_score=score,
                    score_breakdown=breakdown,
                    reasons=reasons,
                    warnings=warnings,
                )
            )
        return items

    async def _resolve_original_url(
        self,
        client: httpx.AsyncClient,
        *,
        discovery_url: str,
        source_url: str,
    ) -> Tuple[str, str, List[str]]:
        """Classify whether the candidate already has a usable article URL.

        Google News RSS usually returns a Google-hosted article URL. The feed's
        source_url is often only the publisher home or section URL, so v1 keeps
        it for traceability but does not pretend it is the original article.
        """

        discovery = _canonical_url(discovery_url)
        source = _canonical_url(source_url)
        if discovery and not _is_google_news_url(discovery):
            return discovery, "direct", []
        warnings: List[str] = []
        if discovery and _env_bool("ACTIVE_RESEARCH_DECODE_GOOGLE_NEWS_URLS", True):
            decoded_url, decode_warning = await self._decode_google_news_url(client, discovery)
            if decoded_url:
                return decoded_url, "decoded_google_news", []
            if decode_warning:
                warnings.append(decode_warning)
        warnings.append("original_url_requires_secondary_search")
        if source:
            warnings.append("source_url_is_publisher_home_not_confirmed_article")
        return "", "needs_secondary_search", warnings

    async def _decode_google_news_url(self, client: httpx.AsyncClient, discovery_url: str) -> Tuple[str, str]:
        """Try to decode a Google News RSS URL to the publisher article URL."""

        article_id = _google_news_article_id(discovery_url)
        if not article_id:
            return "", "google_news_article_id_missing"

        signature = ""
        timestamp = ""
        for page_url in (
            f"https://news.google.com/articles/{article_id}",
            f"https://news.google.com/rss/articles/{article_id}",
        ):
            try:
                response = await client.get(page_url)
                response.raise_for_status()
            except Exception:
                continue
            signature, timestamp = _extract_google_decode_params(response.text)
            if signature and timestamp:
                break
        if not signature or not timestamp:
            return "", "google_news_decode_params_missing"

        payload = [
            "Fbv4je",
            (
                '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
                'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                f'"{article_id}",{timestamp},"{signature}"]'
            ),
        ]
        headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}
        try:
            response = await client.post(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                headers=headers,
                content=f"f.req={quote(json.dumps([[payload]]))}",
            )
            response.raise_for_status()
            decoded = self._parse_google_decode_response(response.text)
        except Exception as exc:
            return "", f"google_news_decode_failed:{type(exc).__name__}"
        if decoded and not _is_google_news_url(decoded):
            return _canonical_url(decoded), ""
        return "", "google_news_decoded_url_invalid"

    def _parse_google_decode_response(self, text: str) -> str:
        """Parse Google's nested batchexecute response and return decoded URL."""

        for line in str(text or "").splitlines():
            line = line.strip()
            if not line.startswith("[["):
                continue
            try:
                outer = json.loads(line)
                inner_raw = outer[0][2]
                inner = json.loads(inner_raw)
                decoded = str(inner[1] or "").strip()
            except (TypeError, ValueError, IndexError, KeyError, json.JSONDecodeError):
                continue
            if decoded:
                return decoded
        return ""

    def build_research_brief(self, candidate: ActiveResearchCandidate) -> Dict[str, Any]:
        """Build a structured package for the future ResearchAgent handoff."""

        research_ready = bool(candidate.original_url)
        warnings = list(candidate.warnings or [])
        stop_reason = ""
        if not research_ready:
            stop_reason = "original_url_unresolved"
            if "original_url_required_before_research" not in warnings:
                warnings.append("original_url_required_before_research")
        return {
            "topic": candidate.title,
            "title": candidate.title,
            "primary_keyword": candidate.keyword,
            "target_keywords": [candidate.keyword],
            "keyword_group": candidate.keyword_group,
            "topic_score": candidate.topic_score,
            "score_breakdown": dict(candidate.score_breakdown),
            "source_name": candidate.source_name,
            "source_url": candidate.source_url,
            "discovery_url": candidate.url,
            "original_url": candidate.original_url,
            "original_url_status": candidate.original_url_status,
            "published_at": candidate.published_at,
            "summary": candidate.summary,
            "research_ready": research_ready,
            "stop_reason": stop_reason,
            "warnings": warnings,
            "sources": [
                {
                    "name": candidate.source_name,
                    "url": candidate.original_url or candidate.source_url or candidate.url,
                    "discovery_url": candidate.url,
                    "type": candidate.source_type,
                    "published_at": candidate.published_at,
                    "verified_original_url": bool(candidate.original_url),
                }
            ],
        }

    def build_research_briefs(self, candidates: Iterable[ActiveResearchCandidate]) -> List[Dict[str, Any]]:
        """Build ResearchAgent handoff packages for a candidate list."""

        return [self.build_research_brief(candidate) for candidate in candidates]

    def _parse_rss_entries(self, raw_xml: str) -> List[Dict[str, str]]:
        """Parse RSS/Atom XML with the standard library to avoid extra deps."""

        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            return []

        entries: List[Dict[str, str]] = []
        rss_items = root.findall(".//item")
        atom_items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for item in rss_items:
            source_el = item.find("source")
            entries.append(
                {
                    "title": item.findtext("title") or "",
                    "link": item.findtext("link") or "",
                    "published": item.findtext("pubDate") or "",
                    "updated": "",
                    "summary": item.findtext("description") or "",
                    "source": item.findtext("source") or "",
                    "source_url": source_el.attrib.get("url", "") if source_el is not None else "",
                }
            )
        for item in atom_items:
            link = ""
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = link_el.attrib.get("href", "")
            entries.append(
                {
                    "title": item.findtext("{http://www.w3.org/2005/Atom}title") or "",
                    "link": link,
                    "published": item.findtext("{http://www.w3.org/2005/Atom}published") or "",
                    "updated": item.findtext("{http://www.w3.org/2005/Atom}updated") or "",
                    "summary": item.findtext("{http://www.w3.org/2005/Atom}summary") or "",
                    "source": "",
                    "source_url": "",
                }
            )
        return entries

    def _source_name(self, entry: Dict[str, str], link: str) -> str:
        """Read source name from RSS metadata, falling back to the URL host."""

        source = entry.get("source")
        if source:
            return _clean_text(source)
        parsed = urlparse(link)
        return parsed.netloc or "unknown"

    def _score_candidate(
        self,
        *,
        title: str,
        summary: str,
        keyword: str,
        published_at: Optional[datetime],
        source_name: str,
        now: datetime,
    ) -> Tuple[float, Dict[str, float], List[str]]:
        """Score candidate value before spending LLM/research tokens."""

        freshness = self._freshness_score(published_at, now)
        relevance = self._relevance_score(title=title, summary=summary, keyword=keyword)
        news_value = self._news_value_score(title=title, summary=summary)
        material = self._material_score(title=title, summary=summary)
        search_value = self._search_value_score(title=title, keyword=keyword)
        authority = self._authority_score(source_name)
        breakdown = {
            "freshness": round(freshness * 0.25, 2),
            "business_relevance": round(relevance * 0.25, 2),
            "news_value": round(news_value * 0.20, 2),
            "material_completeness": round(material * 0.15, 2),
            "search_value": round(search_value * 0.10, 2),
            "source_authority": round(authority * 0.05, 2),
        }
        score = round(sum(breakdown.values()), 2)
        reasons = self._score_reasons(
            freshness=freshness,
            relevance=relevance,
            news_value=news_value,
            material=material,
            search_value=search_value,
            authority=authority,
        )
        return score, breakdown, reasons

    def _freshness_score(self, published_at: Optional[datetime], now: datetime) -> float:
        """Convert age into a 0-100 freshness score."""

        if not published_at:
            return 50.0
        age_hours = max((now - published_at).total_seconds() / 3600, 0)
        if age_hours <= 6:
            return 100.0
        if age_hours <= 24:
            return 90.0
        if age_hours <= 48:
            return 75.0
        if age_hours <= 96:
            return 55.0
        return 30.0

    def _relevance_score(self, *, title: str, summary: str, keyword: str) -> float:
        """Score whether the candidate is inside the configured business scope."""

        haystack = f"{title} {summary}".lower()
        keyword_hit = str(keyword).lower() in haystack
        domain_terms = [
            "mba",
            "emba",
            "商学院",
            "管理",
            "考研",
            "专业学位",
            "教育",
            "招生",
            "企业",
            "职业",
            "院校",
        ]
        hits = sum(1 for term in domain_terms if term.lower() in haystack)
        score = 45 + hits * 12
        if keyword_hit:
            score += 25
        return min(score, 100.0)

    def _news_value_score(self, *, title: str, summary: str) -> float:
        """Estimate impact/novelty from common news-signal terms."""

        text = f"{title} {summary}"
        strong_terms = ["发布", "启动", "改革", "新增", "首次", "重磅", "榜单", "政策", "调整", "合作", "突破", "获批"]
        medium_terms = ["论坛", "活动", "招生", "课程", "项目", "就业", "报名", "申请"]
        score = 45 + sum(14 for term in strong_terms if term in text) + sum(7 for term in medium_terms if term in text)
        return min(score, 100.0)

    def _material_score(self, *, title: str, summary: str) -> float:
        """Estimate whether the feed item has enough material for research."""

        length = len(f"{title}{summary}")
        if length >= 220:
            return 95.0
        if length >= 120:
            return 80.0
        if length >= 60:
            return 65.0
        return 45.0

    def _search_value_score(self, *, title: str, keyword: str) -> float:
        """Estimate search demand from query-style and decision-style phrasing."""

        text = title.lower()
        terms = ["怎么", "如何", "条件", "报名", "申请", "学费", "排名", "区别", "选择", "就业", "政策"]
        score = 50 + sum(8 for term in terms if term in text)
        if str(keyword).lower() in text:
            score += 20
        return min(score, 100.0)

    def _authority_score(self, source_name: str) -> float:
        """Prefer official universities, government sites, and mainstream media."""

        source = str(source_name or "").lower()
        if any(term in source for term in ["edu", "大学", "学院", "教育部", "gov"]):
            return 95.0
        if any(term in source for term in ["新华", "人民网", "中国教育", "央视", "经济日报", "新浪", "腾讯", "搜狐"]):
            return 80.0
        if source and source != "unknown":
            return 65.0
        return 45.0

    def _score_reasons(self, **scores: float) -> List[str]:
        """Create short readable explanations for the score output."""

        reasons: List[str] = []
        if scores["freshness"] >= 90:
            reasons.append("fresh")
        if scores["relevance"] >= 80:
            reasons.append("keyword_scope_match")
        if scores["news_value"] >= 75:
            reasons.append("strong_news_signal")
        if scores["material"] >= 80:
            reasons.append("enough_feed_material")
        if scores["authority"] >= 80:
            reasons.append("trusted_source")
        if not reasons:
            reasons.append("low_signal_candidate")
        return reasons

    def _dedup_key(self, *, title: str, url: str) -> str:
        """Create a stable candidate identity from canonical URL and title."""

        key = _canonical_url(url) or _title_key(title)
        if not key:
            key = title
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    def _dedupe_candidates(self, candidates: Sequence[ActiveResearchCandidate]) -> List[ActiveResearchCandidate]:
        """Collapse duplicate URLs and nearly identical titles, keeping best score."""

        by_key: Dict[str, ActiveResearchCandidate] = {}
        by_title: Dict[str, str] = {}
        for candidate in candidates:
            title_key = _title_key(candidate.title)
            existing_key = by_title.get(title_key)
            key = existing_key or candidate.dedup_key
            existing = by_key.get(key)
            if existing is None or candidate.topic_score > existing.topic_score:
                by_key[key] = candidate
                if title_key:
                    by_title[title_key] = key
        return list(by_key.values())

    @staticmethod
    def to_jsonable(candidates: Iterable[ActiveResearchCandidate]) -> List[Dict[str, Any]]:
        """Convert dataclass candidates to plain dicts for JSON output."""

        return [asdict(item) for item in candidates]


async def discover_active_research_candidates(*, limit: int = 20, include_below_threshold: bool = False) -> List[Dict[str, Any]]:
    """Convenience function used by scripts and future LangGraph nodes."""

    agent = ActiveResearchAgent()
    candidates = await agent.discover(limit=limit, include_below_threshold=include_below_threshold)
    return ActiveResearchAgent.to_jsonable(candidates)


def dumps_candidates(candidates: Iterable[ActiveResearchCandidate]) -> str:
    """Serialize candidates in a readable UTF-8 JSON form."""

    return json.dumps(ActiveResearchAgent.to_jsonable(candidates), ensure_ascii=False, indent=2)
