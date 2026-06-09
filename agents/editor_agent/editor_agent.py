import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import yaml

from agents.editor_agent.tools.grammar_checker import GrammarChecker
from agents.editor_agent.tools.quality_scorer import QualityScorer


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


def _bool_env(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


class EditorAgent:
    def __init__(
        self,
        config_path: str = "agents/editor_agent/config.yaml",
        prompt_path: str = "agents/editor_agent/prompt.md",
    ):
        self.config_path = config_path
        self.prompt_path = prompt_path
        self.config = self._load_config()
        self._prompt_template = None

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _load_prompt(self) -> str:
        if self._prompt_template is not None:
            return self._prompt_template
        if not os.path.exists(self.prompt_path):
            self._prompt_template = ""
            return ""
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            self._prompt_template = f.read()
        return self._prompt_template

    def _get_article_content_md(self, article: Dict[str, Any]) -> str:
        if not isinstance(article, dict):
            return ""
        if article.get("content_md"):
            return str(article.get("content_md") or "")
        if article.get("content"):
            return str(article.get("content") or "")
        if article.get("content_html"):
            return str(article.get("content_html") or "")
        return ""

    def _normalize_output_article(self, payload: Dict[str, Any], *, fallback_article: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}

        article = payload.get("article")
        reviewed_article = payload.get("reviewed_article")
        revised_article = payload.get("revised_article")

        out_article: Dict[str, Any] = {}
        if isinstance(article, dict):
            out_article = dict(article)
        elif isinstance(reviewed_article, dict):
            out_article = {
                "title": reviewed_article.get("title") or "",
                "content_md": reviewed_article.get("content") or "",
                "meta_description": reviewed_article.get("meta_description") or "",
            }
        else:
            out_article = {}

        if not out_article.get("title"):
            out_article["title"] = (fallback_article or {}).get("title") or ""
        if not out_article.get("content_md"):
            out_article["content_md"] = self._get_article_content_md(fallback_article or {})
        if "meta_description" not in out_article:
            out_article["meta_description"] = (fallback_article or {}).get("meta_description") or ""

        if isinstance(revised_article, dict) and revised_article.get("content_md") and not out_article.get("content_md"):
            out_article["content_md"] = revised_article.get("content_md")

        return out_article

    def _approval_status(self, *, overall: float, issues_found: List[Dict[str, Any]]) -> str:
        cfg = self.config or {}
        quality_cfg = cfg.get("quality") or {}
        threshold = float(quality_cfg.get("pass_threshold") or 75)
        critical = set(quality_cfg.get("critical_issues") or [])

        if overall < threshold:
            return "rejected"

        for it in issues_found:
            if not isinstance(it, dict):
                continue
            t = str(it.get("type") or "")
            sev = str(it.get("severity") or "")
            if sev == "critical":
                return "conditional"
            if t and t in critical:
                return "conditional"
        return "approved"

    def _should_use_llm(self) -> bool:
        cfg = self.config or {}
        exec_cfg = cfg.get("execution") or {}
        if not bool(exec_cfg.get("llm_review_enabled", False)):
            return False
        return _bool_env("EDITOR_ENABLE_LLM")

    def _fill_prompt(self, *, article: Dict[str, Any], topic: Dict[str, Any]) -> str:
        content_md = self._get_article_content_md(article)
        prompt = self._load_prompt()
        prompt = prompt.replace("{title}", str(article.get("title") or ""))
        prompt = prompt.replace("{content}", content_md)
        prompt = prompt.replace("{content_type}", str(topic.get("content_type") or topic.get("type") or ""))
        prompt = prompt.replace("{primary_keyword}", str(topic.get("primary_keyword") or ""))
        prompt = prompt.replace("{word_count}", str(topic.get("word_count") or len(content_md)))
        return prompt

    async def _call_llm(self, prompt: str) -> Dict[str, Any]:
        llm_cfg = (self.config or {}).get("llm") or {}
        model = llm_cfg.get("model") or "gpt-4o"
        try:
            import openai
        except Exception as e:
            return {"success": False, "error": "openai_missing", "details": str(e)}

        client = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY") or None)
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            return {"success": False, "error": "llm_request_failed", "details": str(e)}

        try:
            content = resp.choices[0].message.content
            data = json.loads(content) if isinstance(content, str) else {}
            if not isinstance(data, dict):
                data = {"raw": data}
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": "llm_parse_failed", "details": str(e)}

    def _extract_patches(self, grammar_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        patches = grammar_result.get("patches") if isinstance(grammar_result, dict) else None
        if isinstance(patches, list):
            out = []
            for p in patches:
                if isinstance(p, dict):
                    out.append(p)
            return out
        return []

    def _apply_patches(self, text: str, patches: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        applied: List[Dict[str, Any]] = []
        cleaned: List[Tuple[int, int, str]] = []
        for p in patches:
            try:
                start = int(p.get("start"))
                end = int(p.get("end"))
                repl = str(p.get("replacement") or "")
            except Exception:
                continue
            if start < 0 or end <= start or end > len(text):
                continue
            cleaned.append((start, end, repl))

        cleaned.sort(key=lambda x: x[0], reverse=True)
        out = text
        for start, end, repl in cleaned:
            out = out[:start] + repl + out[end:]
            applied.append({"start": start, "end": end, "replacement": repl})
        applied.reverse()
        return out, applied

    async def execute(
        self,
        *,
        article: Dict[str, Any],
        topic: Optional[Dict[str, Any]] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        topic = topic or {}
        base_article = {"title": article.get("title") or "", "content_md": self._get_article_content_md(article)}
        meta_desc = article.get("meta_description") or (article.get("meta") or {}).get("meta_description") or ""
        base_article["meta_description"] = meta_desc

        grammar_cfg = (self.config or {}).get("review") or {}
        language = "chinese"
        checker = GrammarChecker(language=language)
        grammar_result = checker.check(base_article["content_md"])

        scorer = QualityScorer(config=self.config)
        score_result = scorer.score(
            article={
                "title": base_article["title"],
                "content_md": base_article["content_md"],
                "meta_description": base_article.get("meta_description") or "",
                "primary_keyword": topic.get("primary_keyword") or "",
            }
        )

        issues_found: List[Dict[str, Any]] = []
        for it in (grammar_result.get("issues") or []) if isinstance(grammar_result, dict) else []:
            if isinstance(it, dict):
                issues_found.append(
                    {
                        "type": it.get("error_type") or "grammar",
                        "severity": it.get("severity") or "warning",
                        "location": it.get("start"),
                        "description": it.get("message") or "",
                        "suggested_fix": it.get("suggestion") or "",
                    }
                )
        for it in (score_result.get("issues_found") or []) if isinstance(score_result, dict) else []:
            if isinstance(it, dict):
                issues_found.append(it)

        overall = 0.0
        quality_score = score_result.get("quality_score") if isinstance(score_result, dict) else None
        if isinstance(quality_score, dict) and isinstance(quality_score.get("overall"), (int, float)):
            overall = float(quality_score.get("overall"))
        approval_status = self._approval_status(overall=overall, issues_found=issues_found)

        content_md = base_article["content_md"]
        applied_patches: List[Dict[str, Any]] = []
        exec_cfg = (self.config or {}).get("execution") or {}
        auto_fix = exec_cfg.get("auto_fix") or {}
        if bool(auto_fix.get("enabled", False)):
            patches: List[Dict[str, Any]] = []
            if bool(auto_fix.get("fix_grammar", False)):
                patches.extend(self._extract_patches(grammar_result))
            if bool(auto_fix.get("fix_prohibited_words", False)):
                q_patches = score_result.get("patches") if isinstance(score_result, dict) else None
                if isinstance(q_patches, list):
                    for p in q_patches:
                        if isinstance(p, dict):
                            patches.append(p)
            if patches:
                content_md, applied_patches = self._apply_patches(content_md, patches)

        llm_payload: Dict[str, Any] = {}
        llm_used = False
        if (not dry_run) and self._should_use_llm():
            llm_used = True
            prompt = self._fill_prompt(article=base_article, topic=topic)
            llm_resp = await self._call_llm(prompt)
            if llm_resp.get("success") is True and isinstance(llm_resp.get("data"), dict):
                llm_payload = llm_resp["data"]
            else:
                llm_payload = {"llm_error": llm_resp.get("error"), "llm_details": llm_resp.get("details")}

        out_article = self._normalize_output_article(llm_payload, fallback_article={**base_article, "content_md": content_md})

        dimensions = {}
        if isinstance(quality_score, dict) and isinstance(quality_score.get("dimensions"), dict):
            dimensions = quality_score.get("dimensions") or {}

        result = {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "article": out_article,
            "quality_score": {"overall": overall, "dimensions": dimensions},
            "issues_found": issues_found,
            "polishing_notes": llm_payload.get("polishing_notes") if isinstance(llm_payload, dict) else [],
            "approval_status": approval_status,
            "tool_results": {"grammar": grammar_result, "quality": score_result},
            "auto_fix": {"applied_patches": applied_patches},
            "llm": {"used": llm_used},
        }
        result["reviewed_article"] = {
            "title": out_article.get("title") or "",
            "content": out_article.get("content_md") or "",
            "meta_description": out_article.get("meta_description") or "",
        }
        result["revised_article"] = {"content_md": out_article.get("content_md") or ""}
        return result
