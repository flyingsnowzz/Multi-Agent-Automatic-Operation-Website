import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List
import asyncio
import hashlib

import yaml

from agents.cms_agent.tools.cms_client import CMSClient
from agents.cms_agent.tools.media_uploader import MediaUploader


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


@dataclass
class PublishDecision:
    dry_run: bool
    env_gate: bool

    @property
    def can_publish(self) -> bool:
        return (not self.dry_run) and self.env_gate


class CMSAgent:
    def __init__(self, config_path: str = "agents/cms_agent/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _deep_env_resolve(raw)

    def _get_publish_decision(self) -> PublishDecision:
        publishing = (self.config or {}).get("publishing") or {}
        dry_run = bool(publishing.get("dry_run", True))
        env_val = (os.environ.get("CMS_ENABLE_REAL_PUBLISH") or "").strip().lower()
        env_gate = env_val in {"1", "true", "yes", "y", "on"}
        return PublishDecision(dry_run=dry_run, env_gate=env_gate)

    def _extract_article_payload(
        self,
        article: Dict[str, Any],
        page_info: Dict[str, Any],
        images: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        title = (article or {}).get("title") or ""
        content_html = (article or {}).get("content_html")
        content_md = (article or {}).get("content_md") or (article or {}).get("content") or ""
        content = content_html or content_md

        meta = (article or {}).get("meta") or {}
        meta_title = meta.get("meta_title") or meta.get("seo_title") or (article or {}).get("meta_title") or ""
        meta_description = meta.get("meta_description") or meta.get("seo_description") or (article or {}).get(
            "meta_description"
        ) or ""

        slug = (page_info or {}).get("slug") or (article or {}).get("slug") or ""
        category = (page_info or {}).get("category")
        tags = (page_info or {}).get("tags") or []
        primary_keyword = (page_info or {}).get("primary_keyword") or (article or {}).get("primary_keyword") or ""

        featured_image_url = (article or {}).get("featured_image_url") or ""
        if not featured_image_url and images:
            featured_image_url = (
                images.get("featured_image_url")
                or images.get("cover_url")
                or images.get("cover_image_url")
                or ""
            )

        payload = {
            "title": title,
            "content": content,
            "content_html": content_html or "",
            "content_md": content_md or "",
            "excerpt": meta_description,
            "slug": slug,
            "category": category,
            "tags": tags,
            "featured_image_url": featured_image_url,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "primary_keyword": primary_keyword,
            "schema_json": meta.get("schema_json") or (article or {}).get("schema_json"),
            "topic_id": (page_info or {}).get("topic_id") or (article or {}).get("topic_id"),
        }
        return payload

    def _apply_category_mapping(self, category: Any) -> Any:
        mapping = (((self.config or {}).get("content_mapping") or {}).get("category_mapping") or {})
        if isinstance(category, str):
            return mapping.get(category, category)
        return category

    def _normalize_tags(self, tags: Any) -> List[str]:
        if tags is None:
            return []
        if isinstance(tags, str):
            return [t.strip() for t in tags.split(",") if t.strip()]
        if isinstance(tags, list):
            out = []
            for t in tags:
                if isinstance(t, str) and t.strip():
                    out.append(t.strip())
            return out
        return []

    def _apply_tag_strategy(self, *, tags: List[str], primary_keyword: str) -> List[str]:
        tag_cfg = (((self.config or {}).get("content_mapping") or {}).get("tags") or {})
        auto_generate = bool(tag_cfg.get("auto_generate", False))
        max_tags = int(tag_cfg.get("max_tags", 5) or 5)
        use_primary = bool(tag_cfg.get("use_primary_keyword", True))

        out = list(tags)
        if auto_generate and use_primary and primary_keyword:
            if primary_keyword not in out:
                out.insert(0, primary_keyword)
        uniq = []
        seen = set()
        for t in out:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq[:max_tags] if max_tags > 0 else uniq

    def _build_wp_meta(self, *, meta_title: str, meta_description: str, focus_keyword: str) -> Optional[Dict[str, Any]]:
        seo_cfg = (self.config or {}).get("seo_fields") or {}
        yoast = bool(seo_cfg.get("yoast_compatible", False))
        rankmath = bool(seo_cfg.get("rankmath_compatible", False))
        if not yoast and not rankmath:
            return None
        if yoast:
            m = {}
            if meta_title:
                m["_yoast_wpseo_title"] = meta_title
            if meta_description:
                m["_yoast_wpseo_metadesc"] = meta_description
            if focus_keyword:
                m["_yoast_wpseo_focuskw"] = focus_keyword
            return m
        if rankmath:
            m = {}
            if meta_title:
                m["rank_math_title"] = meta_title
            if meta_description:
                m["rank_math_description"] = meta_description
            if focus_keyword:
                m["rank_math_focus_keyword"] = focus_keyword
            return m
        return None

    async def _apply_mappings(
        self,
        *,
        payload: Dict[str, Any],
        client: Optional[CMSClient],
        provider: str,
    ) -> Dict[str, Any]:
        payload["category"] = self._apply_category_mapping(payload.get("category"))
        tags = self._normalize_tags(payload.get("tags"))
        tags = self._apply_tag_strategy(tags=tags, primary_keyword=payload.get("primary_keyword") or "")
        payload["tags"] = tags

        if provider == "wordpress" and client is not None:
            if isinstance(payload.get("category"), str):
                cid = await client.resolve_wordpress_category_id(payload["category"])
                payload["category"] = [cid] if cid is not None else []
            if isinstance(payload.get("tags"), list):
                tag_ids = []
                for t in payload["tags"]:
                    tid = await client.resolve_wordpress_tag_id(t)
                    if tid is None:
                        tid = await client.create_wordpress_tag(t)
                    if tid is not None:
                        tag_ids.append(tid)
                payload["tags"] = tag_ids
        return payload

    def _compute_publish_date(self) -> Optional[str]:
        publishing = (self.config or {}).get("publishing") or {}
        mode = publishing.get("mode") or "draft"
        scheduled = publishing.get("scheduled") or {}
        if mode != "scheduled" and not bool(scheduled.get("enabled", False)):
            return None
        default_time = scheduled.get("default_time") or "09:00"
        try:
            hh, mm = default_time.split(":")
            hour = int(hh)
            minute = int(mm)
        except Exception:
            hour = 9
            minute = 0
        tz_offset = "+08:00"
        now = datetime.now()
        publish_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if publish_dt <= now:
            publish_dt = publish_dt + timedelta(days=1)
        return publish_dt.isoformat() + tz_offset

    async def _retry(self, *, fn, retry_cfg: Dict[str, Any]) -> Any:
        enabled = bool(retry_cfg.get("enabled", False))
        max_retries = int(retry_cfg.get("max_retries", 0) or 0)
        delay_seconds = int(retry_cfg.get("delay_seconds", 0) or 0)
        if not enabled or max_retries <= 0:
            return await fn()
        last_exc = None
        for i in range(max_retries + 1):
            try:
                return await fn()
            except Exception as e:
                last_exc = e
                if i >= max_retries:
                    raise
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
        raise last_exc

    def _write_publish_history(self, record: Dict[str, Any]) -> None:
        logging_cfg = (self.config or {}).get("logging") or {}
        if not bool(logging_cfg.get("save_publish_history", False)):
            return
        out_dir = logging_cfg.get("output_dir") or "logs/cms_agent"
        try:
            os.makedirs(out_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug = (record.get("payload") or {}).get("slug") or "no_slug"
            safe_slug = "".join([c for c in str(slug) if c.isalnum() or c in {"-", "_"}])[:60] or "no_slug"
            path = os.path.join(out_dir, f"{ts}_{safe_slug}.json")
            with open(path, "w", encoding="utf-8") as f:
                import json

                json.dump(record, f, ensure_ascii=False, indent=2)
        except Exception:
            return

    def _slugify(self, title: str) -> str:
        import re

        slug_cfg = ((self.config or {}).get("url") or {}).get("slug") or {}
        max_length = int(slug_cfg.get("max_length", 60) or 60)
        sep = slug_cfg.get("separator") or "-"
        lowercase = bool(slug_cfg.get("lowercase", True))

        s = (title or "").strip()
        if lowercase:
            s = s.lower()
        s = re.sub(r"[^a-z0-9]+", sep, s)
        s = s.strip(sep)
        if not s:
            seed = str(title or "").strip() or "article"
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
            s = f"article{sep}{digest}"
        if max_length and len(s) > max_length:
            s = s[:max_length].strip(sep)
        return s

    def _ensure_slug(self, payload: Dict[str, Any], article: Dict[str, Any], page_info: Dict[str, Any]) -> str:
        if payload.get("slug"):
            return str(payload["slug"]).strip()
        fallback_seed = (
            str(payload.get("topic_id") or "")
            or str((article or {}).get("id") or "")
            or str((page_info or {}).get("id") or "")
            or str(payload.get("title") or "")
            or str(payload.get("content_md") or payload.get("content_html") or payload.get("content") or "")
        )
        slug_cfg = ((self.config or {}).get("url") or {}).get("slug") or {}
        max_length = int(slug_cfg.get("max_length", 60) or 60)
        sep = slug_cfg.get("separator") or "-"
        lowercase = bool(slug_cfg.get("lowercase", True))

        import re

        normalized_seed = fallback_seed.strip()
        if lowercase:
            normalized_seed = normalized_seed.lower()
        normalized_seed = re.sub(r"[^a-z0-9]+", sep, normalized_seed).strip(sep)
        if normalized_seed:
            slug = normalized_seed
        else:
            digest_source = fallback_seed or "article"
            digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:8]
            slug = f"article{sep}{digest}"
        if max_length and len(slug) > max_length:
            slug = slug[:max_length].strip(sep)
        return slug

    def _resolve_publish_status(self) -> str:
        publishing = (self.config or {}).get("publishing") or {}
        mode = publishing.get("mode") or "draft"
        if mode == "draft":
            return "draft"
        if mode == "scheduled":
            return "scheduled"
        return "publish"

    def _is_remote_slug_check_allowed(self, *, decision: PublishDecision) -> bool:
        publishing = (self.config or {}).get("publishing") or {}
        if decision.can_publish:
            return True
        return bool(publishing.get("slug_check_in_dry_run", False))

    def _get_contract_required_fields(self) -> List[str]:
        cms_cfg = (self.config or {}).get("cms") or {}
        custom_cfg = (cms_cfg.get("custom") or {}) if isinstance(cms_cfg, dict) else {}
        post_contract = (custom_cfg.get("post_contract") or {}) if isinstance(custom_cfg, dict) else {}
        required_fields = post_contract.get("required_fields") or []
        return [str(field).strip() for field in required_fields if str(field).strip()]

    def _get_contract_content_field(self) -> str:
        cms_cfg = (self.config or {}).get("cms") or {}
        custom_cfg = (cms_cfg.get("custom") or {}) if isinstance(cms_cfg, dict) else {}
        post_contract = (custom_cfg.get("post_contract") or {}) if isinstance(custom_cfg, dict) else {}
        return str(post_contract.get("content_field") or "content_html").strip() or "content_html"

    def _map_contract_field_to_check(self, field: str) -> Optional[str]:
        normalized = str(field or "").strip()
        if not normalized:
            return None
        content_field = self._get_contract_content_field()
        mapping = {
            "title": "title_not_empty",
            "slug": "slug_not_empty",
            "status": "status_not_empty",
        }
        if normalized == content_field or normalized == "content_html":
            return "content_html_not_empty"
        return mapping.get(normalized)

    def _get_effective_required_checks(self) -> set[str]:
        publishing = (self.config or {}).get("publishing") or {}
        checks_required = {str(item).strip() for item in (publishing.get("pre_publish_check") or []) if str(item).strip()}
        for field in self._get_contract_required_fields():
            mapped = self._map_contract_field_to_check(field)
            if mapped:
                checks_required.add(mapped)
        return checks_required

    async def _resolve_slug(
        self,
        *,
        client: CMSClient,
        slug: str,
        strategy: str = "auto_rewrite",
        max_tries: int = 10,
    ) -> Dict[str, Any]:
        if not slug:
            return {"success": False, "slug": slug, "error": "empty_slug"}

        existing_result = await client.find_posts_by_slug_result(slug)
        if not existing_result.get("success"):
            return {
                "success": False,
                "slug": slug,
                "error": "slug_lookup_failed",
                "details": existing_result.get("details") or existing_result.get("error"),
            }
        existing = existing_result.get("items") or []
        if not existing:
            return {"success": True, "slug": slug, "changed": False}

        if strategy == "overwrite_update":
            post_id = client.extract_post_id(existing[0])
            if post_id is None:
                return {"success": False, "slug": slug, "error": "slug_conflict_missing_post_id"}
            return {
                "success": True,
                "slug": slug,
                "changed": False,
                "update_existing": True,
                "post_id": post_id,
            }

        if strategy == "fail":
            return {"success": False, "slug": slug, "error": "slug_conflict"}

        for i in range(2, max_tries + 2):
            candidate = f"{slug}-{i}"
            exists_result = await client.slug_exists_result(candidate)
            if not exists_result.get("success"):
                return {
                    "success": False,
                    "slug": slug,
                    "error": "slug_lookup_failed",
                    "details": exists_result.get("details") or exists_result.get("error"),
                }
            if not exists_result.get("exists"):
                return {"success": True, "slug": candidate, "changed": True}
        return {"success": False, "slug": slug, "error": "slug_conflict_unresolved"}

    async def _pre_publish_checks(
        self,
        *,
        payload: Dict[str, Any],
        client: Optional[CMSClient],
        publish_status: str,
        allow_remote_slug_check: bool,
    ) -> Dict[str, Any]:
        effective_checks = self._get_effective_required_checks()

        title_not_empty = bool((payload.get("title") or "").strip())
        content_not_empty = bool((payload.get("content") or "").strip())
        content_html_not_empty = bool((payload.get("content_html") or payload.get("content") or "").strip())
        slug_not_empty = bool((payload.get("slug") or "").strip())
        status_not_empty = bool((publish_status or "").strip())
        category_assigned = payload.get("category") not in (None, "", [])
        featured_required = bool((((self.config or {}).get("images") or {}).get("featured_image") or {}).get("required", False))
        featured_image_set = bool(payload.get("featured_image_url")) if featured_required else True

        slug_unique = True
        slug_checked = False
        slug_resolution: Dict[str, Any] = {}
        if "slug_unique" in effective_checks and allow_remote_slug_check and client and slug_not_empty:
            slug_checked = True
            publishing = (self.config or {}).get("publishing") or {}
            conflict_cfg = publishing.get("slug_conflict") or {}
            resolved = await self._resolve_slug(
                client=client,
                slug=payload["slug"],
                strategy=conflict_cfg.get("strategy") or "auto_rewrite",
                max_tries=int(conflict_cfg.get("max_rewrite_attempts", 10) or 10),
            )
            slug_resolution = resolved
            if resolved.get("success"):
                payload["slug"] = resolved.get("slug")
                if resolved.get("update_existing"):
                    payload["_cms_action"] = "update"
                    payload["_cms_post_id"] = resolved.get("post_id")
                else:
                    payload["_cms_action"] = "create"
                slug_unique = True
            else:
                slug_unique = False

        checks = {
            "title_not_empty": title_not_empty,
            "content_not_empty": content_not_empty,
            "content_html_not_empty": content_html_not_empty,
            "slug_not_empty": slug_not_empty,
            "status_not_empty": status_not_empty,
            "category_assigned": category_assigned,
            "featured_image_set": featured_image_set,
            "slug_unique": slug_unique,
            "slug_checked": slug_checked,
            "slug_resolution": slug_resolution,
        }

        errors = []
        def _append_error(code: str) -> None:
            if code not in errors:
                errors.append(code)

        if not title_not_empty:
            _append_error("title_not_empty")
        if "content_not_empty" in effective_checks and not content_not_empty:
            _append_error("content_not_empty")
        if "content_html_not_empty" in effective_checks and not content_html_not_empty:
            _append_error("content_html_not_empty")
        if "slug_not_empty" in effective_checks and not slug_not_empty:
            _append_error("slug_not_empty")
        if "status_not_empty" in effective_checks and not status_not_empty:
            _append_error("status_not_empty")
        if "category_assigned" in effective_checks and not category_assigned:
            _append_error("category_assigned")
        if "featured_image_set" in effective_checks and not featured_image_set:
            _append_error("featured_image_set")
        if "slug_unique" in effective_checks and not slug_unique:
            _append_error("slug_unique")
        if slug_checked and slug_resolution.get("error") == "slug_lookup_failed":
            _append_error("slug_lookup_failed")

        return {"checks": checks, "errors": errors}

    async def execute(
        self,
        article: Dict[str, Any],
        page_info: Dict[str, Any],
        images: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cms_cfg = (self.config or {}).get("cms") or {}
        provider = cms_cfg.get("provider") or "custom"
        api_cfg = (cms_cfg.get("api") or {}) if isinstance(cms_cfg, dict) else {}

        decision = self._get_publish_decision()
        publish_status = self._resolve_publish_status()
        publish_date = self._compute_publish_date()
        payload = self._extract_article_payload(article=article, page_info=page_info, images=images)
        payload["slug"] = self._ensure_slug(payload, article=article, page_info=page_info)
        payload["status"] = publish_status
        payload["publish_date"] = publish_date
        payload = await self._apply_mappings(payload=payload, client=None, provider=provider)

        client: Optional[CMSClient] = None
        uploader: Optional[MediaUploader] = None
        check_result: Dict[str, Any] = {"checks": {}, "errors": []}

        try:
            check_result = await self._pre_publish_checks(
                payload=payload,
                client=None,
                publish_status=publish_status,
                allow_remote_slug_check=False,
            )
            if check_result["errors"]:
                self._write_publish_history(
                    {
                        "provider": provider,
                        "contract_version": api_cfg.get("version"),
                        "decision": decision.__dict__,
                        "status": "failed",
                        "payload": payload,
                        "checks": check_result,
                    }
                )
                return {
                    "article_id": None,
                    "article_url": None,
                    "status": "failed",
                    "published_at": None,
                    "checks": check_result["checks"],
                    "errors": check_result["errors"],
                    "payload": payload,
                }

            allow_remote_slug_check = self._is_remote_slug_check_allowed(decision=decision)
            needs_client = bool(allow_remote_slug_check or decision.can_publish or provider == "wordpress")
            if needs_client:
                client = CMSClient(
                    provider=provider,
                    base_url=api_cfg.get("base_url"),
                    api_key=api_cfg.get("api_key"),
                    api_version=api_cfg.get("version"),
                    contract=self.config,
                )

            if allow_remote_slug_check and client is not None:
                if provider == "custom":
                    auth = await client.authenticate_if_needed()
                    if auth and not auth.get("success", True):
                        self._write_publish_history(
                            {
                                "provider": provider,
                                "contract_version": api_cfg.get("version"),
                                "decision": decision.__dict__,
                                "status": "failed",
                                "payload": payload,
                                "checks": check_result,
                                "result": auth,
                            }
                        )
                        return {
                            "article_id": None,
                            "article_url": None,
                            "status": "failed",
                            "published_at": None,
                            "checks": check_result["checks"],
                            "errors": [auth.get("error") or "auth_failed"],
                            "payload": payload,
                            "details": auth,
                        }
                check_result = await self._pre_publish_checks(
                    payload=payload,
                    client=client,
                    publish_status=publish_status,
                    allow_remote_slug_check=True,
                )
                if check_result["errors"]:
                    self._write_publish_history(
                        {
                            "provider": provider,
                            "contract_version": api_cfg.get("version"),
                            "decision": decision.__dict__,
                            "status": "failed",
                            "payload": payload,
                            "checks": check_result,
                        }
                    )
                    return {
                        "article_id": None,
                        "article_url": None,
                        "status": "failed",
                        "published_at": None,
                        "checks": check_result["checks"],
                        "errors": check_result["errors"],
                        "payload": payload,
                    }

            if not decision.can_publish:
                self._write_publish_history(
                    {
                        "provider": provider,
                        "contract_version": api_cfg.get("version"),
                        "decision": decision.__dict__,
                        "status": "dry_run",
                        "payload": payload,
                        "checks": check_result,
                    }
                )
                return {
                    "article_id": None,
                    "article_url": None,
                    "status": "dry_run",
                    "published_at": None,
                    "checks": check_result["checks"],
                    "errors": [],
                    "payload": payload,
                }

            exec_cfg = (self.config or {}).get("execution") or {}
            retry_cfg = exec_cfg.get("retry") or {}
            if client is None:
                client = CMSClient(
                    provider=provider,
                    base_url=api_cfg.get("base_url"),
                    api_key=api_cfg.get("api_key"),
                    api_version=api_cfg.get("version"),
                    contract=self.config,
                )
            uploader = MediaUploader(
                provider=provider,
                base_url=api_cfg.get("base_url"),
                api_key=api_cfg.get("api_key"),
                api_version=api_cfg.get("version"),
                contract=self.config,
            )

            if provider == "custom":
                auth = await client.authenticate_if_needed()
                if auth and not auth.get("success", True):
                    return {
                        "article_id": None,
                        "article_url": None,
                        "status": "failed",
                        "published_at": None,
                        "checks": check_result["checks"],
                        "errors": [auth.get("error") or "auth_failed"],
                        "payload": payload,
                    }
            payload = await self._apply_mappings(payload=payload, client=client, provider=provider)

            featured_image = None
            img_cfg = (self.config or {}).get("images") or {}
            upload_failure_strategy = str(img_cfg.get("upload_failure_strategy") or "fail").strip().lower() or "fail"
            if payload.get("featured_image_url") and ((self.config or {}).get("images") or {}).get("upload_to_cms", True):
                optimization = (img_cfg.get("optimization") or {}) if isinstance(img_cfg, dict) else {}
                requirements = (img_cfg.get("featured_image") or {}) if isinstance(img_cfg, dict) else {}
                async def _do_upload():
                    return await uploader.upload_file(
                        file_url=payload.get("featured_image_url") or None,
                        alt_text=(images or {}).get("featured_alt") or "",
                        title=payload.get("title") or "",
                        optimization=optimization,
                        requirements=requirements,
                    )
                up = await self._retry(fn=_do_upload, retry_cfg=retry_cfg)
                if up.get("success"):
                    featured_image = up.get("media_id") or up.get("url")
                else:
                    upload_error = up.get("error") or "featured_image_upload_failed"
                    if upload_failure_strategy == "use_original_url" and not requirements.get("required", False):
                        featured_image = payload.get("featured_image_url")
                    else:
                        self._write_publish_history(
                            {
                                "provider": provider,
                                "contract_version": api_cfg.get("version"),
                                "decision": decision.__dict__,
                                "status": "failed",
                                "payload": payload,
                                "checks": check_result,
                                "result": up,
                            }
                        )
                        return {
                            "article_id": None,
                            "article_url": None,
                            "status": "failed",
                            "published_at": None,
                            "checks": check_result["checks"],
                            "errors": [upload_error],
                            "payload": payload,
                            "details": up,
                        }

            async def _do_create():
                common = {
                    "title": payload.get("title") or "",
                    "content": payload.get("content") or "",
                    "slug": payload.get("slug") or None,
                    "status": publish_status,
                    "categories": payload.get("category"),
                    "tags": payload.get("tags"),
                    "featured_image": featured_image or payload.get("featured_image_url") or None,
                    "meta_title": payload.get("meta_title") or None,
                    "meta_description": payload.get("meta_description") or None,
                    "publish_date": publish_date,
                    "content_html": payload.get("content_html") or payload.get("content") or "",
                    "content_md": payload.get("content_md") or "",
                    "excerpt": payload.get("excerpt") or "",
                    "schema_json": payload.get("schema_json"),
                    "topic_id": payload.get("topic_id"),
                    "focus_keyword": payload.get("primary_keyword") or None,
                    "wp_meta": self._build_wp_meta(
                        meta_title=payload.get("meta_title") or "",
                        meta_description=payload.get("meta_description") or "",
                        focus_keyword=payload.get("primary_keyword") or "",
                    ),
                }
                if payload.get("_cms_action") == "update" and payload.get("_cms_post_id"):
                    post_id = int(payload["_cms_post_id"])
                    return await client.update_post(post_id, **common)
                return await client.create_post(
                    **common,
                )
            result = await self._retry(fn=_do_create, retry_cfg=retry_cfg)
        finally:
            if uploader is not None:
                await uploader.close()
            if client is not None:
                await client.close()

        if not result.get("success"):
            err_code = result.get("error") or "publish_failed"
            self._write_publish_history(
                {
                    "provider": provider,
                    "contract_version": api_cfg.get("version"),
                    "decision": decision.__dict__,
                    "status": "failed",
                    "payload": payload,
                    "checks": check_result,
                    "result": result,
                }
            )
            return {
                "article_id": None,
                "article_url": None,
                "status": "failed",
                "published_at": None,
                "checks": check_result["checks"],
                "errors": [err_code],
                "payload": payload,
                "details": result,
            }

        out = {
            "article_id": result.get("post_id"),
            "article_url": result.get("post_url"),
            "status": result.get("status") or publish_status,
            "published_at": datetime.now().isoformat(),
            "checks": check_result["checks"],
            "errors": [],
        }
        self._write_publish_history(
            {
                "provider": provider,
                "contract_version": api_cfg.get("version"),
                "decision": decision.__dict__,
                "status": out.get("status"),
                "payload": payload,
                "checks": check_result,
                "result": result,
            }
        )
        return out
