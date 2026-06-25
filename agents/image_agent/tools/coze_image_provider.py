#!/usr/bin/env python3
"""
Coze Site 图片生成 Provider
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
    DEFAULT_ENDPOINT = "https://gbfbffgpg6.coze.site/run"
    DEFAULT_CACHE_DIR = "output/images/coze_cache"
    DEFAULT_TIMEOUT = 120.0

    def __init__(
        self,
        jwt_token: Optional[str] = None,
        endpoint: Optional[str] = None,
        cache_dir: Optional[str] = None,
        config_path: str = "agents/image_agent/config.yaml",
    ):
        self.jwt_token = jwt_token or os.environ.get("COZE_JWT_TOKEN", "")
        self.endpoint = (endpoint or self.DEFAULT_ENDPOINT).rstrip("/")
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

        self.config = self._load_config(config_path)
        self.coze_cfg = self._extract_coze_config()

        if not self.jwt_token and self.coze_cfg.get("jwt_token"):
            resolved = _deep_env_resolve(self.coze_cfg["jwt_token"])
            if resolved:
                self.jwt_token = resolved

        cfg_endpoint = self.coze_cfg.get("endpoint", "").strip()
        if cfg_endpoint:
            self.endpoint = cfg_endpoint.rstrip("/")

        cfg_cache = self.coze_cfg.get("cache_dir", "").strip()
        if cfg_cache:
            self.cache_dir = cfg_cache
            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

        self.timeout = float(self.coze_cfg.get("timeout", self.DEFAULT_TIMEOUT))
        self.max_retries = int(self.coze_cfg.get("max_retries", 3))
        self.download_images = bool(self.coze_cfg.get("download_images", True))

        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0)
        )

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        if not config_path or not os.path.exists(config_path):
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return raw

    def _extract_coze_config(self) -> Dict[str, Any]:
        img = self.config.get("image_generation") or {}
        coze = img.get("coze") or {}
        return coze if isinstance(coze, dict) else {}

    async def generate(self, prompt: str, n: int = 1) -> Dict[str, Any]:
        if not self.jwt_token:
            return {"success": False, "error": "coze_jwt_not_configured", "images": []}

        images: List[Dict[str, Any]] = []
        errors: List[str] = []

        for i in range(max(n, 1)):
            if i > 0:
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
        return {
            "success": False,
            "error": "; ".join(errors) if errors else "no_images_generated",
            "images": [],
        }

    async def _generate_single(self, prompt: str, index: int = 0) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json",
        }
        payload = {"text_content": prompt}
        last_error = ""

        for attempt in range(self.max_retries):
            try:
                resp = await self.http_client.post(
                    self.endpoint, json=payload, headers=headers
                )
                if resp.status_code in (401, 403):
                    return {
                        "success": False,
                        "error": f"coze_auth_failed_{resp.status_code}",
                        "detail": resp.text[:500],
                    }
                if resp.status_code != 200:
                    last_error = f"coze_http_{resp.status_code}"
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return {"success": False, "error": last_error, "detail": resp.text[:500]}

                data = resp.json()
                if "detail" in data and isinstance(data["detail"], dict):
                    err = data["detail"]
                    return {
                        "success": False,
                        "error": f"coze_error_{err.get('error_code', 'unknown')}",
                        "detail": err.get("error_message", str(err)),
                    }

                cover_images = data.get("cover_images") or []
                if not cover_images:
                    return {"success": False, "error": "coze_no_images_returned", "data": data}

                image_url = cover_images[0].get("url", "")
                run_id = data.get("run_id", "")
                if not image_url:
                    return {"success": False, "error": "coze_empty_image_url"}

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
                last_error = "coze_timeout"
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(3 ** attempt)
                    continue
            except Exception as e:
                last_error = f"coze_exception: {str(e)}"
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue

        return {"success": False, "error": last_error}

    async def _download_image(self, url: str, prompt: str, run_id: str, index: int = 0) -> str:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        url_path = url.split("?")[0]
        ext = ".jpg"
        if "." in url_path.split("/")[-1]:
            ext = "." + url_path.rsplit(".", 1)[-1]
            if len(ext) > 5:
                ext = ".jpg"
        filename = f"coze_{url_hash}{ext}"
        filepath = os.path.join(self.cache_dir, filename)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return os.path.abspath(filepath)
        try:
            resp = await self.http_client.get(url)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(resp.content)
            return os.path.abspath(filepath)
        except Exception:
            return ""

    async def close(self):
        await self.http_client.aclose()


def get_coze_image_tool():
    from crewai.tools import tool

    @tool("coze_image_generator")
    def coze_image_tool(prompt: str, n: int = 1) -> str:
        async def _run():
            provider = CozeImageProvider()
            try:
                return await provider.generate(prompt=prompt, n=int(n) if n else 1)
            finally:
                await provider.close()
        result = asyncio.run(_run())
        return json.dumps(result, ensure_ascii=False, indent=2)

    return coze_image_tool


if __name__ == "__main__":
    async def test():
        provider = CozeImageProvider()
        print("=" * 60)
        print("CozeImageProvider test")
        print(f"Endpoint: {provider.endpoint}")
        print(f"JWT: {'configured' if provider.jwt_token else 'NOT configured'}")
        print(f"Cache: {provider.cache_dir}")
        print("=" * 60)
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
        if result.get("success"):
            for img in result.get("images", []):
                local = img.get("local_path", "")
                if local and os.path.exists(local):
                    print(f"\nSaved: {local} ({os.path.getsize(local)//1024} KB)")
        await provider.close()

    asyncio.run(test())
