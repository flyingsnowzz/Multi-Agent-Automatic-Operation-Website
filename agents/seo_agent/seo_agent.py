import os
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import yaml

from agents.seo_agent.tools.schema_generator import SchemaGenerator as _SchemaGenerator


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


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x or "").strip()]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(value)]


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Running event loop detected. Use `await seo_agent.execute(...)` instead."
    )


@dataclass
class KeywordResult:
    primary_keyword: str
    secondary_keywords: List[str]
    long_tail_keywords: List[str]
    lsi_words: List[str]
    density: float
    occurrences: int
    total_words: int
    distribution: Dict[str, Any]
    assessment: Dict[str, Any]
    analyzer: str


@dataclass
class MetaResult:
    meta_title: str
    meta_description: str
    title_length: int
    description_length: int
    reasoning: Dict[str, Any]
    og_tags: Dict[str, str]
    twitter_tags: Dict[str, str]
    model_used: str


class SEOAgent:
    """SEO Agent — V1 (传统 Python) / V2 (LLM) 双模式。

    V1: jieba + TF-IDF 关键词分析，零 token
    V2: LLM 阅读文章识别关键词，有 token 成本

    两者都使用 LLM 生成 Meta Title / Description。
    """

    def __init__(
        self,
        config_path: str = "agents/seo_agent/config.yaml",
        brand_path: str = "config/brand_guidelines.yaml",
        mode: str = "v1",
    ):
        self.config_path = config_path
        self.brand_path = brand_path
        self.mode = mode.strip().lower()
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

    def _brand_name(self) -> str:
        bp = self._brand_file()
        if os.path.exists(bp):
            with open(bp, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
                raw = _deep_env_resolve(raw)
                if isinstance(raw, dict):
                    return str(raw.get("brand_name") or "").strip()
        return "TechAI Insight"

    def _llm_config(self) -> Dict[str, Any]:
        llm_cfg = (self.config or {}).get("llm") if isinstance(self.config, dict) else {}
        return {
            "model": llm_cfg.get("model") or "gpt-4o-mini",
            "base_url": llm_cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL"),
            "api_key": os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "",
        }

    def _resolve_mode(self, requested: Optional[str] = None) -> str:
        m = (requested or self.mode or "v1").strip().lower()
        return m if m in ("v1", "v2") else "v1"

    def _derive_primary_keyword(self, *, title: str, topic: Dict[str, Any]) -> str:
        pk = str((topic or {}).get("primary_keyword") or "").strip()
        if pk:
            return pk
        tks = (topic or {}).get("target_keywords")
        if isinstance(tks, list) and tks:
            return str(tks[0] or "").strip()
        if isinstance(tks, str) and tks.strip():
            return tks.strip()
        return title.strip()

    # ── V1 关键词分析 ──
    def _analyze_keywords_v1(self, content: str, primary_keyword: str) -> KeywordResult:
        from agents.seo_agent.tools.keyword_analyzer_v1 import KeywordAnalyzerV1
        a = KeywordAnalyzerV1(config_path=self._config_file())
        r = a.analyze(content=content, target_keyword=primary_keyword)
        return KeywordResult(
            primary_keyword=r["primary_keyword"],
            secondary_keywords=r.get("candidates", [])[1:6] if "candidates" in r else [],
            long_tail_keywords=[],
            lsi_words=r.get("lsi_words", [])[:10],
            density=r.get("density", 0.0),
            occurrences=r.get("occurrences", 0),
            total_words=r.get("total_words", 0),
            distribution=r.get("distribution", {}),
            assessment=r.get("assessment", {}),
            analyzer=r.get("analyzer", "v1_traditional"),
        )

    # ── V2 关键词分析 ──
    async def _analyze_keywords_v2_async(self, content: str, primary_keyword: str) -> KeywordResult:
        from agents.seo_agent.tools.keyword_analyzer_v2 import KeywordAnalyzerV2
        llm = self._llm_config()
        a = KeywordAnalyzerV2(model=llm["model"], base_url=llm["base_url"], api_key=llm["api_key"])
        r = await a.analyze(content=content, target_keyword=primary_keyword)
        return KeywordResult(
            primary_keyword=r.get("primary_keyword", primary_keyword),
            secondary_keywords=r.get("secondary_keywords", []),
            long_tail_keywords=r.get("long_tail_keywords", []),
            lsi_words=r.get("lsi_words", []),
            density=float(r.get("keyword_density") or 0),
            occurrences=0,
            total_words=_word_count(content),
            distribution=r.get("distribution", {}),
            assessment=r.get("assessment", {}),
            analyzer=r.get("analyzer", "v2_llm"),
        )

    # ── LLM Meta 生成 ──
    async def _generate_meta_async(self, title: str, content: str, primary_keyword: str) -> MetaResult:
        from agents.seo_agent.tools.meta_generator_llm import MetaGeneratorLLM
        llm = self._llm_config()
        brand = self._brand_name()
        gen = MetaGeneratorLLM(
            brand_name=brand, model=llm["model"],
            base_url=llm["base_url"], api_key=llm["api_key"],
            config_path=self._config_file(),
        )
        r = await gen.generate(title=title, content=content, primary_keyword=primary_keyword)
        mt = r.get("meta_title", title)
        md = r.get("meta_description", "")
        return MetaResult(
            meta_title=mt, meta_description=md,
            title_length=int(r.get("title_length", 0)),
            description_length=int(r.get("description_length", 0)),
            reasoning=r.get("reasoning", {}),
            og_tags={"og:title": mt, "og:description": md, "og:type": "article", "og:site_name": brand},
            twitter_tags={"twitter:card": "summary_large_image", "twitter:title": mt, "twitter:description": md},
            model_used=r.get("model_used", llm["model"]),
        )

    def _schema_type(self, topic: Dict[str, Any]) -> str:
        t = str((topic or {}).get("content_type") or (topic or {}).get("category") or "").strip().lower()
        return t if t in {"blog", "news", "faq", "howto", "breadcrumb", "article"} else "article"

    def _normalize_suggestions(self, suggestions: Any) -> List[str]:
        if suggestions is None:
            return []
        if isinstance(suggestions, list):
            return [str(x) for x in suggestions if str(x or "").strip()]
        if isinstance(suggestions, str):
            return [suggestions.strip()] if suggestions.strip() else []
        return [str(suggestions)]

    def _suggest_internal_links(self, *, primary_keyword: str, keyword_analysis: Dict[str, Any], max_count: int = 3) -> List[Dict[str, str]]:
        anchors: List[str] = [primary_keyword] if primary_keyword else []
        extracted = keyword_analysis.get("extracted_keywords") if isinstance(keyword_analysis, dict) else None
        if isinstance(extracted, list):
            anchors.extend([str(x) for x in extracted[:max(0, max_count - len(anchors))]])
        out: List[Dict[str, str]] = []
        for a in anchors[:max_count]:
            s = str(a or "").strip()
            if s:
                out.append({"suggested_anchor": s, "target_url": "", "suggested_position": "正文相关段落"})
        return out

    def _build_report(self, *, kw: KeywordResult, meta: MetaResult, schema_valid: bool, schema_errors: List[str]) -> Dict[str, Any]:
        ka = kw.assessment
        kw_score = float(ka.get("score") or 0)
        meta_score = max(0.0, 100.0 - 10.0 * max(0, meta.title_length - 60)) if meta.title_length > 60 else 100.0
        schema_score = 100.0 if schema_valid else 60.0
        overall = int(round((kw_score + meta_score + schema_score) / 3))
        return {
            "overall_score": overall,
            "keyword_optimization": {
                "score": kw_score, "primary_keyword": kw.primary_keyword,
                "density": f"{kw.density:.1f}%", "analyzer": kw.analyzer,
                "issues": ka.get("issues", []), "passed_checks": ka.get("passed_checks", []),
            },
            "meta_optimization": {
                "title": meta.meta_title, "description": meta.meta_description,
                "title_length": meta.title_length, "description_length": meta.description_length,
            },
            "technical_seo": {"schema_valid": schema_valid, "schema_errors": schema_errors},
        }

    # ── 主入口 ──
    async def execute(
        self, *, article: Dict[str, Any], topic: Optional[Dict[str, Any]] = None,
        page_info: Optional[Dict[str, Any]] = None, dry_run: bool = True,
        language: Optional[str] = None, keyword_mode: Optional[str] = None,
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

        primary_keyword = self._derive_primary_keyword(title=title, topic=topic)
        mode = self._resolve_mode(keyword_mode)

        # 关键词分析
        if mode == "v2":
            kw = await self._analyze_keywords_v2_async(content, primary_keyword)
        else:
            kw = self._analyze_keywords_v1(content, primary_keyword)

        # LLM Meta 生成
        meta = await self._generate_meta_async(title, content, primary_keyword)

        # Schema
        schema_gen = _SchemaGenerator(config_path=self._config_file(), brand_path=self._brand_file())
        schema_type = self._schema_type(topic)
        kws_for_schema = [kw.primary_keyword] + [k for k in kw.secondary_keywords[:5] if k]
        published = article.get("published_date") or article.get("published_at") or ""
        modified = article.get("modified_date") or article.get("updated_at") or published or ""
        url = page_info.get("url") or article.get("url") or ""
        slug = article.get("slug") or page_info.get("slug") or ""
        category = topic.get("category") or topic.get("content_type") or page_info.get("category") or ""
        if slug and url and isinstance(url, str) and url.endswith("/"):
            url = url.rstrip("/") + "/" + str(slug).lstrip("/")

        schema_article = {
            "title": title, "meta_description": meta.meta_description,
            "featured_image_url": article.get("featured_image_url") or article.get("featured_image") or "",
            "logo_url": page_info.get("logo_url") or "", "url": url,
            "published_date": published, "modified_date": modified,
            "category": category, "keywords": kws_for_schema,
            "word_count": _word_count(content),
            "publisher": page_info.get("publisher") or "",
            "author": page_info.get("author") or "",
        }
        schema_json = schema_gen.generate(schema_article, schema_type=schema_type)
        schema_val = schema_gen.validate_schema(schema_json if isinstance(schema_json, dict) else {})
        if isinstance(schema_val, dict) and not schema_val.get("valid"):
            warnings.extend(self._normalize_suggestions(schema_val.get("errors")))

        # 内链
        il_cfg = (self.config or {}).get("internal_links") if isinstance(self.config, dict) else {}
        max_links = int(((il_cfg or {}).get("count") or {}).get("max") or 3) if isinstance(il_cfg, dict) else 3
        internal_links = self._suggest_internal_links(
            primary_keyword=kw.primary_keyword,
            keyword_analysis={"extracted_keywords": kw.secondary_keywords},
            max_count=max(1, max_links),
        )

        # 建议汇总
        suggestions: List[str] = []
        suggestions.extend(self._normalize_suggestions(kw.assessment.get("suggestions")))
        if isinstance(schema_val, dict) and not schema_val.get("valid"):
            suggestions.extend(self._normalize_suggestions(schema_val.get("errors")))

        report = self._build_report(
            kw=kw, meta=meta,
            schema_valid=bool((schema_val or {}).get("valid")),
            schema_errors=(schema_val or {}).get("errors") or [],
        )

        return {
            "mode": mode,
            "keyword_result": {
                "primary_keyword": kw.primary_keyword,
                "secondary_keywords": kw.secondary_keywords,
                "long_tail_keywords": kw.long_tail_keywords,
                "lsi_words": kw.lsi_words,
                "density": kw.density,
                "occurrences": kw.occurrences,
                "total_words": kw.total_words,
                "distribution": kw.distribution,
                "assessment": kw.assessment,
                "analyzer": kw.analyzer,
            },
            "meta_title": meta.meta_title,
            "meta_description": meta.meta_description,
            "og_tags": meta.og_tags,
            "twitter_tags": meta.twitter_tags,
            "schema_json": schema_json,
            "internal_links": internal_links,
            "seo_report": report,
            "improvement_suggestions": suggestions,
            "warnings": [str(x) for x in warnings if str(x or "").strip()],
            "generated_at": datetime.now().isoformat(),
        }

    # ── 从数据库执行 ──
    async def execute_from_db(
        self, *, article_id: Optional[int] = None, candidate_id: Optional[int] = None,
        limit: int = 10, min_score: float = 70.0, keyword_mode: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        from agents.seo_agent.tools.db_reader import ArticleDBReader

        reader = ArticleDBReader()
        if article_id is not None:
            rec = reader.fetch_by_id(article_id)
            records = [rec] if rec else []
        else:
            records = reader.fetch_generated(limit=limit, min_score=min_score, candidate_id=candidate_id)

        results: List[Dict[str, Any]] = []
        for rec in records:
            result = await self.execute(
                article={
                    "title": rec.generated_title or rec.source_title or "",
                    "content_md": rec.generated_content_md or "",
                    "content": rec.generated_content_md or "",
                },
                topic={"title": rec.generated_title or rec.source_title or "", "primary_keyword": ""},
                page_info={},
                dry_run=True,
                keyword_mode=keyword_mode,
            )
            result["db_article_id"] = rec.id
            result["db_candidate_id"] = rec.candidate_id
            results.append(result)
        return results

    def execute_sync(self, **kwargs) -> Dict[str, Any]:
        return _run_async(self.execute(**kwargs))
