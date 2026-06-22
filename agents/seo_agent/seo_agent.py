import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from agents.seo_agent.tools.keyword_analyzer import KeywordAnalyzer
from agents.seo_agent.tools.meta_generator import MetaGenerator
from agents.seo_agent.tools.schema_generator import SchemaGenerator


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


def _word_count(text: str) -> int:
    s = text or ""
    chinese = len(re.findall(r"[\u4e00-\u9fff]", s))
    english = len(re.findall(r"\b[a-zA-Z]+\b", s))
    return chinese + english


def _guess_language(text: str) -> str:
    s = text or ""
    if re.search(r"[\u4e00-\u9fff]", s):
        return "chinese"
    return "english"


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x or "").strip()]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(value)]


class SEOAgent:
    def __init__(
        self,
        config_path: str = "agents/seo_agent/config.yaml",
        brand_path: str = "config/brand_guidelines.yaml",
    ):
        self.config_path = config_path
        self.brand_path = brand_path
        self.config = self._load_config()

    def _resolve_path(self, path: str, *, fallback: Path) -> str:
        p = (path or "").strip()
        if p and os.path.exists(p):
            return p
        return str(fallback)

    def _config_file(self) -> str:
        return self._resolve_path(self.config_path, fallback=Path(__file__).resolve().parent / "config.yaml")

    def _brand_file(self) -> str:
        project_root = Path(__file__).resolve().parents[2]
        return self._resolve_path(self.brand_path, fallback=project_root / "config" / "brand_guidelines.yaml")

    def _load_config(self) -> Dict[str, Any]:
        cfg_path = self._config_file()
        if not os.path.exists(cfg_path):
            return {}
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _schema_type(self, topic: Dict[str, Any]) -> str:
        t = str((topic or {}).get("content_type") or (topic or {}).get("category") or "").strip().lower()
        if t in {"blog", "news", "faq", "howto", "breadcrumb", "article"}:
            return t
        return "article"

    def _normalize_suggestions(self, suggestions: Any) -> List[str]:
        if suggestions is None:
            return []
        if isinstance(suggestions, list):
            return [str(x) for x in suggestions if str(x or "").strip()]
        if isinstance(suggestions, str):
            return [suggestions.strip()] if suggestions.strip() else []
        return [str(suggestions)]

    def _suggest_internal_links(
        self, *, primary_keyword: str, keyword_analysis: Dict[str, Any], max_count: int = 3
    ) -> List[Dict[str, str]]:
        anchors: List[str] = []
        if primary_keyword:
            anchors.append(primary_keyword)
        extracted = keyword_analysis.get("extracted_keywords") if isinstance(keyword_analysis, dict) else None
        if isinstance(extracted, list):
            anchors.extend([str(x) for x in extracted[: max(0, max_count - len(anchors))]])

        out: List[Dict[str, str]] = []
        for a in anchors[:max_count]:
            s = str(a or "").strip()
            if not s:
                continue
            out.append({"suggested_anchor": s, "target_url": "", "suggested_position": "正文相关段落"})
        return out

    def _seo_report(
        self,
        *,
        keyword_analysis: Dict[str, Any],
        meta: Dict[str, Any],
        schema_validation: Dict[str, Any],
    ) -> Dict[str, Any]:
        score = 0.0
        parts = 0

        ka = keyword_analysis.get("assessment") if isinstance(keyword_analysis, dict) else None
        if isinstance(ka, dict) and isinstance(ka.get("score"), (int, float)):
            score += float(ka.get("score"))
            parts += 1

        warnings = meta.get("warnings") if isinstance(meta, dict) else None
        if warnings is not None:
            score += max(0.0, 100.0 - 10.0 * len(warnings if isinstance(warnings, list) else [warnings]))
            parts += 1

        valid_schema = bool((schema_validation or {}).get("valid")) if isinstance(schema_validation, dict) else False
        score += 100.0 if valid_schema else 60.0
        parts += 1

        overall = int(round(score / max(parts, 1)))
        return {
            "overall_score": overall,
            "keyword_optimization": ka,
            "meta_optimization": {"warnings": warnings, "title_length": meta.get("title_length"), "description_length": meta.get("description_length")},
            "technical_seo": {"schema_valid": valid_schema, "schema_errors": (schema_validation or {}).get("errors")},
        }

    async def execute(
        self,
        *,
        article: Dict[str, Any],
        topic: Optional[Dict[str, Any]] = None,
        page_info: Optional[Dict[str, Any]] = None,
        dry_run: bool = True,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        article = article if isinstance(article, dict) else {}
        topic = topic if isinstance(topic, dict) else {}
        page_info = page_info if isinstance(page_info, dict) else {}

        title = str(article.get("title") or topic.get("title") or "")
        content = str(article.get("content_md") or article.get("content") or article.get("content_html") or "")
        warnings: List[str] = []
        if not title.strip():
            warnings.append("missing_title")
        if not content.strip():
            warnings.append("missing_content")
        if dry_run:
            warnings.append("dry_run")

        primary_keyword = str(topic.get("primary_keyword") or "")
        if not primary_keyword:
            kws = topic.get("target_keywords")
            if isinstance(kws, list) and kws:
                primary_keyword = str(kws[0] or "")

        secondary_keywords = topic.get("secondary_keywords")
        if secondary_keywords is None:
            secondary_keywords = topic.get("target_keywords")
        secondary_keywords_list = _as_list(secondary_keywords)

        lang = str(language or topic.get("language") or "").strip().lower()
        if lang not in {"chinese", "english"}:
            lang = _guess_language(title + "\n" + content)

        analyzer = KeywordAnalyzer(config_path=self._config_file())
        keyword_analysis = analyzer.analyze(
            content=content,
            primary_keyword=primary_keyword,
            secondary_keywords=secondary_keywords_list,
            language=lang,
        )

        meta_gen = MetaGenerator(config_path=self._config_file(), brand_path=self._brand_file())
        meta = meta_gen.generate(
            title=title,
            content=content,
            primary_keyword=primary_keyword,
            secondary_keywords=secondary_keywords_list,
            language=lang,
        )
        warnings.extend(self._normalize_suggestions(meta.get("warnings") if isinstance(meta, dict) else None))

        schema_gen = SchemaGenerator(config_path=self._config_file(), brand_path=self._brand_file())
        schema_type = self._schema_type(topic)
        keywords: List[str] = []
        if primary_keyword:
            keywords.append(primary_keyword)
        keywords.extend([k for k in secondary_keywords_list if k and k != primary_keyword])

        published = article.get("published_date") or article.get("published_at") or ""
        modified = article.get("modified_date") or article.get("updated_at") or published or ""
        url = page_info.get("url") or article.get("url") or ""
        slug = article.get("slug") or page_info.get("slug") or ""
        category = topic.get("category") or topic.get("content_type") or page_info.get("category") or ""
        if slug and url and isinstance(url, str) and url.endswith("/"):
            url = url.rstrip("/") + "/" + str(slug).lstrip("/")

        schema_article = {
            "title": title,
            "meta_description": meta.get("meta_description") or article.get("meta_description") or "",
            "featured_image_url": article.get("featured_image_url") or article.get("featured_image") or "",
            "logo_url": page_info.get("logo_url") or "",
            "url": url,
            "published_date": published,
            "modified_date": modified,
            "category": category,
            "keywords": keywords,
            "word_count": _word_count(content),
            "publisher": page_info.get("publisher") or "",
            "author": page_info.get("author") or "",
        }
        schema_json = schema_gen.generate(schema_article, schema_type=schema_type)
        schema_validation = schema_gen.validate_schema(schema_json if isinstance(schema_json, dict) else {})
        if isinstance(schema_validation, dict) and not schema_validation.get("valid"):
            warnings.extend(self._normalize_suggestions(schema_validation.get("errors")))

        internal_links_cfg = (self.config or {}).get("internal_links") if isinstance(self.config, dict) else {}
        max_links = int(((internal_links_cfg or {}).get("count") or {}).get("max") or 3) if isinstance(internal_links_cfg, dict) else 3
        internal_links = self._suggest_internal_links(
            primary_keyword=primary_keyword, keyword_analysis=keyword_analysis, max_count=max(1, max_links)
        )

        assessment = keyword_analysis.get("assessment") if isinstance(keyword_analysis, dict) else {}
        improvement_suggestions: List[str] = []
        if isinstance(assessment, dict):
            improvement_suggestions.extend(self._normalize_suggestions(assessment.get("suggestions")))
        improvement_suggestions.extend(self._normalize_suggestions(meta.get("warnings") if isinstance(meta, dict) else None))
        if isinstance(schema_validation, dict) and not schema_validation.get("valid"):
            improvement_suggestions.extend(self._normalize_suggestions(schema_validation.get("errors")))

        seo_report = self._seo_report(keyword_analysis=keyword_analysis, meta=meta, schema_validation=schema_validation)
        return {
            "optimized_article": {"title": title, "content": content},
            "meta_title": str(meta.get("meta_title") or ""),
            "meta_description": str(meta.get("meta_description") or ""),
            "og_tags": meta.get("og_tags") if isinstance(meta.get("og_tags"), dict) else {},
            "twitter_tags": meta.get("twitter_tags") if isinstance(meta.get("twitter_tags"), dict) else {},
            "schema_json": schema_json if isinstance(schema_json, dict) else {},
            "internal_links": internal_links,
            "seo_report": seo_report,
            "improvement_suggestions": improvement_suggestions,
            "warnings": [str(x) for x in warnings if str(x or "").strip()],
            "tool_results": {
                "keyword_analysis": keyword_analysis,
                "meta": meta,
                "schema_validation": schema_validation,
            },
            "generated_at": datetime.now().isoformat(),
        }
