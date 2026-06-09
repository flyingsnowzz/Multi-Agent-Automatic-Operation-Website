import os
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from agents.cms_agent import CMSAgent
from agents.cms_agent.tools.cms_client import CMSClient


class TestCMSAgent(unittest.TestCase):
    def test_dry_run_gate(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": ""}, clear=False):
            agent = CMSAgent()
            agent.config = {
                "cms": {"provider": "custom", "api": {"base_url": "https://example.com/api", "api_key": "k", "version": "v1"}},
                "publishing": {"dry_run": True, "pre_publish_check": ["content_not_empty"]},
                "images": {"featured_image": {"required": False}},
            }
            out = asyncio.run(
                agent.execute(
                    article={"title": "t", "content_html": "<p>x</p>", "meta": {}},
                    page_info={"category": "demo", "tags": [], "slug": "t"},
                )
            )
        self.assertEqual(out["status"], "dry_run")
        self.assertIn("payload", out)

    def test_slug_conflict_resolution_in_dry_run_when_enabled(self):
        with patch.dict(os.environ, {"CMS_ENABLE_SLUG_CHECK": "true", "CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = {
                "cms": {"provider": "custom", "api": {"base_url": "https://example.com/api", "api_key": "k", "version": "v1"}},
                "publishing": {"dry_run": True, "pre_publish_check": ["slug_unique", "content_not_empty"]},
                "images": {"featured_image": {"required": False}},
                "url": {"slug": {"separator": "-", "lowercase": True, "max_length": 60}},
            }

            async def side_effect(slug):
                return [{"id": 1, "slug": "dup"}] if slug == "dup" else []

            with patch.object(CMSClient, "find_posts_by_slug", new=AsyncMock(side_effect=side_effect)):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "dup", "content_html": "<p>x</p>", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "dup"},
                    )
                )
        self.assertEqual(out["status"], "dry_run")
        self.assertEqual(out["payload"]["slug"], "dup-2")

    def test_slug_conflict_can_fail(self):
        with patch.dict(os.environ, {"CMS_ENABLE_SLUG_CHECK": "true", "CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = {
                "cms": {"provider": "custom", "api": {"base_url": "https://example.com/api", "api_key": "k", "version": "v1"}},
                "publishing": {
                    "dry_run": True,
                    "pre_publish_check": ["slug_unique", "content_not_empty"],
                    "slug_conflict": {"strategy": "fail"},
                },
                "images": {"featured_image": {"required": False}},
            }

            with patch.object(CMSClient, "find_posts_by_slug", new=AsyncMock(return_value=[{"id": 12, "slug": "dup"}])):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "dup", "content_html": "<p>x</p>", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "dup"},
                    )
                )
        self.assertEqual(out["status"], "failed")
        self.assertIn("slug_unique", out["errors"])

    def test_slug_conflict_can_select_update(self):
        with patch.dict(os.environ, {"CMS_ENABLE_SLUG_CHECK": "true", "CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = {
                "cms": {"provider": "custom", "api": {"base_url": "https://example.com/api", "api_key": "k", "version": "v1"}},
                "publishing": {
                    "dry_run": True,
                    "pre_publish_check": ["slug_unique", "content_not_empty"],
                    "slug_conflict": {"strategy": "overwrite_update"},
                },
                "images": {"featured_image": {"required": False}},
            }

            with patch.object(CMSClient, "find_posts_by_slug", new=AsyncMock(return_value=[{"id": 12, "slug": "dup"}])):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "dup", "content_html": "<p>x</p>", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "dup"},
                    )
                )
        self.assertEqual(out["status"], "dry_run")
        self.assertEqual(out["payload"]["_cms_action"], "update")
        self.assertEqual(out["payload"]["_cms_post_id"], 12)

    def test_custom_payload_uses_backend_contract_fields(self):
        client = CMSClient(provider="custom", base_url="https://example.com/api", api_key="k", api_version="v1")
        payload = client._build_custom_post_payload(
            title="Title",
            content="<p>html</p>",
            slug="title",
            status="draft",
            categories="emba-guide",
            tags=["EMBA"],
            featured_image="https://cdn/image.webp",
            meta_title="SEO Title",
            meta_description="SEO Desc",
            publish_date="2026-06-09T09:00:00+08:00",
            kwargs={
                "content_html": "<p>html</p>",
                "content_md": "# md",
                "focus_keyword": "EMBA",
                "schema_json": {"@type": "Article"},
                "topic_id": "topic-1",
            },
        )
        asyncio.run(client.close())
        self.assertEqual(payload["content_html"], "<p>html</p>")
        self.assertEqual(payload["content_md"], "# md")
        self.assertEqual(payload["category"], "emba-guide")
        self.assertEqual(payload["meta"]["seo_title"], "SEO Title")
        self.assertNotIn("content", payload)
        self.assertFalse(any(k.startswith("custom_") for k in payload))

    def test_retry_helper(self):
        agent = CMSAgent()
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("x")
            return "ok"

        out = asyncio.run(agent._retry(fn=fn, retry_cfg={"enabled": True, "max_retries": 2, "delay_seconds": 0}))
        self.assertEqual(out, "ok")
        self.assertEqual(attempts["n"], 2)


if __name__ == "__main__":
    unittest.main()
