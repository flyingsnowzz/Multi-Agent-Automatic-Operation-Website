#!/usr/bin/env python3
"""
CMS客户端工具 - CMSAgent
通过API与CMS系统交互，管理内容发布
"""

import os
import json
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime
from urllib.parse import urljoin


class CMSClient:
    """CMS API客户端"""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ):
        """
        初始化CMS客户端
        
        Args:
            base_url: CMS API基础URL
            api_key: API密钥
            username: 用户名
            password: 密码
        """
        self.base_url = base_url or os.environ.get("CMS_BASE_URL", "http://localhost:8080/api")
        self.api_key = api_key or os.environ.get("CMS_API_KEY", "")
        self.username = username or os.environ.get("CMS_USERNAME", "")
        self.password = password or os.environ.get("CMS_PASSWORD", "")
        self.token = None
        self.http_client = httpx.AsyncClient(timeout=30.0)
    
    async def authenticate(self) -> Dict[str, Any]:
        """获取访问令牌"""
        url = urljoin(self.base_url, "/auth/login")
        
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
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            "Content-Type": "application/json"
        }
        
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
        categories: Optional[List[int]] = None,
        tags: Optional[List[int]] = None,
        featured_image: Optional[str] = None,
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
        
        # 构建文章数据
        post_data = {
            "title": title,
            "content": content,
            "slug": slug,
            "status": status,
            "date": publish_date or datetime.now().isoformat()
        }
        
        # 可选字段
        if categories:
            post_data["categories"] = categories
        if tags:
            post_data["tags"] = tags
        if featured_image:
            post_data["featured_media"] = featured_image
        
        # SEO字段
        if meta_title:
            post_data["meta_title"] = meta_title
        if meta_description:
            post_data["meta_description"] = meta_description
        
        # 自定义字段
        for key, value in kwargs.items():
            if value is not None:
                post_data[f"custom_{key}"] = value
        
        url = urljoin(self.base_url, "/posts")
        
        try:
            response = await self.http_client.post(
                url,
                json=post_data,
                headers=self._get_headers()
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "success": True,
                "post_id": result.get("id"),
                "post_url": result.get("link", ""),
                "slug": result.get("slug", ""),
                "status": result.get("status", ""),
                "data": result
            }
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "error": f"HTTP错误: {e.response.status_code}",
                "details": e.response.text
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
        url = urljoin(self.base_url, f"/posts/{post_id}")
        
        try:
            response = await self.http_client.patch(
                url,
                json=kwargs,
                headers=self._get_headers()
            )
            response.raise_for_status()
            
            return {
                "success": True,
                "post_id": post_id,
                "updated": True
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
        url = urljoin(self.base_url, f"/posts/{post_id}")
        
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
        url = urljoin(self.base_url, f"/posts/{post_id}")
        
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
        url = urljoin(self.base_url, "/categories")
        
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
        url = urljoin(self.base_url, "/tags")
        
        try:
            response = await self.http_client.get(
                url,
                headers=self._get_headers()
            )
            response.raise_for_status()
            
            return response.json()
        except Exception:
            return []
    
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
            # 先认证
            auth_result = await client.authenticate()
            if not auth_result.get("success"):
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
