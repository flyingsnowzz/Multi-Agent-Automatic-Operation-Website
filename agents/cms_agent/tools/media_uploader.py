#!/usr/bin/env python3
"""
媒体上传工具 - CMSAgent
上传图片、视频等到CMS媒体库
"""

import os
import json
import httpx
import base64
import mimetypes
from typing import Dict, List, Any, Optional, Union
from pathlib import Path


class MediaUploader:
    """媒体上传工具"""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        self.base_url = base_url or os.environ.get("CMS_BASE_URL", "http://localhost:8080/api")
        self.api_key = api_key or os.environ.get("CMS_API_KEY", "")
        self.token = None
        self.http_client = httpx.AsyncClient(timeout=120.0)  # 大文件需要更长超时
    
    async def upload_file(
        self,
        file_path: Optional[str] = None,
        file_url: Optional[str] = None,
        file_data: Optional[str] = None,  # Base64编码
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        alt_text: Optional[str] = None,
        title: Optional[str] = None,
        caption: Optional[str] = None
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
            return await self._upload_local_file(file_path, alt_text, title, caption)
        elif file_url:
            return await self._upload_from_url(file_url, alt_text, title, caption)
        elif file_data:
            return await self._upload_base64(file_data, file_name, mime_type, alt_text, title, caption)
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
        caption: Optional[str] = None
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
        
        # 上传
        return await self._do_upload(
            file_data=file_data,
            file_name=path.name,
            mime_type=mime_type,
            alt_text=alt_text,
            title=title or path.stem,
            caption=caption
        )
    
    async def _upload_from_url(
        self,
        url: str,
        alt_text: Optional[str] = None,
        title: Optional[str] = None,
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """从URL下载后上传"""
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
            
            # 上传
            return await self._do_upload(
                file_data=file_data,
                file_name=file_name,
                mime_type=mime_type,
                alt_text=alt_text,
                title=title,
                caption=caption
            )
            
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
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """上传Base64编码的文件"""
        try:
            # 解码
            data = base64.b64decode(file_data)
            
            # 获取MIME类型
            if not mime_type:
                mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            
            # 上传
            return await self._do_upload(
                file_data=data,
                file_name=file_name,
                mime_type=mime_type,
                alt_text=alt_text,
                title=title,
                caption=caption
            )
            
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
        url = f"{self.base_url}/media"
        
        # 构建表单数据
        files = {
            "file": (file_name, file_data, mime_type)
        }
        
        data = {}
        if alt_text:
            data["alt_text"] = alt_text
        if title:
            data["title"] = title
        if caption:
            data["caption"] = caption
        
        # 添加认证
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        
        try:
            response = await self.http_client.post(
                url,
                files=files,
                data=data,
                headers=headers
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "success": True,
                "media_id": result.get("id"),
                "url": result.get("url", ""),
                "thumbnail_url": result.get("thumbnail_url", result.get("url", "")),
                "file_name": result.get("filename", file_name),
                "file_type": result.get("mime_type", mime_type),
                "file_size": result.get("size", len(file_data)),
                "alt_text": result.get("alt_text", alt_text),
                "title": result.get("title", title)
            }
            
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
        url = f"{self.base_url}/media/{media_id}"
        
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        
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
        url = f"{self.base_url}/media/{media_id}"
        
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        
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
