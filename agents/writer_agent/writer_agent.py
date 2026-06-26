import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from agents.writer_agent.tools.readability_checker import ReadabilityChecker
from agents.crawler_processor_agent.tools.url_content_fetcher import URLContentFetcher


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


def _extract_json(text: str) -> Dict[str, Any]:
    raw = text if isinstance(text, str) else str(text or "")
    s = raw.strip()
    if "```" in s:
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]
    return json.loads(s)


def _word_count(text: str) -> int:
    s = text or ""
    chinese = len(re.findall(r"[\u4e00-\u9fff]", s))
    english = len(re.findall(r"\b[a-zA-Z]+\b", s))
    return chinese + english


def _count_keyword_occurrences(content: str, keyword: str) -> int:
    if not keyword:
        return 0
    c = content or ""
    k = keyword.strip()
    if not k:
        return 0
    return len(re.findall(re.escape(k), c, flags=re.IGNORECASE))


def _normalize_space(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


class WriterAgent:
    def __init__(
        self,
        config_path: str = "agents/writer_agent/config.yaml",
        prompt_path: str = "agents/writer_agent/prompt.md",
        llm: Any = None,
    ):
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

        self.config_path = config_path
        self.prompt_path = prompt_path
        self.config = self._load_config()
        self._prompt_template: Optional[str] = None
        self.llm = llm
        if self.llm is None:
            self.llm = self._default_llm()

    def _default_llm(self) -> Any:
        cfg = (self.config or {}).get("llm") if isinstance(self.config, dict) else {}
        model = (cfg.get("model") or "deepseek-chat") if isinstance(cfg, dict) else "deepseek-chat"
        temperature = float((cfg.get("temperature") if isinstance(cfg, dict) else None) or 0.6)
        base_url = str((cfg.get("base_url") if isinstance(cfg, dict) else None) or "").strip() or None
        api_key_env = str((cfg.get("api_key") if isinstance(cfg, dict) else None) or "").strip()
        import os as _os
        api_key = _os.path.expandvars(api_key_env) if api_key_env else None
        try:
            from langchain_openai import ChatOpenAI
            kwargs = {"model": model, "temperature": temperature}
            if base_url:
                kwargs["base_url"] = base_url
            if api_key:
                kwargs["api_key"] = api_key
            return ChatOpenAI(**kwargs)
        except Exception:
            return None

    def _load_config(self) -> Dict[str, Any]:
        p = (self.config_path or "").strip()
        if p and os.path.exists(p):
            cfg_path = p
        else:
            cfg_path = str(Path(__file__).resolve().parent / "config.yaml")
            if not os.path.exists(cfg_path):
                return {}

        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _load_prompt(self) -> str:
        if self._prompt_template is not None:
            return self._prompt_template

        p = (self.prompt_path or "").strip()
        if p and os.path.exists(p):
            prompt_path = p
        else:
            prompt_path = str(Path(__file__).resolve().parent / "prompt.md")
            if not os.path.exists(prompt_path):
                self._prompt_template = ""
                return ""

        with open(prompt_path, "r", encoding="utf-8") as f:
            self._prompt_template = f.read()
        return self._prompt_template

    def _resolve_brand_config(self, brand_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        cfg = dict(brand_config or {}) if isinstance(brand_config, dict) else {}
        guide_path = str(cfg.get("brand_guide") or "").strip()
        if not guide_path:
            return cfg

        candidate = guide_path
        if not os.path.exists(candidate):
            project_root = Path(__file__).resolve().parents[2]
            candidate = str(project_root / guide_path)
        if not os.path.exists(candidate):
            return cfg

        try:
            with open(candidate, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        except Exception:
            return cfg

        merged = dict(cfg)
        tone = loaded.get("tone_of_voice") or []
        if tone and not merged.get("tone"):
            merged["tone"] = tone
        vocab = loaded.get("vocabulary_constraints") or {}
        if isinstance(vocab, dict):
            if vocab.get("avoid_words") and not merged.get("prohibited_words"):
                merged["prohibited_words"] = vocab.get("avoid_words")
            if vocab.get("preferred_terms") and not merged.get("recommended_words"):
                merged["recommended_words"] = vocab.get("preferred_terms")
        if loaded.get("formatting_rules") and not merged.get("must_include"):
            merged["must_include"] = loaded.get("formatting_rules")
        if loaded.get("brand_name") and not merged.get("brand_name"):
            merged["brand_name"] = loaded.get("brand_name")
        if loaded.get("target_audience") and not merged.get("target_audience"):
            merged["target_audience"] = loaded.get("target_audience")
        return merged

    def _placeholders(self, template: str) -> List[str]:
        return sorted(set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template or "")))

    def _render_prompt(self, template: str, context: Dict[str, Any]) -> str:
        s = template or ""
        for key in self._placeholders(s):
            val = context.get(key, "")
            if isinstance(val, (dict, list)):
                rep = json.dumps(val, ensure_ascii=False, indent=2)
            else:
                rep = "" if val is None else str(val)
            s = s.replace("{" + key + "}", rep)
        return s

    def _content_type_word_count(self, content_type: str) -> Tuple[int, int, int]:
        cfg = (self.config or {}).get("article") if isinstance(self.config, dict) else {}
        wc_cfg = (cfg.get("word_count") or {}) if isinstance(cfg, dict) else {}
        by_type = (wc_cfg.get("by_type") or {}) if isinstance(wc_cfg, dict) else {}
        min_wc = int(wc_cfg.get("min") or 1200)
        max_wc = int(wc_cfg.get("max") or 4000)
        t = (content_type or "").strip()
        preferred = by_type.get(t) if isinstance(by_type, dict) else None
        if preferred:
            target = int(preferred)
            min_wc = min(min_wc, target)
            max_wc = max(max_wc, target + 500)
            return target, min_wc, max_wc
        target = int((min_wc + max_wc) / 2)
        return target, min_wc, max_wc

    def _brief_bullets(self, items: Any, *, field: Optional[str] = None) -> str:
        if not isinstance(items, list):
            return ""
        lines: List[str] = []
        for item in items:
            if isinstance(item, dict) and field:
                text = _normalize_space(item.get(field))
            else:
                text = _normalize_space(item)
            if not text:
                continue
            lines.append(f"- {text}")
        return "\n".join(lines)

    def _brief_outline_markdown(self, writer_outline: Any) -> str:
        if not isinstance(writer_outline, dict):
            return ""
        sections = writer_outline.get("sections")
        if not isinstance(sections, list):
            return ""
        lines: List[str] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            title = _normalize_space(section.get("title"))
            if not title:
                continue
            lines.append(f"- {title}")
            for point in section.get("key_points") or []:
                text = _normalize_space(point)
                if text:
                    lines.append(f"  - {text}")
        return "\n".join(lines)

    def _research_brief_context(self, materials: Dict[str, Any]) -> Dict[str, str]:
        materials = materials if isinstance(materials, dict) else {}
        brief = materials.get("research_brief") if isinstance(materials.get("research_brief"), dict) else {}
        snapshot = brief.get("source_snapshot") if isinstance(brief.get("source_snapshot"), dict) else {}
        summary_parts = [
            _normalize_space(snapshot.get("source_title")),
            _normalize_space(snapshot.get("source_summary")),
        ]
        summary = "\n".join([f"- {part}" for part in summary_parts if part])
        if not summary:
            summary = self._brief_bullets(brief.get("source_highlights"))

        writer_outline = brief.get("writer_outline") if isinstance(brief.get("writer_outline"), dict) else {}
        return {
            "research_brief_summary": summary,
            "research_brief_highlights": self._brief_bullets(brief.get("source_highlights")),
            "research_brief_key_facts": self._brief_bullets(brief.get("key_facts"), field="fact"),
            "research_brief_constraints": self._brief_bullets(brief.get("rewrite_constraints")),
            "research_brief_risk_points": self._brief_bullets(brief.get("risk_points")),
            "research_brief_suggested_sections": self._brief_bullets(brief.get("suggested_sections")),
            "research_brief_writer_outline": self._brief_outline_markdown(writer_outline),
        }

    def _context(
        self,
        *,
        topic: Dict[str, Any],
        outline: Optional[Dict[str, Any]],
        materials: Dict[str, Any],
        brand_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        topic = topic if isinstance(topic, dict) else {}
        materials = materials if isinstance(materials, dict) else {}
        brand_config = self._resolve_brand_config(brand_config if isinstance(brand_config, dict) else {})

        title = str(topic.get("title") or "")
        content_type = str(topic.get("content_type") or "guide")
        search_intent = str(topic.get("search_intent") or "informational")

        primary_keyword = str(topic.get("primary_keyword") or "")
        if not primary_keyword:
            kws = topic.get("target_keywords")
            if isinstance(kws, list) and kws:
                primary_keyword = str(kws[0] or "")

        secondary_keywords = topic.get("secondary_keywords")
        if secondary_keywords is None:
            secondary_keywords = topic.get("target_keywords") or []
        if not isinstance(secondary_keywords, list):
            secondary_keywords = [str(secondary_keywords)]
        secondary_keywords = [str(x) for x in secondary_keywords if str(x).strip()]

        target_wc, min_wc, max_wc = self._content_type_word_count(content_type)
        if isinstance(topic.get("min_word_count"), int):
            min_wc = int(topic.get("min_word_count"))
        if isinstance(topic.get("max_word_count"), int):
            max_wc = int(topic.get("max_word_count"))
        if isinstance(topic.get("target_word_count"), int):
            target_wc = int(topic.get("target_word_count"))
        reading_time = max(1, int(target_wc / 300))

        brand_cfg = (self.config or {}).get("brand") if isinstance(self.config, dict) else {}
        tone = brand_config.get("tone") or brand_cfg.get("tone") or ["专业", "权威", "亲和"]
        must_include = brand_config.get("must_include") or brand_cfg.get("must_include") or []
        prohibited_words = brand_config.get("prohibited_words") or brand_cfg.get("prohibited_words") or []
        recommended_words = brand_config.get("recommended_words") or brand_cfg.get("recommended_words") or []
        brief = materials.get("research_brief") if isinstance(materials.get("research_brief"), dict) else {}
        writer_outline = brief.get("writer_outline") if isinstance(brief.get("writer_outline"), dict) else {}
        outline_val = (
            outline
            or writer_outline
            or materials.get("outline")
            or materials.get("detailed_outline")
            or materials.get("hierarchy_outline")
            or {}
        )
        hierarchy_outline = outline_val
        if isinstance(outline_val, str):
            hierarchy_outline = outline_val
        brief_context = self._research_brief_context(materials)

        meta_cfg = ((self.config or {}).get("seo") or {}).get("meta_description") if isinstance(self.config, dict) else {}
        meta_min = int(meta_cfg.get("min_length") or 120) if isinstance(meta_cfg, dict) else 120
        meta_max = int(meta_cfg.get("max_length") or 160) if isinstance(meta_cfg, dict) else 160
        meta_requirements = f"{meta_min}-{meta_max}字符，必须包含主关键词"

        return {
            "title": title,
            "primary_keyword": primary_keyword,
            "secondary_keywords": secondary_keywords,
            "content_type": content_type,
            "search_intent": search_intent,
            "hierarchy_outline": hierarchy_outline,
            "research_materials": materials,
            "brand_tone": "、".join([str(x) for x in tone]) if isinstance(tone, list) else str(tone),
            "brand_must_include": must_include,
            "prohibited_words": prohibited_words,
            "recommended_words": recommended_words,
            "target_word_count": target_wc,
            "min_word_count": min_wc,
            "reading_time": reading_time,
            "meta_description_requirements": meta_requirements,
            **brief_context,
        }

    async def _call_llm(self, prompt: str) -> str:
        if self.llm is None:
            raise RuntimeError("writer_llm_not_configured")

        messages = [
            SystemMessage(content="你是高级撰稿人，必须输出纯 JSON，不要输出代码块或解释文字。"),
            HumanMessage(content=prompt),
        ]

        if hasattr(self.llm, "ainvoke"):
            resp = await self.llm.ainvoke(messages)
            return resp.content if hasattr(resp, "content") else str(resp)

        resp = await asyncio.to_thread(self.llm.invoke, messages)
        return resp.content if hasattr(resp, "content") else str(resp)

    def _normalize_output(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}
        article = payload.get("article") if isinstance(payload.get("article"), dict) else {}
        out_article = {
            "title": str(article.get("title") or ""),
            "content_md": str(article.get("content_md") or article.get("content") or ""),
            "meta_description": str(article.get("meta_description") or ""),
        }

        stats = payload.get("statistics") if isinstance(payload.get("statistics"), dict) else {}
        out_stats = {
            "word_count": int(stats.get("word_count") or 0),
            "reading_time_minutes": int(stats.get("reading_time_minutes") or 0),
        }

        return {
            "article": out_article,
            "seo_analysis": payload.get("seo_analysis") if isinstance(payload.get("seo_analysis"), dict) else {},
            "internal_links": payload.get("internal_links") if isinstance(payload.get("internal_links"), list) else [],
            "image_alt_texts": payload.get("image_alt_texts") if isinstance(payload.get("image_alt_texts"), list) else [],
            "statistics": out_stats,
            "quality_checks": payload.get("quality_checks") if isinstance(payload.get("quality_checks"), dict) else {},
            "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
        }

    def _extract_citation_urls(self, materials: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        if not isinstance(materials, dict):
            return out
        sources = materials.get("sources")
        citations = materials.get("citations")
        pool: List[Any] = []
        if isinstance(sources, list):
            pool.extend(sources)
        if isinstance(citations, list):
            pool.extend(citations)
        for it in pool:
            if isinstance(it, dict):
                u = str(it.get("url") or it.get("link") or "").strip()
                if u:
                    out.append(u)
                t = str(it.get("text") or it.get("citation") or "").strip()
                if "http" in t:
                    out.extend(re.findall(r"https?://[^\s\)]+", t))
            elif isinstance(it, str) and "http" in it:
                out.extend(re.findall(r"https?://[^\s\)]+", it))
        uniq: List[str] = []
        seen = set()
        for u in out:
            v = u.strip()
            if not v or v in seen:
                continue
            seen.add(v)
            uniq.append(v)
        return uniq

    def _citation_check(self, content_md: str, materials: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        urls = self._extract_citation_urls(materials)
        text = content_md or ""
        used = [u for u in urls if u in text]
        unused = [u for u in urls if u not in text]
        has_section = "## 参考来源" in text or "## 参考资料" in text
        passed = True
        if urls:
            passed = bool(has_section and used)
        return passed, {"passed": passed, "used": used, "unused": unused}

    def _keyword_density_check(self, *, content_md: str, primary_keyword: str, secondary_keywords: List[str]) -> Dict[str, Any]:
        total_words = _word_count(content_md)
        primary_count = _count_keyword_occurrences(content_md, primary_keyword)
        primary_density = (primary_count / max(total_words, 1)) * 100.0

        secondary_counts: Dict[str, int] = {}
        for k in secondary_keywords or []:
            secondary_counts[k] = _count_keyword_occurrences(content_md, k)

        cfg = (self.config or {}).get("seo") if isinstance(self.config, dict) else {}
        dens_cfg = (cfg.get("keyword_density") or {}) if isinstance(cfg, dict) else {}
        p_cfg = dens_cfg.get("primary") if isinstance(dens_cfg.get("primary"), dict) else {}
        s_cfg = dens_cfg.get("secondary") if isinstance(dens_cfg.get("secondary"), dict) else {}
        p_min = float(p_cfg.get("min") or 1.0)
        p_max = float(p_cfg.get("max") or 2.5)
        s_min = float(s_cfg.get("min") or 0.5)
        s_max = float(s_cfg.get("max") or 1.5)

        primary_pass = (not primary_keyword) or (p_min <= primary_density <= p_max)
        secondary_pass = True
        for k, c in secondary_counts.items():
            if not k:
                continue
            d = (c / max(total_words, 1)) * 100.0
            if c > 0 and not (s_min <= d <= s_max):
                secondary_pass = False

        return {
            "passed": bool(primary_pass and secondary_pass),
            "total_words": total_words,
            "primary_keyword": primary_keyword,
            "primary_count": primary_count,
            "primary_density_percent": round(primary_density, 2),
            "secondary_counts": secondary_counts,
        }

    def _prohibited_words_check(self, content_md: str) -> Dict[str, Any]:
        brand_cfg = (self.config or {}).get("brand") if isinstance(self.config, dict) else {}
        prohibited = brand_cfg.get("prohibited_words") if isinstance(brand_cfg, dict) else []
        prohibited = prohibited if isinstance(prohibited, list) else []
        hits: List[str] = []
        text = content_md or ""
        for w in prohibited:
            s = str(w or "").strip()
            if not s:
                continue
            if s in text:
                hits.append(s)
        return {"passed": not hits, "hits": hits}

    def _word_count_check(self, *, content_md: str, content_type: str, topic: Dict[str, Any]) -> Dict[str, Any]:
        total = _word_count(content_md)
        target, min_wc, max_wc = self._content_type_word_count(content_type)
        if isinstance(topic.get("min_word_count"), int):
            min_wc = int(topic.get("min_word_count"))
        if isinstance(topic.get("max_word_count"), int):
            max_wc = int(topic.get("max_word_count"))
        passed = bool(min_wc <= total <= max_wc)
        return {"passed": passed, "word_count": total, "min": min_wc, "max": max_wc, "target": target}

    def _paragraph_length_check(self, content_md: str) -> Dict[str, Any]:
        cfg = (self.config or {}).get("article") if isinstance(self.config, dict) else {}
        para_cfg = (cfg.get("paragraph") or {}) if isinstance(cfg, dict) else {}
        max_len = int(para_cfg.get("max_length") or 150) if isinstance(para_cfg, dict) else 150
        min_len = int(para_cfg.get("min_length") or 50) if isinstance(para_cfg, dict) else 50

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content_md or "") if p.strip()]
        lens = [len(re.sub(r"\s+", "", p)) for p in paragraphs]
        too_long = sum(1 for n in lens if n > max_len)
        too_short = sum(1 for n in lens if n < min_len)
        passed = bool(too_long == 0)
        return {
            "passed": passed,
            "paragraphs": len(paragraphs),
            "too_long": too_long,
            "too_short": too_short,
            "max_length": max_len,
            "min_length": min_len,
        }

    def _readability_check(self, content_md: str) -> Dict[str, Any]:
        checker = ReadabilityChecker()
        result = checker.check(content_md or "", language="auto")
        return checker.to_dict(result)

    def _quality_gate(
        self,
        *,
        topic: Dict[str, Any],
        content_md: str,
        materials: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, Any], List[str]]:
        warnings: List[str] = []
        t = topic if isinstance(topic, dict) else {}
        content_type = str(t.get("content_type") or context.get("content_type") or "guide")
        primary_keyword = str(context.get("primary_keyword") or "")
        secondary_keywords = context.get("secondary_keywords")
        secondary_keywords = secondary_keywords if isinstance(secondary_keywords, list) else []

        checks: Dict[str, Any] = {}
        checks["word_count"] = self._word_count_check(content_md=content_md, content_type=content_type, topic=t)
        checks["paragraph_length"] = self._paragraph_length_check(content_md)
        checks["no_prohibited_words"] = self._prohibited_words_check(content_md)
        checks["keyword_density"] = self._keyword_density_check(
            content_md=content_md,
            primary_keyword=primary_keyword,
            secondary_keywords=secondary_keywords,
        )
        checks["readability"] = self._readability_check(content_md)

        citations_passed, citations_payload = self._citation_check(content_md, materials)
        checks["citations"] = citations_payload

        passed = True
        fail_reasons: List[str] = []

        if not checks["word_count"]["passed"]:
            passed = False
            fail_reasons.append("word_count_out_of_range")
        if not checks["paragraph_length"]["passed"]:
            passed = False
            fail_reasons.append("paragraphs_too_long")
        if not checks["no_prohibited_words"]["passed"]:
            passed = False
            fail_reasons.append("contains_prohibited_words")
        if not checks["keyword_density"]["passed"]:
            passed = False
            fail_reasons.append("keyword_density_out_of_range")
        if not citations_passed:
            passed = False
            fail_reasons.append("missing_citation_backlinks")

        if not passed:
            warnings.extend(fail_reasons)

        return passed, checks, warnings

    def _rewrite_instruction(self, reasons: List[str], checks: Dict[str, Any]) -> str:
        parts: List[str] = []
        if "word_count_out_of_range" in reasons and isinstance(checks.get("word_count"), dict):
            wc = checks["word_count"]
            parts.append(f"把正文长度调整到 {wc.get('min')}-{wc.get('max')} 字范围内。")
        if "paragraphs_too_long" in reasons:
            parts.append("把过长段落拆分为更短段落（每段尽量不超过配置要求）。")
        if "contains_prohibited_words" in reasons and isinstance(checks.get("no_prohibited_words"), dict):
            hits = checks["no_prohibited_words"].get("hits") or []
            if hits:
                parts.append("移除或改写以下禁用词：" + "、".join([str(x) for x in hits]) + "。")
            else:
                parts.append("移除或改写禁用词。")
        if "keyword_density_out_of_range" in reasons and isinstance(checks.get("keyword_density"), dict):
            kd = checks["keyword_density"]
            parts.append(f"调整主关键词出现次数与分布，使密度更接近 1-2.5%。当前密度 {kd.get('primary_density_percent')}%。")
        if "missing_citation_backlinks" in reasons:
            parts.append("在文末添加“## 参考来源”小节，至少列出 1 条来自调研素材的可回链 URL，并在正文对应段落自然引用。")
        if not parts:
            parts.append("修复质量问题并保持输出 JSON 契约不变。")
        return "\n".join(parts)

    def _finalize_statistics(self, payload: Dict[str, Any]) -> None:
        article = payload.get("article") or {}
        content_md = str(article.get("content_md") or "")
        wc = _word_count(content_md)
        payload["statistics"]["word_count"] = wc
        payload["statistics"]["reading_time_minutes"] = max(1, int(wc / 300))

    async def execute(
        self,
        *,
        topic: Dict[str, Any],
        outline: Optional[Dict[str, Any]] = None,
        materials: Dict[str, Any],
        brand_config: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 从 original_url 抓取原文（如有需要）
        if not (materials or {}).get("source_content") and not (topic or {}).get("source_content"):
            url = (topic or {}).get("original_url") or (materials or {}).get("original_url")
            if url:
                fetcher = URLContentFetcher()
                result = await fetcher.fetch(str(url))
                if result.success and result.content:
                    materials = dict(materials or {})
                    materials["source_content"] = result.content

        template = self._load_prompt()
        context = self._context(topic=topic, outline=outline, materials=materials, brand_config=brand_config)
        prompt = self._render_prompt(template, context)

        placeholders_left = self._placeholders(prompt)
        if placeholders_left:
            return {
                "article": {"title": str(context.get("title") or ""), "content_md": "", "meta_description": ""},
                "seo_analysis": {},
                "internal_links": [],
                "image_alt_texts": [],
                "statistics": {"word_count": 0, "reading_time_minutes": 0},
                "quality_checks": {"prompt_render": {"passed": False, "missing": placeholders_left}},
                "warnings": ["prompt_placeholders_not_filled"],
                "generated_at": datetime.now().isoformat(),
            }

        max_retries = 1  # 最多重试1次，选最高分
        last_checks: Dict[str, Any] = {}
        last_warnings: List[str] = []
        for attempt in range(max_retries + 1):
            raw = await self._call_llm(prompt)
            try:
                payload = _extract_json(raw)
            except Exception as e:
                if attempt >= max_retries:
                    return {
                        "article": {"title": str(context.get("title") or ""), "content_md": "", "meta_description": ""},
                        "seo_analysis": {},
                        "internal_links": [],
                        "image_alt_texts": [],
                        "statistics": {"word_count": 0, "reading_time_minutes": 0},
                        "quality_checks": {"json_parse": {"passed": False, "error": str(e)}},
                        "warnings": ["json_parse_failed"],
                        "generated_at": datetime.now().isoformat(),
                    }
                prompt = prompt + "\n\n" + "只输出一个 JSON 对象（不要代码块/解释文字）。"
                continue

            out = self._normalize_output(payload)
            self._finalize_statistics(out)

            passed, checks, warnings = self._quality_gate(
                topic=topic,
                content_md=out["article"]["content_md"],
                materials=materials,
                context=context,
            )
            out["quality_checks"] = checks
            out["warnings"] = list(out.get("warnings") or []) + warnings
            out["generated_at"] = datetime.now().isoformat()

            if passed:
                return out

            last_checks = checks
            last_warnings = warnings

            if attempt >= max_retries:
                return out

            prompt = prompt + "\n\n" + "修复要求：\n" + self._rewrite_instruction(warnings, checks) + "\n并保持输出 JSON 契约不变。"

        return {
            "article": {"title": str(context.get("title") or ""), "content_md": "", "meta_description": ""},
            "seo_analysis": {},
            "internal_links": [],
            "image_alt_texts": [],
            "statistics": {"word_count": 0, "reading_time_minutes": 0},
            "quality_checks": last_checks,
            "warnings": last_warnings,
            "generated_at": datetime.now().isoformat(),
        }
