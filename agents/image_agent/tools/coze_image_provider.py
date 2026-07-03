#!/usr/bin/env python3
"""
Coze Site 图片生成 Provider

设计说明：
    本模块封装 Coze Site 的图片生成 HTTP 接口（一个部署在 coze.site 的 Bot）。
    与 OpenAI Images API 不同，Coze Site 通过 JWT Bearer Token 鉴权，
    请求体为 {"text_content": prompt}，响应体中 cover_images 字段携带图片 URL。

    核心流程：
      1. 携带 JWT 向 Coze Site endpoint 发 POST 请求
      2. 解析响应中的 cover_images，取出图片 URL
      3. （可选）下载图片到本地缓存目录
      4. 返回标准化结果 {success, images: [{url, local_path, run_id}]}

    容错策略：
      - 遇 401/402/403（鉴权/配额问题）直接失败，不重试
      - 其它错误（超时、5xx 等）按指数退避重试，最多 max_retries 次
"""

import os
import json
import hashlib
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List

import httpx
import yaml


def _deep_env_resolve(value: Any) -> Any:
    """
    递归解析配置中形如 ${VAR} 的环境变量占位符。

    注意：此版本不支持 :- 默认值语法（与其它模块的版本略有差异），
    仅用于解析 coze 配置块中的 ${COZE_JWT_TOKEN} 等。
    """
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


class CozeImageProvider:
    """Coze Site 图片生成 Provider

    通过 HTTP 调用 Coze Site Bot 实现文生图。
    支持多张生成、本地缓存、指数退避重试。

    配置来源优先级：构造参数 > 环境变量 > config.yaml
    """

    # 默认常量（构造参数和配置都未指定时使用）
    DEFAULT_ENDPOINT = "https://gbfbffgpg6.coze.site/run"
    DEFAULT_CACHE_DIR = "output/images/coze_cache"
    DEFAULT_TIMEOUT = 120.0  # Coze 生图较慢，默认 120s

    def __init__(
        self,
        jwt_token: Optional[str] = None,
        endpoint: Optional[str] = None,
        cache_dir: Optional[str] = None,
        config_path: str = "agents/image_agent/config.yaml",
    ):
        # JWT Token：构造参数 > 环境变量 > 配置文件
        self.jwt_token = jwt_token or os.environ.get("COZE_JWT_TOKEN", "")
        # endpoint：构造参数 > 默认值（后续可能被配置覆盖）
        self.endpoint = (endpoint or self.DEFAULT_ENDPOINT).rstrip("/")
        # 缓存目录
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

        # 加载配置文件
        self.config = self._load_config(config_path)
        self.coze_cfg = self._extract_coze_config()

        # 若构造时未给 JWT，尝试从配置解析（配置里可能是 ${COZE_JWT_TOKEN} 占位符）
        if not self.jwt_token and self.coze_cfg.get("jwt_token"):
            resolved = _deep_env_resolve(self.coze_cfg["jwt_token"])
            if resolved:
                self.jwt_token = resolved

        # 配置中的 endpoint / cache_dir 优先级高于默认值
        cfg_endpoint = self.coze_cfg.get("endpoint", "").strip()
        if cfg_endpoint:
            self.endpoint = cfg_endpoint.rstrip("/")

        cfg_cache = self.coze_cfg.get("cache_dir", "").strip()
        if cfg_cache:
            self.cache_dir = cfg_cache
            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

        # 超时、重试、是否下载图片
        self.timeout = float(self.coze_cfg.get("timeout", self.DEFAULT_TIMEOUT))
        self.max_retries = int(self.coze_cfg.get("max_retries", 3))
        self.download_images = bool(self.coze_cfg.get("download_images", True))

        # 异步 HTTP 客户端：总超时 timeout，连接超时 10s
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0)
        )

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """读取 config.yaml（不解析占位符，原始返回）"""
        if not config_path or not os.path.exists(config_path):
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return raw

    def _extract_coze_config(self) -> Dict[str, Any]:
        """从配置中提取 image_generation.coze 配置块"""
        img = self.config.get("image_generation") or {}
        coze = img.get("coze") or {}
        return coze if isinstance(coze, dict) else {}

    async def generate(self, prompt: str, n: int = 1) -> Dict[str, Any]:
        """生成图片（主入口）

        Args:
            prompt: 图片描述提示词
            n: 生成数量（每张单独请求，间隔 0.5s 避免限流）

        Returns:
            {success, images: [{url, local_path, run_id, index}], provider, prompt, total}
            或 {success: False, error, images: []}
        """
        # JWT 未配置直接返回失败
        if not self.jwt_token:
            return {"success": False, "error": "coze_jwt_not_configured", "images": []}

        images: List[Dict[str, Any]] = []
        errors: List[str] = []

        # 逐张生成（Coze Site 单次请求只返回一张图）
        for i in range(max(n, 1)):
            if i > 0:
                # 多张之间间隔 0.5s，避免触发限流
                await asyncio.sleep(0.5)
            result = await self._generate_single(prompt, index=i)
            if result.get("success"):
                images.append(result)
            else:
                errors.append(result.get("error", "unknown"))

        if images:
            return {
                "success": True,
                "images": images,
                "provider": "coze",
                "prompt": prompt,
                "total": len(images),
            }
        # 全部失败
        return {
            "success": False,
            "error": "; ".join(errors) if errors else "no_images_generated",
            "images": [],
        }

    async def _generate_single(self, prompt: str, index: int = 0) -> Dict[str, Any]:
        """生成单张图片（含重试逻辑）

        Args:
            prompt: 提示词
            index: 当前图片序号（用于本地文件命名）

        Returns:
            {success, url, local_path, run_id, index} 或 {success: False, error}
        """
        headers = {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json",
        }
        # Coze Site 的请求体字段名为 text_content
        payload = {"text_content": prompt}
        last_error = ""

        # 指数退避重试
        for attempt in range(self.max_retries):
            try:
                resp = await self.http_client.post(
                    self.endpoint, json=payload, headers=headers
                )
                # 鉴权/配额错误：不重试，直接返回
                if resp.status_code in (401, 402, 403):
                    return {
                        "success": False,
                        "error": f"coze_http_{resp.status_code}",
                        "detail": resp.text[:500],
                    }
                # 其它非 200：重试
                if resp.status_code != 200:
                    last_error = f"coze_http_{resp.status_code}"
                    if attempt < self.max_retries - 1:
                        # 指数退避：1s, 2s, 4s...
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return {"success": False, "error": last_error, "detail": resp.text[:500]}

                data = resp.json()
                # Coze 返回的 detail 字段表示业务错误
                if "detail" in data and isinstance(data["detail"], dict):
                    err = data["detail"]
                    return {
                        "success": False,
                        "error": f"coze_error_{err.get('error_code', 'unknown')}",
                        "detail": err.get("error_message", str(err)),
                    }

                # 提取图片 URL
                cover_images = data.get("cover_images") or []
                if not cover_images:
                    return {"success": False, "error": "coze_no_images_returned", "data": data}

                image_url = cover_images[0].get("url", "")
                run_id = data.get("run_id", "")
                if not image_url:
                    return {"success": False, "error": "coze_empty_image_url"}

                # 可选：下载到本地
                local_path = ""
                if self.download_images and image_url:
                    local_path = await self._download_image(image_url, prompt, run_id, index)

                return {
                    "success": True,
                    "url": image_url,
                    "local_path": local_path,
                    "run_id": run_id,
                    "index": index,
                }

            except httpx.TimeoutException:
                # 超时：更长退避（3s, 9s, 27s...）
                last_error = "coze_timeout"
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(3 ** attempt)
                    continue
            except Exception as e:
                # 其它异常：指数退避
                last_error = f"coze_exception: {str(e)}"
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue

        # 重试耗尽
        return {"success": False, "error": last_error}

    async def _download_image(self, url: str, prompt: str, run_id: str, index: int = 0) -> str:
        """下载图片到本地缓存

        用 URL 的 MD5 前缀做文件名，避免重复下载。
        已存在且非空时直接返回路径，跳过下载。
        """
        # 用 URL 哈希做文件名，保证唯一性
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        # 从 URL 推断扩展名
        url_path = url.split("?")[0]
        ext = ".jpg"
        if "." in url_path.split("/")[-1]:
            ext = "." + url_path.rsplit(".", 1)[-1]
            # 扩展名过长（异常情况）则回退到 .jpg
            if len(ext) > 5:
                ext = ".jpg"
        filename = f"coze_{url_hash}{ext}"
        filepath = os.path.join(self.cache_dir, filename)
        # 已存在且非空，跳过下载
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return os.path.abspath(filepath)
        try:
            resp = await self.http_client.get(url)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(resp.content)
            return os.path.abspath(filepath)
        except Exception:
            # 下载失败返回空字符串，不影响主流程
            return ""

    async def close(self):
        """关闭 HTTP 客户端，释放连接池"""
        await self.http_client.aclose()


