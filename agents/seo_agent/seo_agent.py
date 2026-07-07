"""SEOAgent main entry.

Beginner mental model:
    SEOAgent does not rewrite article content and does not publish to CMS. It
    takes an already selected/edited article and adds search metadata:
    keywords, meta title, meta description, social tags, and schema JSON.

Used by:
    worker_publish.py, after an article has passed original quality or rewrite
    quality.
"""

import os, re, asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import yaml
from agents.seo_agent.tools.schema_generator import SchemaGenerator as _SchemaGenerator

def _deep_env_resolve(value: Any) -> Any:
    # Expand ${ENV} and ${ENV:-default} in YAML config files.
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            expr = value[2:-1]
            if ":-" in expr:
                key, default = expr.split(":-", 1)
                return os.environ.get(key, default)
            return os.environ.get(expr, "")
        return value
    if isinstance(value, dict): return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list): return [_deep_env_resolve(v) for v in value]
    return value

def _word_count(text: str) -> int:
    # Lightweight mixed Chinese/English count for SEO reports and schema fields.
    s = text or ""
    return len(re.findall(r"[\u4e00-\u9fff]", s)) + len(re.findall(r"\b[a-zA-Z]+\b", s))

@dataclass
class KeywordResult:
    keywords: List[str]; density: float; occurrences: int; total_words: int
    distribution: Dict[str, Any]; assessment: Dict[str, Any]; analyzer: str

@dataclass
class MetaResult:
    meta_title: str; meta_description: str; title_length: int; description_length: int
    reasoning: Dict[str, Any]; og_tags: Dict[str, str]; twitter_tags: Dict[str, str]; model_used: str

