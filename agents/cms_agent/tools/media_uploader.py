#!/usr/bin/env python3
"""
CMS 媒体适配器。
负责上传图片、视频等媒体，并处理 provider 差异与返回值归一化。
"""

import os
import json
import httpx
import base64
import hashlib
import hmac
import mimetypes
import time
import uuid
from typing import Dict, List, Any, Optional, Union
from pathlib import Path
import io


class MediaUploader:
    """媒体上传工具"""
    
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
        self.provider = (provider or "custom").strip().lower()
        self.api_version = (api_version or os.environ.get("CMS_API_VERSION") or "").strip().lstrip("/")

        env_base_url = os.environ.get("CMS_API_URL") or os.environ.get("CMS_BASE_URL") or "http://localhost:8080/api"
        self.base_url = (base_url or env_base_url).rstrip("/")

        self.api_key = api_key or os.environ.get("CMS_API_KEY", "")
        self.username = username or os.environ.get("CMS_USERNAME", "")
        self.password = password or os.environ.get("CMS_PASSWORD", "")
        self.token = None
        self.http_client = httpx.AsyncClient(timeout=120.0)  # 大文件需要更长超时
        self.contract = contract or {}
        self.bff_secret = os.environ.get("BFF_API_SECRET", "")

    def _get_custom_post_contract(self) -> Dict[str, Any]:
        cms = self.contract.get("cms") if isinstance(self.contract, dict) else None
        custom = (cms or {}).get("custom") if isinstance(cms, dict) else None
        post_contract = (custom or {}).get("post_contract") if isinstance(custom, dict) else None
        return post_contract or {}

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
    def _extract_by_paths(cls, data: Any, paths: List[str]) -> Any:
        for p in paths or []:
            v = cls._extract_by_path(data, p)
            if v is not None:
                return v
        return None

    def _join(self, *parts: str) -> str:
        base = self.base_url.rstrip("/") + "/"
        rel = "/".join([p.strip("/") for p in parts if p is not None and str(p).strip("/") != ""])
        return base + rel

    def _media_url(self, media_id: Optional[Union[str, int]] = None) -> str:
        if self.provider == "wordpress":
            return self._join("media", str(media_id)) if media_id else self._join("media")
        if self.provider == "ghost":
            return self._join("images", "upload", "")
        if self.provider == "strapi":
            if "/api" in self.base_url:
                return self._join("upload")
            return self._join("api", "upload")
        if self.provider == "custom":
            pc = self._get_custom_post_contract()
            req = pc.get("request") if isinstance(pc, dict) else None
            base_path = (req.get("media_upload_path") if isinstance(req, dict) else None) or "/media"
            base_path = str(base_path).strip("/")
            if media_id:
                return self._join(self.api_version, base_path, str(media_id)) if self.api_version else self._join(base_path, str(media_id))
            return self._join(self.api_version, base_path) if self.api_version else self._join(base_path)
        if media_id:
            return self._join(self.api_version, "media", str(media_id)) if self.api_version else self._join("media", str(media_id))
        return self._join(self.api_version, "media") if self.api_version else self._join("media")

    def _bff_headers(self, method: str, path: str, body: str = "", *, content_type: Optional[str] = "application/json") -> Dict[str, str]:
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        clean_path = path.strip("/")
        canonical = f"{method.upper()}\n/{clean_path}\n\n{body}\n{ts}\n{nonce}"
        signature = hmac.new(
            self.bff_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "x-timestamp": ts,
            "x-nonce": nonce,
            "x-signature": signature,
            "x-signature-method": "HMAC-SHA256",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _get_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
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
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers
    
    async def upload_file(
        self,
        file_path: Optional[str] = None,
        file_url: Optional[str] = None,
        file_data: Optional[str] = None,  # Base64编码
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        alt_text: Optional[str] = None,
        title: Optional[str] = None,
        caption: Optional[str] = None,
        optimization: Optional[Dict[str, Any]] = None,
        requirements: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        上传媒体文件
        
        Args:
            file_path: 本地文件路径
            file_url: 远程文件URL（将从URL下载）
            file_data: Base64编码的文件数据
            file_name: 文件名
            mime_type: MIME类型
            alt_text: Alt文本
            title: 标题
            caption: 说明
            
        Returns:
            上传结果
        """
        # 获取文件数据
        if file_path:
            return await self._upload_local_file(file_path, alt_text, title, caption, optimization, requirements)
        elif file_url:
            return await self._upload_from_url(file_url, alt_text, title, caption, optimization, requirements)
        elif file_data:
            return await self._upload_base64(file_data, file_name, mime_type, alt_text, title, caption, optimization, requirements)
        else:
            return {
                "success": False,
                "error": "必须提供 file_path、file_url 或 file_data 之一"
            }
    
    async def _upload_local_file(
        self,
        file_path: str,
        alt_text: Optional[str] = None,
        title: Optional[str] = None,
        caption: Optional[str] = None,
        optimization: Optional[Dict[str, Any]] = None,
        requirements: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """从本地文件上传"""
        path = Path(file_path)
        
        if not path.exists():
            return {
                "success": False,
                "error": f"文件不存在: {file_path}"
            }
        
        # 读取文件
        with open(path, "rb") as f:
            file_data = f.read()
        
        # 获取MIME类型
        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

        processed = self._process_image_bytes(
            file_bytes=file_data,
            file_name=path.name,
            mime_type=mime_type,
            optimization=optimization,
            requirements=requirements,
        )
        if not processed.get("success", True):
            return processed
        file_data = processed.get("file_bytes") or file_data
        file_name = processed.get("file_name") or path.name
        mime_type = processed.get("mime_type") or mime_type
        warnings = processed.get("warnings") or []
        
        # 上传
        out = await self._do_upload(
            file_data=file_data,
            file_name=file_name,
            mime_type=mime_type,
            alt_text=alt_text,
            title=title or path.stem,
            caption=caption
        )
        if warnings:
            out["warnings"] = warnings
        return out
    
    async def _upload_from_url(
        self,
        url: str,
        alt_text: Optional[str] = None,
        title: Optional[str] = None,
        caption: Optional[str] = None,
        optimization: Optional[Dict[str, Any]] = None,
        requirements: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """从URL下载后上传"""
        if self.provider == "custom" and self.bff_secret:
            try:
                path = str(((self._get_custom_post_contract().get("request") or {}).get("media_upload_path")) or "/media").strip("/")
                body = json.dumps({"url": url}, ensure_ascii=False, separators=(",", ":"))
                response = await self.http_client.post(
                    self._media_url(),
                    content=body.encode("utf-8"),
                    headers=self._bff_headers("POST", path, body),
                )
                response.raise_for_status()
                result = response.json()
                media_url = self._extract_by_paths(result, ["data.url", "url"]) or ""
                if not media_url:
                    # Some BFF/PHP deployments fail to parse JSON URL-transfer
                    # payloads while multipart file upload still works. Fall
                    # back to downloading the remote image here and upload it
                    # through the same multipart path that local generated
                    # covers use.
                    downloaded = await self.http_client.get(url)
                    downloaded.raise_for_status()
                    file_name = url.split("/")[-1].split("?")[0] or "cover.jpg"
                    mime_type = (
                        downloaded.headers.get("content-type", "").split(";")[0].strip()
                        or mimetypes.guess_type(file_name)[0]
                        or "image/jpeg"
                    )
                    return await self._do_upload(
                        file_data=downloaded.content,
                        file_name=file_name,
                        mime_type=mime_type,
                        alt_text=alt_text,
                        title=title,
                        caption=caption,
                    )
                return {
                    "success": True,
                    "media_id": None,
                    "url": media_url,
                    "thumbnail_url": media_url,
                    "file_name": url.split("/")[-1].split("?")[0],
                    "file_type": "",
                    "file_size": 0,
                    "alt_text": alt_text,
                    "title": title,
                    "data": result,
                }
            except httpx.HTTPStatusError as e:
                return {"success": False, "error": f"上传失败: {e.response.status_code}", "details": e.response.text}
            except Exception as e:
                return {"success": False, "error": str(e)}
        try:
            # 下载文件
            response = await self.http_client.get(url)
            response.raise_for_status()
            file_data = response.content
            
            # 获取文件名和MIME类型
            content_disposition = response.headers.get("content-disposition", "")
            if "filename=" in content_disposition:
                import re
                match = re.search(r'filename="?([^";]+)"?', content_disposition)
                file_name = match.group(1) if match else "downloaded_file"
            else:
                file_name = url.split("/")[-1].split("?")[0]
            
            mime_type = response.headers.get("content-type", "application/octet-stream")

            processed = self._process_image_bytes(
                file_bytes=file_data,
                file_name=file_name,
                mime_type=mime_type,
                optimization=optimization,
                requirements=requirements,
            )
            if not processed.get("success", True):
                return processed
            file_data = processed.get("file_bytes") or file_data
            file_name = processed.get("file_name") or file_name
            mime_type = processed.get("mime_type") or mime_type
            warnings = processed.get("warnings") or []
            
            # 上传
            out = await self._do_upload(
                file_data=file_data,
                file_name=file_name,
                mime_type=mime_type,
                alt_text=alt_text,
                title=title,
                caption=caption
            )
            if warnings:
                out["warnings"] = warnings
            return out
            
        except Exception as e:
            return {
                "success": False,
                "error": f"下载失败: {str(e)}"
            }
    
    async def _upload_base64(
        self,
        file_data: str,
        file_name: str,
        mime_type: Optional[str] = None,
        alt_text: Optional[str] = None,
        title: Optional[str] = None,
        caption: Optional[str] = None,
        optimization: Optional[Dict[str, Any]] = None,
        requirements: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """上传Base64编码的文件"""
        if self.provider == "custom" and self.bff_secret:
            try:
                path = str(((self._get_custom_post_contract().get("request") or {}).get("media_upload_path")) or "/media").strip("/")
                payload_data = file_data if file_data.startswith("data:") else f"data:{mime_type or 'image/png'};base64,{file_data}"
                body = json.dumps({"base64": payload_data}, ensure_ascii=False, separators=(",", ":"))
                response = await self.http_client.post(
                    self._media_url(),
                    content=body.encode("utf-8"),
                    headers=self._bff_headers("POST", path, body),
                )
                response.raise_for_status()
                result = response.json()
                media_url = self._extract_by_paths(result, ["data.url", "url"]) or ""
                if not media_url:
                    return {"success": False, "error": "contract_response_parse_failed", "data": result}
                return {
                    "success": True,
                    "media_id": None,
                    "url": media_url,
                    "thumbnail_url": media_url,
                    "file_name": file_name,
                    "file_type": mime_type or "",
                    "file_size": 0,
                    "alt_text": alt_text,
                    "title": title,
                    "data": result,
                }
            except httpx.HTTPStatusError as e:
                return {"success": False, "error": f"上传失败: {e.response.status_code}", "details": e.response.text}
            except Exception as e:
                return {"success": False, "error": str(e)}
        try:
            # 解码
            data = base64.b64decode(file_data)
            
            # 获取MIME类型
            if not mime_type:
                mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

            processed = self._process_image_bytes(
                file_bytes=data,
                file_name=file_name,
                mime_type=mime_type,
                optimization=optimization,
                requirements=requirements,
            )
            if not processed.get("success", True):
                return processed
            data = processed.get("file_bytes") or data
            file_name = processed.get("file_name") or file_name
            mime_type = processed.get("mime_type") or mime_type
            warnings = processed.get("warnings") or []
            
            # 上传
            out = await self._do_upload(
                file_data=data,
                file_name=file_name,
                mime_type=mime_type,
                alt_text=alt_text,
                title=title,
                caption=caption
            )
            if warnings:
                out["warnings"] = warnings
            return out
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Base64解码失败: {str(e)}"
            }
    
    async def _do_upload(
        self,
        file_data: bytes,
        file_name: str,
        mime_type: str,
        alt_text: Optional[str],
        title: Optional[str],
        caption: Optional[str]
    ) -> Dict[str, Any]:
        """执行上传"""
        url = self._media_url()
        
        # 构建表单数据
        if self.provider == "strapi":
            files = {"files": (file_name, file_data, mime_type)}
        else:
            files = {"file": (file_name, file_data, mime_type)}
        
        data = {}
        if alt_text:
            data["alt_text"] = alt_text
        if title:
            data["title"] = title
        if caption:
            data["caption"] = caption
        
        headers = self._get_headers()
        if self.provider == "custom" and self.bff_secret:
            path = str(((self._get_custom_post_contract().get("request") or {}).get("media_upload_path")) or "/media").strip("/")
            headers = self._bff_headers("POST", path, "", content_type=None)
        
        try:
            response = await self.http_client.post(
                url,
                files=files,
                data=data,
                headers=headers
            )
            response.raise_for_status()
            result = response.json()

            if self.provider == "wordpress" and isinstance(result, dict):
                return {
                    "success": True,
                    "media_id": result.get("id"),
                    "url": result.get("source_url", ""),
                    "thumbnail_url": result.get("source_url", ""),
                    "file_name": file_name,
                    "file_type": mime_type,
                    "file_size": len(file_data),
                    "alt_text": alt_text,
                    "title": title,
                }

            if self.provider == "ghost" and isinstance(result, dict):
                images = result.get("images") or []
                img0 = images[0] if isinstance(images, list) and images else {}
                return {
                    "success": True,
                    "media_id": img0.get("ref") or img0.get("id"),
                    "url": img0.get("url", ""),
                    "thumbnail_url": img0.get("url", ""),
                    "file_name": file_name,
                    "file_type": mime_type,
                    "file_size": len(file_data),
                    "alt_text": alt_text,
                    "title": title,
                }

            if self.provider == "strapi" and isinstance(result, list) and result:
                item = result[0] if isinstance(result[0], dict) else {}
                return {
                    "success": True,
                    "media_id": item.get("id"),
                    "url": item.get("url", ""),
                    "thumbnail_url": (item.get("formats") or {}).get("thumbnail", {}).get("url") if isinstance(item, dict) else "",
                    "file_name": item.get("name", file_name),
                    "file_type": item.get("mime", mime_type),
                    "file_size": item.get("size", len(file_data)),
                    "alt_text": alt_text,
                    "title": title,
                }

            if isinstance(result, dict):
                if self.provider == "custom":
                    pc = self._get_custom_post_contract()
                    mrp = pc.get("media_response_paths") if isinstance(pc, dict) else None
                    id_paths = (mrp.get("id") if isinstance(mrp, dict) else None) or ["id"]
                    url_paths = (mrp.get("url") if isinstance(mrp, dict) else None) or ["url"]
                    media_id = self._extract_by_paths(result, id_paths)
                    media_url = self._extract_by_paths(result, url_paths) or ""
                    if media_id is None and not media_url:
                        return {"success": False, "error": "contract_response_parse_failed", "data": result}
                    return {
                        "success": True,
                        "media_id": media_id,
                        "url": media_url,
                        "thumbnail_url": media_url,
                        "file_name": file_name,
                        "file_type": mime_type,
                        "file_size": len(file_data),
                        "alt_text": alt_text,
                        "title": title,
                        "data": result,
                        "request_files": file_name if (os.environ.get("CMS_CONTRACT_DEBUG") or "").lower() in {"1", "true", "yes"} else None,
                    }
                return {
                    "success": True,
                    "media_id": result.get("id"),
                    "url": result.get("url", ""),
                    "thumbnail_url": result.get("thumbnail_url", result.get("url", "")),
                    "file_name": result.get("filename", file_name),
                    "file_type": result.get("mime_type", mime_type),
                    "file_size": result.get("size", len(file_data)),
                    "alt_text": result.get("alt_text", alt_text),
                    "title": result.get("title", title),
                }

            return {"success": True, "data": result}
            
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "error": f"上传失败: {e.response.status_code}",
                "details": e.response.text
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _process_image_bytes(
        self,
        *,
        file_bytes: bytes,
        file_name: str,
        mime_type: str,
        optimization: Optional[Dict[str, Any]],
        requirements: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not mime_type or not mime_type.startswith("image/"):
            return {"success": True, "file_bytes": file_bytes, "file_name": file_name, "mime_type": mime_type, "warnings": []}

        warnings: List[str] = []
        try:
            from PIL import Image
        except Exception:
            warnings.append("image_processing_unavailable")
            return {"success": True, "file_bytes": file_bytes, "file_name": file_name, "mime_type": mime_type, "warnings": warnings}

        try:
            img = Image.open(io.BytesIO(file_bytes))
            img.load()
        except Exception:
            warnings.append("image_decode_failed")
            return {"success": True, "file_bytes": file_bytes, "file_name": file_name, "mime_type": mime_type, "warnings": warnings}

        if requirements:
            min_w = int(requirements.get("min_width") or 0)
            min_h = int(requirements.get("min_height") or 0)
            if min_w and img.width < min_w:
                return {"success": False, "error": "featured_image_min_width", "width": img.width, "height": img.height}
            if min_h and img.height < min_h:
                return {"success": False, "error": "featured_image_min_height", "width": img.width, "height": img.height}

        out_img = img
        if optimization:
            max_w = int(optimization.get("max_width") or 0)
            max_h = int(optimization.get("max_height") or 0)
            if max_w or max_h:
                bound_w = max_w or out_img.width
                bound_h = max_h or out_img.height
                if out_img.width > bound_w or out_img.height > bound_h:
                    out_img.thumbnail((bound_w, bound_h))

        fmt = (optimization or {}).get("format") if optimization else None
        quality = int((optimization or {}).get("quality") or 85) if optimization else 85
        target_format = (fmt or "").strip().lower()
        if target_format in {"webp", "jpg", "jpeg", "png"}:
            buf = io.BytesIO()
            save_kwargs: Dict[str, Any] = {}
            if target_format in {"jpg", "jpeg"}:
                save_fmt = "JPEG"
                save_kwargs["quality"] = quality
                if out_img.mode in {"RGBA", "P"}:
                    out_img = out_img.convert("RGB")
                new_name = os.path.splitext(file_name)[0] + ".jpg"
                new_mime = "image/jpeg"
            elif target_format == "png":
                save_fmt = "PNG"
                new_name = os.path.splitext(file_name)[0] + ".png"
                new_mime = "image/png"
            else:
                save_fmt = "WEBP"
                save_kwargs["quality"] = quality
                new_name = os.path.splitext(file_name)[0] + ".webp"
                new_mime = "image/webp"

            try:
                out_img.save(buf, format=save_fmt, **save_kwargs)
                return {
                    "success": True,
                    "file_bytes": buf.getvalue(),
                    "file_name": new_name,
                    "mime_type": new_mime,
                    "warnings": warnings,
                }
            except Exception:
                warnings.append("image_encode_failed")
                return {"success": True, "file_bytes": file_bytes, "file_name": file_name, "mime_type": mime_type, "warnings": warnings}

        return {"success": True, "file_bytes": file_bytes, "file_name": file_name, "mime_type": mime_type, "warnings": warnings}
    
    async def upload_multiple(
        self,
        file_paths: List[str],
        alt_texts: Optional[List[str]] = None,
        titles: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        批量上传文件
        
        Args:
            file_paths: 文件路径列表
            alt_texts: Alt文本列表
            titles: 标题列表
            
        Returns:
            批量上传结果
        """
        results = []
        success_count = 0
        fail_count = 0
        
        for i, path in enumerate(file_paths):
            alt_text = alt_texts[i] if alt_texts and i < len(alt_texts) else None
            title = titles[i] if titles and i < len(titles) else None
            
            result = await self._upload_local_file(path, alt_text, title)
            
            if result.get("success"):
                success_count += 1
            else:
                fail_count += 1
            
            results.append({
                "file": path,
                "result": result
            })
        
        return {
            "total": len(file_paths),
            "success": success_count,
            "failed": fail_count,
            "results": results
        }
    
    async def get_media_info(self, media_id: int) -> Dict[str, Any]:
        """获取媒体信息"""
        url = self._media_url(media_id)
        headers = self._get_headers()
        
        try:
            response = await self.http_client.get(url, headers=headers)
            response.raise_for_status()
            
            return {
                "success": True,
                "media": response.json()
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def delete_media(self, media_id: int) -> Dict[str, Any]:
        """删除媒体"""
        url = self._media_url(media_id)
        headers = self._get_headers()
        
        try:
            response = await self.http_client.delete(url, headers=headers)
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
    
    async def close(self):
        """关闭客户端"""
        await self.http_client.aclose()


# CrewAI Tool 包装
def get_media_uploader_tool():
    """返回CrewAI可用的Tool"""
    from crewai.tools import tool
    
    @tool("media_uploader")
    def media_uploader_tool(
        action: str,
        file_path: str = "",
        file_url: str = "",
        file_name: str = "",
        alt_text: str = "",
        title: str = ""
    ) -> str:
        """
        上传图片或其他媒体文件到CMS。
        
        Args:
            action: 操作类型 upload/info/delete
            file_path: 本地文件路径（用于upload）
            file_url: 远程URL（用于从URL上传）
            file_name: 文件名
            alt_text: Alt文本
            title: 标题
            
        Returns:
            JSON格式的上传结果
        """
        import asyncio
        
        uploader = MediaUploader()
        
        async def run():
            if action == "upload":
                return await uploader.upload_file(
                    file_path=file_path or None,
                    file_url=file_url or None,
                    file_name=file_name or None,
                    alt_text=alt_text or None,
                    title=title or None
                )
            elif action == "info":
                return await uploader.get_media_info(int(file_path))
            elif action == "delete":
                return await uploader.delete_media(int(file_path))
            else:
                return {"success": False, "error": f"未知操作: {action}"}
        
        try:
            result = asyncio.run(run())
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            asyncio.run(uploader.close())
    
    return media_uploader_tool


if __name__ == "__main__":
    # 测试
    import asyncio
    
    async def test():
        uploader = MediaUploader()
        
        # 上传本地文件
        result = await uploader.upload_file(
            file_path="/path/to/image.jpg",
            alt_text="测试图片",
            title="测试标题"
        )
        
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        await uploader.close()
    
    asyncio.run(test())