def get_coze_image_tool():
    """返回 CrewAI 可用的 Tool

    将 CozeImageProvider 包装成 CrewAI @tool。
    """
    from crewai.tools import tool

    @tool("coze_image_generator")
    def coze_image_tool(prompt: str, n: int = 1) -> str:
        async def _run():
            provider = CozeImageProvider()
            try:
                return await provider.generate(prompt=prompt, n=int(n) if n else 1)
            finally:
                await provider.close()
        # CrewAI 工具是同步函数，内部用 asyncio.run 驱动异步逻辑
        result = asyncio.run(_run())
        return json.dumps(result, ensure_ascii=False, indent=2)

    return coze_image_tool


if __name__ == "__main__":
    # 模块自测
    async def test():
        provider = CozeImageProvider()
        print("=" * 60)
        print("CozeImageProvider test")
        print(f"Endpoint: {provider.endpoint}")
        print(f"JWT: {'configured' if provider.jwt_token else 'NOT configured'}")
        print(f"Cache: {provider.cache_dir}")
        print("=" * 60)
        # 未配置 JWT 时直接退出
        if not provider.jwt_token:
            print("\nJWT Token not configured. Set COZE_JWT_TOKEN env var.")
            await provider.close()
            return
        result = await provider.generate(
            prompt="a cute orange cat sitting on a windowsill watching sunset",
            n=1,
        )
        print("\nResult:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # 若成功，打印本地文件大小
        if result.get("success"):
            for img in result.get("images", []):
                local = img.get("local_path", "")
                if local and os.path.exists(local):
                    print(f"\nSaved: {local} ({os.path.getsize(local)//1024} KB)")
        await provider.close()

    asyncio.run(test())