class SEOAgent:
    """Small orchestrator around SEO tools.

    It coordinates keyword analysis, meta generation, and schema generation,
    then returns one normalized dictionary for the publish worker.
    """
    def __init__(self, config_path="agents/seo_agent/config.yaml", brand_path="config/brand_guidelines.yaml", mode="v2"):
        self.config_path, self.brand_path, self.mode = config_path, brand_path, mode.strip().lower()
        self.config = self._load_config()

    def _config_file(self):
        p = self.config_path.strip()
        return p if os.path.exists(p) else str(Path(__file__).resolve().parent / "config.yaml")
    def _brand_file(self):
        p = self.brand_path.strip()
        return p if os.path.exists(p) else str(Path(__file__).resolve().parents[2] / "config" / "brand_guidelines.yaml")
    def _load_config(self):
        p = self._config_file()
        if not os.path.exists(p): return {}
        with open(p,"r",encoding="utf-8") as f: return _deep_env_resolve(yaml.safe_load(f) or {})
    def _brand_name(self):
        p = self._brand_file()
        if os.path.exists(p):
            with open(p,"r",encoding="utf-8") as f:
                r = _deep_env_resolve(yaml.safe_load(f) or {})
                if isinstance(r, dict): return str(r.get("brand_name") or "").strip()
        return "TechAI Insight"
    def _llm_config(self):
        # SEO can use a different model/provider from writer/research. Configure
        # it through SEO_AGENT_* env vars.
        c = (self.config or {}).get("llm") if isinstance(self.config, dict) else {}
        return {
            "model": os.environ.get("SEO_AGENT_MODEL") or c.get("model") or "deepseek-chat",
            "base_url": os.environ.get("SEO_AGENT_BASE_URL") or c.get("base_url") or os.environ.get("OPENAI_BASE_URL"),
            "api_key": os.environ.get("SEO_AGENT_API_KEY") or c.get("api_key") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "",
        }
    def _resolve_mode(self, requested=None):
        m = (requested or self.mode or "v2").strip().lower()
        return m if m in ("v1","v2") else "v2"
    def _derive_primary_keyword(self, *, title, topic):
        # Prefer explicit primary_keyword from upstream. If missing, fall back to
        # target_keywords or title so SEO can still produce metadata.
        pk = str((topic or {}).get("primary_keyword") or "").strip()
        if pk: return pk
        tks = (topic or {}).get("target_keywords")
        if isinstance(tks, list) and tks: return str(tks[0] or "").strip()
        return title.strip()

    def _analyze_keywords_v1(self, content, primary_keyword):
        # v1 is deterministic/rule-based. It is cheaper and useful for tests.
        from agents.seo_agent.tools.keyword_analyzer_v1 import KeywordAnalyzerV1
        a = KeywordAnalyzerV1(config_path=self._config_file())
        r = a.analyze(content=content, target_keyword=primary_keyword)
        return KeywordResult(keywords=r.get("keywords",[]) or [r["primary_keyword"]]+r.get("candidates",[])[:7],
            density=r.get("density",0.0), occurrences=r.get("occurrences",0),
            total_words=r.get("total_words",0), distribution=r.get("distribution",{}), assessment=r.get("assessment",{}),
            analyzer=r.get("analyzer","v1_traditional"))

    async def _analyze_keywords_v2_async(self, content, primary_keyword):
        # v2 uses an LLM for semantic keyword extraction.
        from agents.seo_agent.tools.keyword_analyzer_v2 import KeywordAnalyzerV2
        llm = self._llm_config()
        a = KeywordAnalyzerV2(model=llm["model"], base_url=llm["base_url"], api_key=llm["api_key"])
        r = await a.analyze(content=content, target_keyword=primary_keyword)
        return KeywordResult(keywords=r.get("keywords",[]) or [r.get("primary_keyword",primary_keyword)]+r.get("secondary_keywords",[]),
            density=float(r.get("keyword_density") or 0),
            occurrences=0, total_words=_word_count(content), distribution=r.get("distribution",{}), assessment=r.get("assessment",{}),
            analyzer=r.get("analyzer","v2_llm"))

    async def _generate_meta_async(self, title, content, primary_keyword):
        # Generate search-result title/description. This does not edit the
        # article body itself.
        from agents.seo_agent.tools.meta_generator_llm import MetaGeneratorLLM
        llm = self._llm_config(); brand = self._brand_name()
        g = MetaGeneratorLLM(brand_name=brand, model=llm["model"], base_url=llm["base_url"], api_key=llm["api_key"], config_path=self._config_file())
        r = await g.generate(title=title, content=content, primary_keyword=primary_keyword)
        mt, md = r.get("meta_title",title), r.get("meta_description","")
        return MetaResult(meta_title=mt, meta_description=md, title_length=int(r.get("title_length",0)),
            description_length=int(r.get("description_length",0)), reasoning=r.get("reasoning",{}),
            og_tags={"og:title":mt,"og:description":md,"og:type":"article","og:site_name":brand},
            twitter_tags={"twitter:card":"summary_large_image","twitter:title":mt,"twitter:description":md},
            model_used=r.get("model_used",llm["model"]))

    def _schema_type(self, topic):
        t = str((topic or {}).get("content_type") or (topic or {}).get("category") or "").strip().lower()
        return t if t in {"blog","news","faq","howto","breadcrumb","article"} else "article"

    def _normalize_suggestions(self, s):
        if s is None: return []
        if isinstance(s, list): return [str(x) for x in s if str(x or "").strip()]
        if isinstance(s, str): return [s.strip()] if s.strip() else []
        return [str(s)]

    async def execute(self, *, article, topic=None, page_info=None, dry_run=True, language=None, keyword_mode=None):
        """Generate SEO metadata for one article.

        Important:
            This method prepares metadata only. It does not update MySQL and it
            does not publish; worker_publish.py handles audit writes and routing.
        """
        article = article if isinstance(article, dict) else {}
        topic = topic if isinstance(topic, dict) else {}
        page_info = page_info if isinstance(page_info, dict) else {}
        title = str(article.get("title") or topic.get("title") or "")
        content = str(article.get("content_md") or article.get("content") or article.get("content_html") or "")
        primary_keyword = self._derive_primary_keyword(title=title, topic=topic)
        mode = self._resolve_mode(keyword_mode)
        # Keyword analysis can use either:
        # - v1: rule-based, cheaper
        # - v2: LLM-based, better semantic keywords
        if mode == "v2": kw = await self._analyze_keywords_v2_async(content, primary_keyword)
        else: kw = self._analyze_keywords_v1(content, primary_keyword)
        # Meta generation is separated from keyword analysis because it optimizes
        # for search-result display and click-through wording.
        meta = await self._generate_meta_async(title, content, primary_keyword)
        sg = _SchemaGenerator(config_path=self._config_file(), brand_path=self._brand_file())
        st = self._schema_type(topic)
        # SchemaGenerator expects a standardized article object; map the worker
        # payload into that shape here.
        kfs = [((kw.keywords or [""])[0] if kw.keywords else "")] + [k for k in kw.keywords[:8] if k]
        sa = {"title":title,"meta_description":meta.meta_description,"url":page_info.get("url") or article.get("url") or "",
              "published_date":article.get("published_date") or "", "modified_date":article.get("modified_date") or "",
              "category":topic.get("category") or "", "keywords":kfs, "word_count":_word_count(content),
              "publisher":page_info.get("publisher") or "", "author":page_info.get("author") or ""}
        schema_json = sg.generate(sa, schema_type=st)
        # keyword_result can contain detailed analyzer metadata. worker_publish
        # stores only the keyword list/meta fields in MySQL and logs the rest.
        keyword_result = {
            "keywords": kw.keywords,
            "density": kw.density,
            "occurrences": kw.occurrences,
            "total_words": kw.total_words,
            "distribution": kw.distribution,
            "assessment": kw.assessment,
            "analyzer": kw.analyzer,
        }
        warnings = []
        if not content.strip():
            warnings.append("content_empty")
        if not primary_keyword.strip():
            warnings.append("primary_keyword_empty")
        return {
            "optimized_article": {
                **article,
                "title": title,
                "content_md": content,
                "slug": article.get("slug") or page_info.get("slug") or "",
                "meta": {"meta_title": meta.meta_title, "meta_description": meta.meta_description},
            },
            "keyword_result": keyword_result,
            "meta_title": meta.meta_title,
            "meta_description": meta.meta_description,
            "og_tags": meta.og_tags,
            "twitter_tags": meta.twitter_tags,
            "schema_json": schema_json,
            "internal_links": [],
            "seo_report": {
                "primary_keyword": primary_keyword,
                "keyword_mode": mode,
                "word_count": _word_count(content),
                "keyword_density": kw.density,
                "model_used": meta.model_used,
            },
            "improvement_suggestions": self._normalize_suggestions(kw.assessment.get("suggestions") if isinstance(kw.assessment, dict) else []),
            "warnings": warnings,
            "generated_at": datetime.now().isoformat(),
        }

    def execute_sync(self, **kwargs):
        try: asyncio.get_running_loop(); raise RuntimeError("Use await seo_agent.execute(...) instead")
        except RuntimeError as e:
            if "Use await" in str(e): raise
            return asyncio.run(self.execute(**kwargs))
