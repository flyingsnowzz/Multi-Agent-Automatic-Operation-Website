#!/usr/bin/env python3
"""
CMS 后端适配客户端。
负责通过 API 与 CMS 系统交互，并把 provider/contract 差异归一化为统一结果。
"""

import os
import json
import base64
import httpx
from typing import Dict, List, Any, Optional, Sequence
from datetime import datetime
from urllib.parse import urljoin


class CMSClient:
    """CMS API客户端"""
    
    def __init__(
        self,
        provider: str = "custom",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        contract: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化CMS客户端
        
        Args:
            base_url: CMS API基础URL
            api_key: API密钥
            username: 用户名
            password: 密码
        """
        self.provider = (provider or "custom").strip().lower()
        self.api_version = (api_version or os.environ.get("CMS_API_VERSION") or "").strip().lstrip("/")

        env_base_url = os.environ.get("CMS_API_URL") or os.environ.get("CMS_BASE_URL") or "http://localhost:8080/api"
        self.base_url = (base_url or env_base_url).rstrip("/")

        self.api_key = api_key or os.environ.get("CMS_API_KEY", "")
        self.username = username or os.environ.get("CMS_USERNAME", "")
        self.password = password or os.environ.get("CMS_PASSWORD", "")
        self.token = None
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.contract = contract or {}

    @staticmethod
    def _custom_post_contract_from(contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        cms = contract.get("cms") if isinstance(contract, dict) else None
        custom = (cms or {}).get("custom") if isinstance(cms, dict) else None
        post_contract = (custom or {}).get("post_contract") if isinstance(custom, dict) else None
        return post_contract or {}

    def _get_custom_post_contract(self) -> Dict[str, Any]:
        return self._custom_post_contract_from(self.contract)

    @classmethod
    def business_checks_from_contract(cls, contract: Optional[Dict[str, Any]]) -> set[str]:
        pc = cls._custom_post_contract_from(contract)
        required_fields = pc.get("required_fields") if isinstance(pc, dict) else None
        content_field = str((pc.get("content_field") if isinstance(pc, dict) else None) or "content_html").strip() or "content_html"
        checks: set[str] = set()
        for raw_field in required_fields or []:
            field = str(raw_field or "").strip()
            if not field:
                continue
            if field == "title":
                checks.add("title_not_empty")
            elif field in {content_field, "content_html", "content"}:
                checks.add("content_not_empty")
            elif field == "slug":
                checks.add("slug_not_empty")
            elif field == "status":
                checks.add("status_valid")
        return checks

    @staticmethod
    def _extract_by_path(data: Any, path: str) -> Any:
        if not path:
            return None
        cur = data
        for part in path.split("."):
            if cur is None:
                return None
            if isinstance(cur, list):
                if not cur:
                    return None
                cur = cur[0]
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    @classmethod
    def _extract_by_paths(cls, data: Any, paths: Sequence[str]) -> Any:
        for p in paths or []:
            v = cls._extract_by_path(data, p)
            if v is not None:
                return v
        return None

    def _map_custom_status(self, status: str) -> str:
        pc = self._get_custom_post_contract()
        mapping = pc.get("status_mapping") if isinstance(pc, dict) else None
        if isinstance(mapping, dict) and status in mapping and mapping.get(status):
            return str(mapping[status])
        return status

    def _build_wordpress_meta(
        self,
        *,
        meta_title: Optional[str],
        meta_description: Optional[str],
        focus_keyword: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        seo_cfg = (self.contract.get("seo_fields") or {}) if isinstance(self.contract, dict) else {}
        yoast = bool(seo_cfg.get("yoast_compatible", False))
        rankmath = bool(seo_cfg.get("rankmath_compatible", False))
        if not yoast and not rankmath:
            return None

        if yoast:
            meta: Dict[str, Any] = {}
            if meta_title:
                meta["_yoast_wpseo_title"] = meta_title
            if meta_description:
                meta["_yoast_wpseo_metadesc"] = meta_description
            if focus_keyword:
                meta["_yoast_wpseo_focuskw"] = focus_keyword
            return meta or None

        if rankmath:
            meta = {}
            if meta_title:
                meta["rank_math_title"] = meta_title
            if meta_description:
                meta["rank_math_description"] = meta_description
            if focus_keyword:
                meta["rank_math_focus_keyword"] = focus_keyword
            return meta or None
        return None

    def _custom_request_path(self, key: str, default_path: str) -> str:
        pc = self._get_custom_post_contract()
        req = pc.get("request") if isinstance(pc, dict) else None
        if isinstance(req, dict) and req.get(key):
            return str(req[key])
        return default_path

    def _custom_response_paths(self, key: str, defaults: Sequence[str]) -> Sequence[str]:
        pc = self._get_custom_post_contract()
        rp = pc.get("response_paths") if isinstance(pc, dict) else None
        paths = rp.get(key) if isinstance(rp, dict) else None
        return paths or defaults

    def _custom_url(self, path: str) -> str:
        return self._join(self.api_version, path) if self.api_version else self._join(path)

    def _normalize_post_response(
        self,
        result: Any,
        *,
        fallback_post_id: Optional[int] = None,
        fallback_url: str = "",
        fallback_status: str = "",
        fallback_slug: str = "",
    ) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {
                "success": False,
                "error": "invalid_post_response",
                "data": result,
            }

        if self.provider == "custom":
            post_id = self._extract_by_paths(result, self._custom_response_paths("id", ["id"]))
            post_url = self._extract_by_paths(result, self._custom_response_paths("url", ["url", "link"])) or fallback_url or ""
            post_status = self._extract_by_paths(result, self._custom_response_paths("status", ["status"])) or fallback_status or ""
            post_slug = self._extract_by_paths(result, self._custom_response_paths("slug", ["slug"])) or fallback_slug or ""
            if post_id is None:
                post_id = fallback_post_id
            if post_id is None and not post_url:
                return {
                    "success": False,
                    "error": "contract_response_parse_failed",
                    "data": result,
                }
            return {
                "success": True,
                "post_id": post_id,
                "post_url": post_url,
                "slug": post_slug,
                "status": post_status,
                "data": result,
                "request_json": None,
            }

        post_id = result.get("id") if isinstance(result, dict) else None
        if post_id is None:
            post_id = fallback_post_id
        post_url = (
            (result.get("url") if isinstance(result, dict) else None)
            or (result.get("link") if isinstance(result, dict) else None)
            or fallback_url
            or ""
        )
        post_slug = (result.get("slug") if isinstance(result, dict) else None) or fallback_slug or ""
        post_status = (result.get("status") if isinstance(result, dict) else None) or fallback_status or ""
        return {
            "success": True,
            "post_id": post_id,
            "post_url": post_url,
            "slug": post_slug,
            "status": post_status,
            "data": result,
        }

    @staticmethod
    def extract_post_id(post: Dict[str, Any]) -> Optional[int]:
        if not isinstance(post, dict):
            return None
        raw_id = post.get("id") or post.get("post_id") or post.get("article_id")
        if raw_id is None and isinstance(post.get("attributes"), dict):
            raw_id = post["attributes"].get("id")
        if raw_id is None:
            return None
        try:
            return int(raw_id)
        except (TypeError, ValueError):
            return None

    def _join(self, *parts: str) -> str:
        base = self.base_url.rstrip("/") + "/"
        rel = "/".join([p.strip("/") for p in parts if p is not None and str(p).strip("/") != ""])
        return urljoin(base, rel)

    def _posts_url(self, post_id: Optional[Any] = None) -> str:
        if self.provider == "wordpress":
            base = self.base_url
            return self._join("posts", str(post_id)) if post_id else self._join("posts")
        if self.provider == "ghost":
            base = self.base_url
            return self._join("posts", str(post_id), "") if post_id else self._join("posts", "")
        if self.provider == "strapi":
            if "/api" in self.base_url:
                return self._join("posts", str(post_id)) if post_id else self._join("posts")
            return self._join("api", "posts", str(post_id)) if post_id else self._join("api", "posts")

        if post_id:
            return self._join(self.api_version, "posts", str(post_id)) if self.api_version else self._join("posts", str(post_id))
        return self._join(self.api_version, "posts") if self.api_version else self._join("posts")

    def _categories_url(self) -> str:
        if self.provider == "wordpress":
            return self._join("categories")
        if self.provider == "strapi":
            if "/api" in self.base_url:
                return self._join("categories")
            return self._join("api", "categories")
        return self._join(self.api_version, "categories") if self.api_version else self._join("categories")

    def _tags_url(self) -> str:
        if self.provider == "wordpress":
            return self._join("tags")
        if self.provider == "strapi":
            if "/api" in self.base_url:
                return self._join("tags")
            return self._join("api", "tags")
        return self._join(self.api_version, "tags") if self.api_version else self._join("tags")

    def _build_custom_post_payload(
        self,
        *,
        title: str,
        content: str,
        slug: str,
        status: str,
        categories: Optional[Any],
        tags: Optional[Any],
        featured_image: Optional[Any],
        meta_title: Optional[str],
        meta_description: Optional[str],
        publish_date: Optional[str],
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        pc = self._get_custom_post_contract()
        content_field = (pc.get("content_field") if isinstance(pc, dict) else None) or "content_html"
        md_field = (pc.get("preserve_markdown_field") if isinstance(pc, dict) else None) or "content_md"
        meta_field = (pc.get("meta_field") if isinstance(pc, dict) else None) or "meta"

        content_html = kwargs.get(content_field) or kwargs.get("content_html") or content
        content_md = kwargs.get(md_field) or kwargs.get("content_md") or ""

        status_val = self._map_custom_status(status)
        meta: Dict[str, Any] = {}
        if meta_title:
            meta["seo_title"] = meta_title
        if meta_description:
            meta["seo_description"] = meta_description
        if kwargs.get("focus_keyword"):
            meta["focus_keyword"] = kwargs["focus_keyword"]
        if kwargs.get("schema_json") is not None:
            meta["schema_json"] = kwargs["schema_json"]

        post_data: Dict[str, Any] = {
            "title": title,
            content_field: content_html,
            md_field: content_md,
            "excerpt": kwargs.get("excerpt") or meta_description or "",
            "slug": slug,
            "category": categories,
            "tags": tags or [],
            "featured_image": featured_image,
            meta_field: meta,
            "status": status_val,
            "publish_date": publish_date,
            "topic_id": kwargs.get("topic_id"),
        }
        return {k: v for k, v in post_data.items() if v is not None}
    
    async def authenticate(self) -> Dict[str, Any]:
        url = self._join("auth", "login")
        
        data = {
            "username": self.username,
            "password": self.password
        }
        
        try:
            response = await self.http_client.post(url, json=data)
            response.raise_for_status()
            result = response.json()
            
            self.token = result.get("token")
            
            return {
                "success": True,
                "token": self.token,
                "user": result.get("user", {})
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def authenticate_if_needed(self) -> Dict[str, Any]:
        if self.provider != "custom":
            return {"success": True, "skipped": True}
        if self.token or self.api_key:
            return {"success": True, "skipped": True}
        if not self.username or not self.password:
            return {"success": False, "error": "missing_username_or_password"}
        return await self.authenticate()
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {"Content-Type": "application/json"}

        if self.provider == "wordpress":
            if self.username and self.password:
                token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
                headers["Authorization"] = f"Basic {token}"
            return headers

        if self.provider == "strapi":
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            return headers

        if self.provider == "ghost":
            jwt_token = os.environ.get("GHOST_ADMIN_API_JWT") or ""
            if jwt_token:
                headers["Authorization"] = f"Ghost {jwt_token}"
            return headers

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key

        return headers
    
    async def create_post(
        self,
        title: str,
        content: str,
        slug: Optional[str] = None,
        status: str = "draft",
        categories: Optional[Any] = None,
        tags: Optional[Any] = None,
        featured_image: Optional[Any] = None,
        meta_title: Optional[str] = None,
        meta_description: Optional[str] = None,
        publish_date: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        创建文章
        
        Args:
            title: 标题
            content: 内容
            slug: URL别名
            status: 状态 draft/publish/scheduled
            categories: 分类ID列表
            tags: 标签ID列表
            featured_image: 特色图片URL
            meta_title: SEO标题
            meta_description: SEO描述
            publish_date: 定时发布时间
            
        Returns:
            创建结果
        """
        # 生成slug
        if not slug:
            slug = self._generate_slug(title)
        
        if self.provider == "ghost":
            post = {
                "title": title,
                "html": content,
                "slug": slug,
                "status": status,
            }
            if featured_image:
                post["feature_image"] = featured_image
            if meta_title:
                post["meta_title"] = meta_title
            if meta_description:
                post["meta_description"] = meta_description
            post_data: Any = {"posts": [post]}
            url = self._posts_url()
        elif self.provider == "strapi":
            post = {
                "title": title,
                "content": content,
                "slug": slug,
                "status": status,
            }
            if categories is not None:
                post["categories"] = categories
            if tags is not None:
                post["tags"] = tags
            if featured_image is not None:
                post["featured_image"] = featured_image
            if meta_title:
                post["meta_title"] = meta_title
            if meta_description:
                post["meta_description"] = meta_description
            post_data = {"data": post}
            url = self._posts_url()
        elif self.provider == "wordpress":
            post_data = {
                "title": title,
                "content": content,
                "slug": slug,
                "status": status,
            }
            if categories is not None:
                post_data["categories"] = categories
            if tags is not None:
                post_data["tags"] = tags
            if featured_image is not None:
                post_data["featured_media"] = featured_image
            wp_meta = self._build_wordpress_meta(
                meta_title=meta_title,
                meta_description=meta_description,
                focus_keyword=kwargs.get("focus_keyword") or kwargs.get("primary_keyword"),
            )
            if isinstance(wp_meta, dict):
                post_data["meta"] = wp_meta
            url = self._posts_url()
        else:
            post_data = self._build_custom_post_payload(
                title=title,
                content=content,
                slug=slug,
                status=status,
                categories=categories,
                tags=tags,
                featured_image=featured_image,
                meta_title=meta_title,
                meta_description=meta_description,
                publish_date=publish_date,
                kwargs=kwargs,
            )
            url = self._custom_url(self._custom_request_path("create_post_path", "/posts"))
        
        try:
            response = await self.http_client.post(
                url,
                json=post_data,
                headers=self._get_headers()
            )
            response.raise_for_status()
            result = response.json()

            normalized = self._normalize_post_response(
                result,
                fallback_status=status,
                fallback_slug=slug or "",
            )
            if normalized.get("success") and self.provider == "custom":
                normalized["request_json"] = (
                    post_data if (os.environ.get("CMS_CONTRACT_DEBUG") or "").lower() in {"1", "true", "yes"} else None
                )
            return normalized
        except httpx.HTTPStatusError as e:
            err_msg = None
            if self.provider == "custom":
                try:
                    pc = self._get_custom_post_contract()
                    error_paths = (pc.get("error_paths") if isinstance(pc, dict) else None) or []
                    j = e.response.json()
                    err_msg = self._extract_by_paths(j, error_paths)
                except Exception:
                    err_msg = None
            return {
                "success": False,
                "error": f"HTTP错误: {e.response.status_code}",
                "details": err_msg or e.response.text,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def update_post(
        self,
        post_id: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        更新文章
        
        Args:
            post_id: 文章ID
            **kwargs: 要更新的字段
            
        Returns:
            更新结果
        """
        if self.provider == "custom":
            base_path = self._custom_request_path("create_post_path", "/posts").rstrip("/")
            url = self._custom_url(f"{base_path}/{post_id}")
        else:
            url = self._posts_url(post_id)
        body: Any = kwargs
        if self.provider == "strapi":
            body = {"data": kwargs}
        elif self.provider == "ghost":
            body = {"posts": [kwargs]}
        elif self.provider == "wordpress":
            body = {k: v for k, v in kwargs.items() if v is not None and k not in {"meta_title", "meta_description", "focus_keyword", "primary_keyword"}}
            wp_meta = self._build_wordpress_meta(
                meta_title=kwargs.get("meta_title"),
                meta_description=kwargs.get("meta_description"),
                focus_keyword=kwargs.get("focus_keyword") or kwargs.get("primary_keyword"),
            )
            if isinstance(wp_meta, dict):
                body["meta"] = wp_meta
        elif self.provider == "custom":
            full_update_keys = {"title", "content", "content_html", "content_md", "slug"}
            if any(kwargs.get(k) not in (None, "") for k in full_update_keys):
                body = self._build_custom_post_payload(
                    title=kwargs.get("title") or "",
                    content=kwargs.get("content") or "",
                    slug=kwargs.get("slug") or "",
                    status=kwargs.get("status") or "draft",
                    categories=kwargs.get("categories"),
                    tags=kwargs.get("tags"),
                    featured_image=kwargs.get("featured_image"),
                    meta_title=kwargs.get("meta_title"),
                    meta_description=kwargs.get("meta_description"),
                    publish_date=kwargs.get("publish_date"),
                    kwargs=kwargs,
                )
            else:
                body = {k: v for k, v in kwargs.items() if v is not None}
                if "status" in body:
                    body["status"] = self._map_custom_status(str(body["status"]))
        
        try:
            response = await self.http_client.patch(
                url,
                json=body,
                headers=self._get_headers()
            )
            response.raise_for_status()

            result = response.json()
            normalized = self._normalize_post_response(
                result,
                fallback_post_id=post_id,
                fallback_status=str(kwargs.get("status") or ""),
                fallback_slug=str(kwargs.get("slug") or ""),
            )
            if not normalized.get("success"):
                return normalized
            normalized["updated"] = True
            if normalized.get("post_url"):
                return normalized

            post_detail = await self.get_post(post_id)
            if post_detail.get("success") and isinstance(post_detail.get("post"), dict):
                hydrated = self._normalize_post_response(
                    post_detail["post"],
                    fallback_post_id=post_id,
                    fallback_status=normalized.get("status") or str(kwargs.get("status") or ""),
                    fallback_slug=normalized.get("slug") or str(kwargs.get("slug") or ""),
                )
                if hydrated.get("success"):
                    hydrated["updated"] = True
                    return hydrated

            return normalized
        except httpx.HTTPStatusError as e:
            err_msg = None
            if self.provider == "custom":
                try:
                    pc = self._get_custom_post_contract()
                    error_paths = (pc.get("error_paths") if isinstance(pc, dict) else None) or []
                    j = e.response.json()
                    err_msg = self._extract_by_paths(j, error_paths)
                except Exception:
                    err_msg = None
            return {
                "success": False,
                "error": f"HTTP错误: {e.response.status_code}",
                "details": err_msg or e.response.text,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def publish_post(self, post_id: int) -> Dict[str, Any]:
        """
        发布文章
        
        Args:
            post_id: 文章ID
            
        Returns:
            发布结果
        """
        return await self.update_post(post_id, status="publish")
    
    async def delete_post(self, post_id: int) -> Dict[str, Any]:
        """
        删除文章
        
        Args:
            post_id: 文章ID
            
        Returns:
            删除结果
        """
        if self.provider == "custom":
            base_path = self._custom_request_path("create_post_path", "/posts").rstrip("/")
            url = self._custom_url(f"{base_path}/{post_id}")
        else:
            url = self._posts_url(post_id)
        
        try:
            response = await self.http_client.delete(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            
            return {
                "success": True,
                "deleted": True
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_post(self, post_id: int) -> Dict[str, Any]:
        """
        获取文章详情
        
        Args:
            post_id: 文章ID
            
        Returns:
            文章详情
        """
        if self.provider == "custom":
            base_path = self._custom_request_path("create_post_path", "/posts").rstrip("/")
            url = self._custom_url(f"{base_path}/{post_id}")
        else:
            url = self._posts_url(post_id)
        
        try:
            response = await self.http_client.get(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            
            return {
                "success": True,
                "post": response.json()
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_categories(self) -> List[Dict]:
        """获取分类列表"""
        url = self._categories_url()
        
        try:
            response = await self.http_client.get(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            
            return response.json()
        except Exception:
            return []
    
    async def get_tags(self) -> List[Dict]:
        """获取标签列表"""
        url = self._tags_url()
        
        try:
            response = await self.http_client.get(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            
            return response.json()
        except Exception:
            return []

    async def find_posts_by_slug_result(self, slug: str) -> Dict[str, Any]:
        if not slug:
            return {"success": True, "items": []}
        if self.provider == "custom":
            url = self._custom_url(self._custom_request_path("create_post_path", "/posts"))
        else:
            url = self._posts_url()
        params: Dict[str, Any] = {}
        if self.provider in {"custom", "wordpress"}:
            if self.provider == "custom":
                pc = self._get_custom_post_contract()
                req = pc.get("request") if isinstance(pc, dict) else None
                qp = (req.get("slug_query_param") if isinstance(req, dict) else None) or "slug"
                params[qp] = slug
            else:
                params["slug"] = slug
        elif self.provider == "strapi":
            params["filters[slug][$eq]"] = slug
        else:
            return {"success": True, "items": []}
        try:
            response = await self.http_client.get(url, params=params, headers=self._get_headers())
            response.raise_for_status()
            data = response.json()
            if self.provider == "strapi" and isinstance(data, dict):
                items = data.get("data") or []
                out = []
                for item in items:
                    if isinstance(item, dict):
                        out.append(item)
                return {"success": True, "items": out}
            if isinstance(data, list):
                return {"success": True, "items": [d for d in data if isinstance(d, dict)]}
            if isinstance(data, dict):
                for key in ("items", "results", "data", "posts", "articles"):
                    value = data.get(key)
                    if isinstance(value, list):
                        return {"success": True, "items": [d for d in value if isinstance(d, dict)]}
                return {"success": True, "items": [data]}
            return {"success": True, "items": []}
        except httpx.HTTPStatusError as e:
            details = None
            try:
                details = e.response.text
            except Exception:
                details = None
            return {
                "success": False,
                "error": f"HTTP错误: {e.response.status_code}",
                "details": details,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    async def find_posts_by_slug(self, slug: str) -> List[Dict[str, Any]]:
        result = await self.find_posts_by_slug_result(slug)
        if not result.get("success"):
            return []
        items = result.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    async def resolve_wordpress_category_id(self, category: str) -> Optional[int]:
        if self.provider != "wordpress" or not category:
            return None
        items = await self.get_categories()
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("slug") == category or item.get("name") == category:
                cid = item.get("id")
                return int(cid) if cid is not None else None
        return None

    async def resolve_wordpress_tag_id(self, tag: str) -> Optional[int]:
        if self.provider != "wordpress" or not tag:
            return None
        items = await self.get_tags()
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("slug") == tag or item.get("name") == tag:
                tid = item.get("id")
                return int(tid) if tid is not None else None
        return None

    async def create_wordpress_tag(self, tag: str) -> Optional[int]:
        if self.provider != "wordpress" or not tag:
            return None
        url = self._tags_url()
        try:
            response = await self.http_client.post(url, json={"name": tag}, headers=self._get_headers())
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("id") is not None:
                return int(data["id"])
            return None
        except Exception:
            return None

    async def prepare_payload_for_provider(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.provider != "wordpress":
            return payload
        out = dict(payload)
        if isinstance(out.get("category"), str):
            cid = await self.resolve_wordpress_category_id(out["category"])
            out["category"] = [cid] if cid is not None else []
        if isinstance(out.get("tags"), list):
            tag_ids = []
            for tag in out["tags"]:
                tid = await self.resolve_wordpress_tag_id(tag)
                if tid is None:
                    tid = await self.create_wordpress_tag(tag)
                if tid is not None:
                    tag_ids.append(tid)
            out["tags"] = tag_ids
        return out

    async def slug_exists_result(self, slug: str) -> Dict[str, Any]:
        result = await self.find_posts_by_slug_result(slug)
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error") or "slug_lookup_failed",
                "details": result.get("details"),
            }
        return {
            "success": True,
            "exists": bool(result.get("items") or []),
        }

    async def slug_exists(self, slug: str) -> bool:
        result = await self.slug_exists_result(slug)
        return bool(result.get("success")) and bool(result.get("exists"))
    
    def _generate_slug(self, title: str) -> str:
        """生成URL别名"""
        import re
        
        # 转小写
        slug = title.lower()
        
        # 替换中文字符为拼音
        # 简化处理：直接用汉字Unicode编码
        def to_pinyin(match):
            # 这里可以接入拼音库
            return match.group()
        
        slug = re.sub(r'[\u4e00-\u9fff]', to_pinyin, slug)
        
        # 移除非字母数字字符
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        
        # 移除首尾横线
        slug = slug.strip('-')
        
        return slug
    
    async def close(self):
        """关闭客户端"""
        await self.http_client.aclose()


# CrewAI Tool 包装
def get_cms_client_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("cms_client")
    def cms_client_tool(
        action: str,
        title: str = "",
        content: str = "",
        post_id: int = 0,
        slug: str = "",
        status: str = "draft",
        meta_title: str = "",
        meta_description: str = ""
    ) -> str:
        """
        创建、更新、发布CMS文章。
        
        Args:
            action: 操作类型 create/update/publish/delete/get
            title: 文章标题（create时必填）
            content: 文章内容（create时必填）
            post_id: 文章ID（update/publish/delete/get时必填）
            slug: URL别名（可选）
            status: 状态 draft/publish/scheduled（create时使用）
            meta_title: SEO标题（可选）
            meta_description: SEO描述（可选）
            
        Returns:
            JSON格式的操作结果
        """
        import asyncio
        
        client = CMSClient()
        
        async def run():
            auth_result = await client.authenticate_if_needed()
            if not auth_result.get("success", True):
                return auth_result
            
            # 执行操作
            if action == "create":
                return await client.create_post(
                    title=title,
                    content=content,
                    slug=slug or None,
                    status=status,
                    meta_title=meta_title or None,
                    meta_description=meta_description or None
                )
            elif action == "update":
                return await client.update_post(
                    post_id,
                    title=title or None,
                    content=content or None,
                    meta_title=meta_title or None,
                    meta_description=meta_description or None
                )
            elif action == "publish":
                return await client.publish_post(post_id)
            elif action == "delete":
                return await client.delete_post(post_id)
            elif action == "get":
                return await client.get_post(post_id)
            else:
                return {"success": False, "error": f"未知操作: {action}"}
        
        try:
            result = asyncio.run(run())
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            asyncio.run(client.close())
    
    return cms_client_tool


if __name__ == "__main__":
    # 测试
    import asyncio
    
    async def test():
        client = CMSClient(
            base_url="http://localhost:8080/api",
            username="admin",
            password="password"
        )
        
        # 测试认证
        auth = await client.authenticate()
        print("Auth:", json.dumps(auth, ensure_ascii=False, indent=2))
        
        # 如果认证成功，创建文章
        if auth.get("success"):
            result = await client.create_post(
                title="测试文章",
                content="这是一篇测试文章的内容",
                status="draft"
            )
            print("Create:", json.dumps(result, ensure_ascii=False, indent=2))
        
        await client.close()
    
    asyncio.run(test())
