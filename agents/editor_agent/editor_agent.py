"""
EditorAgent — 发布前编辑

职责：
1. 错别字修复 + 政治审查（LLM）
2. Markdown → HTML 分段输出
3. 图片占位符插入
4. 敏感词安全过滤（后置过滤，命中则刷掉）
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import markdown
import yaml

from agents.editor_agent.tools.grammar_checker import GrammarChecker
from agents.editor_agent.tools.sensitive_filter import SensitiveFilter


def _deep_env_resolve(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            return os.environ.get(value[2:-1], "")
        return value
    if isinstance(value, dict):
        return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_env_resolve(v) for v in value]
    return value


def _bool_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class EditorAgent:
    """发布前编辑器"""

    def __init__(
        self,
        config_path: str = "agents/editor_agent/config.yaml",
        prompt_path: str = "agents/editor_agent/prompt.md",
    ):
        self.config_path = config_path
        self.prompt_path = prompt_path
        self.config = self._load_config()
        self._prompt_template: Optional[str] = None
        self._sensitive_filter: Optional[SensitiveFilter] = None

    # ---- config ----

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return _deep_env_resolve(yaml.safe_load(f) or {})

    def _load_prompt(self) -> str:
        if self._prompt_template is not None:
            return self._prompt_template
        if not os.path.exists(self.prompt_path):
            self._prompt_template = ""
            return ""
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            self._prompt_template = f.read()
        return self._prompt_template

    @property
    def sensitive_filter(self) -> SensitiveFilter:
        if self._sensitive_filter is None:
            self._sensitive_filter = SensitiveFilter()
            self._sensitive_filter.load()
        return self._sensitive_filter

    # ---- helpers ----

    @staticmethod
    def _get_content_md(article: Dict[str, Any]) -> str:
        if not isinstance(article, dict):
            return ""
        return str(
            article.get("content_md")
            or article.get("content")
            or article.get("content_html")
            or ""
        )

    # ---- grammar fix ----

    def _fix_grammar(self, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        """语法规则修复（标点、拼写等，非 LLM）。"""
        cfg = self.config.get("grammar", {}) or {}
        if not cfg.get("enabled", True):
            return text, []

        checker = GrammarChecker(language=cfg.get("language", "chinese"))
        result = checker.check(text)
        patches = result.get("patches") or []
        if not patches:
            return text, []
        return self._apply_patches(text, patches), patches

    @staticmethod
    def _apply_patches(text: str, patches: List[Dict[str, Any]]) -> str:
        cleaned: List[Tuple[int, int, str]] = []
        for p in patches:
            try:
                start = int(p.get("start", 0))
                end = int(p.get("end", 0))
                repl = str(p.get("replacement") or "")
            except (ValueError, TypeError):
                continue
            if start < 0 or end <= start or end > len(text):
                continue
            cleaned.append((start, end, repl))
        cleaned.sort(key=lambda x: x[0], reverse=True)
        out = text
        for start, end, repl in cleaned:
            out = out[:start] + repl + out[end:]
        return out

    # ---- image placeholders ----

    @staticmethod
    def _insert_image_placeholders(md_text: str, images: Optional[List[Dict[str, Any]]] = None) -> str:
        """将 {IMG: xxx} 占位符转为 HTML figure 标签。
        如果没有 image 数据，保留占位符作为 slot 标记。
        """
        def _repl(match: re.Match) -> str:
            pos_id = match.group(1).strip()
            if images:
                for img in images:
                    if not isinstance(img, dict):
                        continue
                    if img.get("position") == pos_id:
                        url = img.get("url") or ""
                        alt = img.get("alt") or ""
                        cap = img.get("caption") or ""
                        cap_html = f"<figcaption>{cap}</figcaption>" if cap else ""
                        return (
                            f'<figure class="article-image" data-position="{pos_id}">\n'
                            f'  <img src="{url}" alt="{alt}" loading="lazy">\n'
                            f'  {cap_html}\n'
                            f"</figure>"
                        ).strip()
            # 无图时保留为占位 slot
            return (
                f'<figure class="article-image article-image--placeholder" data-position="{pos_id}">\n'
                f"  <!-- image slot: {pos_id} -->\n"
                f"</figure>"
            )

        return re.sub(r"\{IMG:\s*([^}]+)\}", _repl, md_text)

    # ---- LLM ----

    def _fill_prompt(self, article: Dict[str, Any]) -> str:
        content_md = self._get_content_md(article)
        prompt = self._load_prompt()
        prompt = prompt.replace("{title}", str(article.get("title") or ""))
        prompt = prompt.replace("{content}", content_md)
        return prompt

    async def _call_llm(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """调 LLM 做错别字修正 + 政治审查。"""
        cfg = self.config.get("llm", {}) or {}
        model = cfg.get("model", "gpt-4o")
        base_url = cfg.get("base_url") or None
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""

        prompt = self._fill_prompt(article)

        try:
            import openai
        except ImportError:
            return {"success": False, "error": "openai_missing"}

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        if cfg.get("timeout"):
            client_kwargs["timeout"] = int(cfg["timeout"])

        client = openai.AsyncOpenAI(**client_kwargs)
        try:
            resp = await client.chat.completions.create(
                model=model,
                temperature=float(cfg.get("temperature", 0.2)),
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            return {"success": False, "error": "llm_request_failed", "details": str(e)}

        content = resp.choices[0].message.content if resp.choices else "{}"
        try:
            data = json.loads(content if isinstance(content, str) else "{}")
            if not isinstance(data, dict):
                data = {"raw": data}
            return {"success": True, "data": data}
        except json.JSONDecodeError:
            return {"success": False, "error": "llm_parse_failed", "raw": content}

    # ---- MD → HTML ----

    @staticmethod
    def _md_to_html(md_text: str) -> str:
        """将 Markdown 转为 HTML，自动分段。"""
        return markdown.markdown(
            md_text,
            extensions=["extra", "codehilite", "toc"],
            output_format="html5",
        )

    # ---- main ----

    async def execute(
        self,
        *,
        article: Dict[str, Any],
        images: Optional[List[Dict[str, Any]]] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        执行编辑流程：
        1. 语法规则修复
        2. 图片占位符 → HTML figure
        3. LLM 错别字修正 + 政治审查
        4. Markdown → HTML
        5. 敏感词安全过滤
        """
        title = str(article.get("title") or "")
        content_md = self._get_content_md(article)
        timestamp = datetime.now().isoformat()

        # 1. 语法修复
        fixed_md, grammar_patches = self._fix_grammar(content_md)

        # 2. 图片占位符插入
        fixed_md = self._insert_image_placeholders(fixed_md, images)

        # 3. 敏感词过滤（前置，节省 LLM token）
        safety_check = self.sensitive_filter.check(fixed_md)

        # 4. LLM 审校（错别字 + 政治审查，仅敏感词命中时启用）
        llm_data: Dict[str, Any] = {}
        llm_used = False
        llm_skipped_reason = ""
        if not dry_run and self.config.get("llm", {}).get("enabled", True):
            if not safety_check["passed"]:
                llm_used = True
                llm_result = await self._call_llm(
                    {"title": title, "content_md": fixed_md}
                )
                if llm_result.get("success") and isinstance(llm_result.get("data"), dict):
                    llm_data = llm_result["data"]
                    # LLM 返回的修正后文章
                    corrected_md = llm_data.get("corrected_content") or llm_data.get("content_md") or fixed_md
                    fixed_md = corrected_md
                else:
                    llm_data = {"llm_error": llm_result.get("error"), "llm_details": llm_result.get("details")}
            else:
                llm_skipped_reason = "sensitive_check_clean"

        # 5. Markdown → HTML
        content_html = self._md_to_html(fixed_md)

        # 组装结果
        return {
            "success": True,
            "timestamp": timestamp,
            "title": title,
            "content_md": fixed_md,
            "content_html": content_html,
            "grammar_patches": grammar_patches,
            "llm_review": {
                "used": llm_used,
                "typos_found": llm_data.get("typos_found") or [],
                "typos_fixed": llm_data.get("typos_fixed") or [],
                "political_review": llm_data.get("political_review") or {},
                "summary": llm_data.get("summary") or "",
            },
            "llm_skipped_reason": llm_skipped_reason,
            "safety_check": safety_check,
            "images_inserted": [
                (img.get("position") or "") for img in (images or []) if isinstance(img, dict)
            ],
        }
