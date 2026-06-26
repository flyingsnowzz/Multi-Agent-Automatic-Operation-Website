import os
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from agents.cms_agent import CMSAgent
from agents.cms_agent.tools.cms_client import CMSClient
from agents.cms_agent.tools.media_uploader import MediaUploader


class TestCMSAgent(unittest.TestCase):
    def _base_config(self, *, dry_run: bool = True) -> dict:
        return {
            "cms": {"provider": "custom", "api": {"base_url": "https://example.com/api", "api_key": "k", "version": "v1"}},
            "publishing": {
                "dry_run": dry_run,
                "pre_publish_check": [
                    "title_not_empty",
                    "content_not_empty",
                    "content_html_not_empty",
                    "slug_not_empty",
                    "status_not_empty",
                    "slug_unique",
                ],
            },
            "images": {"featured_image": {"required": False}, "upload_to_cms": False},
            "cms": {
                "provider": "custom",
                "api": {"base_url": "https://example.com/api", "api_key": "k", "version": "v1"},
                "custom": {
                    "post_contract": {
                        "required_fields": ["title", "content_html", "slug", "status"],
                    }
                },
            },
            "url": {"slug": {"separator": "-", "lowercase": True, "max_length": 60}},
        }

    def test_dry_run_gate_does_not_call_remote_write_or_auth(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": ""}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)

            auth = AsyncMock(return_value={"success": True})
            create_post = AsyncMock(return_value={"success": True})
            update_post = AsyncMock(return_value={"success": True})
            find_posts = AsyncMock(return_value=[])
            upload_file = AsyncMock(return_value={"success": True})

            with (
                patch.object(CMSClient, "authenticate_if_needed", new=auth),
                patch.object(CMSClient, "create_post", new=create_post),
                patch.object(CMSClient, "update_post", new=update_post),
                patch.object(CMSClient, "find_posts_by_slug", new=find_posts),
                patch.object(MediaUploader, "upload_file", new=upload_file),
            ):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "t", "content_html": "<p>x</p>", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "t"},
                    )
                )

        self.assertEqual(out["status"], "dry_run")
        self.assertTrue(out["checks"]["title_not_empty"])
        self.assertFalse(out["checks"]["slug_checked"])
        auth.assert_not_awaited()
        create_post.assert_not_awaited()
        update_post.assert_not_awaited()
        find_posts.assert_not_awaited()
        upload_file.assert_not_awaited()

    def test_dry_run_does_not_check_slug_remotely_by_default(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)
            find_posts = AsyncMock(return_value=[{"id": 1, "slug": "dup"}])

            with patch.object(CMSClient, "find_posts_by_slug", new=find_posts):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "dup", "content_html": "<p>x</p>", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "dup"},
                    )
                )

        self.assertEqual(out["status"], "dry_run")
        self.assertFalse(out["checks"]["slug_checked"])
        find_posts.assert_not_awaited()

    def test_dry_run_slug_check_only_when_enabled(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)
            agent.config["publishing"]["slug_check_in_dry_run"] = True

            async def side_effect(slug):
                return [{"id": 1, "slug": "dup"}] if slug == "dup" else []

            find_posts = AsyncMock(side_effect=side_effect)
            with patch.object(CMSClient, "find_posts_by_slug", new=find_posts):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "dup", "content_html": "<p>x</p>", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "dup"},
                    )
                )

        self.assertEqual(out["status"], "dry_run")
        self.assertTrue(out["checks"]["slug_checked"])
        self.assertEqual(out["payload"]["slug"], "dup-2")
        find_posts.assert_awaited()

    def test_title_is_builtin_required_check(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)
            out = asyncio.run(
                agent.execute(
                    article={"title": "   ", "content_html": "<p>x</p>", "meta": {}},
                    page_info={"category": "demo", "tags": [], "slug": ""},
                )
            )
        self.assertEqual(out["status"], "failed")
        self.assertIn("title_not_empty", out["errors"])
        self.assertFalse(out["checks"]["title_not_empty"])

    def test_chinese_title_gets_non_empty_slug(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)
            out = asyncio.run(
                agent.execute(
                    article={"title": "纯中文标题", "content_html": "<p>x</p>", "meta": {}},
                    page_info={"category": "demo", "tags": [], "slug": ""},
                )
            )
        self.assertEqual(out["status"], "dry_run")
        self.assertTrue(out["checks"]["slug_not_empty"])
        self.assertTrue(out["payload"]["slug"])

    def test_contract_required_fields_missing_show_in_local_errors(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)
            out = asyncio.run(
                agent.execute(
                    article={"title": "Title", "content_html": "", "content": "", "meta": {}},
                    page_info={"category": "demo", "tags": [], "slug": ""},
                )
            )
        self.assertEqual(out["status"], "failed")
        self.assertIn("content_html_not_empty", out["errors"])
        self.assertTrue(out["checks"]["status_not_empty"])
        self.assertTrue(out["checks"]["slug_not_empty"])

    def test_slug_conflict_can_fail(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)
            agent.config["publishing"]["slug_conflict"] = {"strategy": "fail"}
            agent.config["publishing"]["slug_check_in_dry_run"] = True

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
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "false"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=True)
            agent.config["publishing"]["slug_conflict"] = {"strategy": "overwrite_update"}
            agent.config["publishing"]["slug_check_in_dry_run"] = True

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

    def test_update_result_prefers_article_url_when_available(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "true"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=False)
            agent.config["publishing"]["slug_conflict"] = {"strategy": "overwrite_update"}

            with (
                patch.object(CMSClient, "find_posts_by_slug", new=AsyncMock(return_value=[{"id": 12, "slug": "dup"}])),
                patch.object(CMSClient, "authenticate_if_needed", new=AsyncMock(return_value={"success": True})),
                patch.object(
                    CMSClient,
                    "update_post",
                    new=AsyncMock(return_value={"success": True, "post_id": 12, "post_url": "https://example.com/posts/dup", "status": "draft"}),
                ),
            ):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "dup", "content_html": "<p>x</p>", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "dup"},
                    )
                )

        self.assertEqual(out["status"], "draft")
        self.assertEqual(out["article_id"], 12)
        self.assertEqual(out["article_url"], "https://example.com/posts/dup")

    def test_featured_image_required_and_upload_failed_returns_failed(self):
        with patch.dict(os.environ, {"CMS_ENABLE_REAL_PUBLISH": "true"}, clear=False):
            agent = CMSAgent()
            agent.config = self._base_config(dry_run=False)
            agent.config["images"] = {
                "upload_to_cms": True,
                "upload_failure_strategy": "fail",
                "featured_image": {"required": True},
            }

            create_post = AsyncMock(return_value={"success": True})
            with (
                patch.object(CMSClient, "find_posts_by_slug", new=AsyncMock(return_value=[])),
                patch.object(CMSClient, "authenticate_if_needed", new=AsyncMock(return_value={"success": True})),
                patch.object(MediaUploader, "upload_file", new=AsyncMock(return_value={"success": False, "error": "upload_failed"})),
                patch.object(CMSClient, "create_post", new=create_post),
            ):
                out = asyncio.run(
                    agent.execute(
                        article={"title": "dup", "content_html": "<p>x</p>", "featured_image_url": "https://cdn/img.webp", "meta": {}},
                        page_info={"category": "demo", "tags": [], "slug": "dup"},
                        images={"featured_image_url": "https://cdn/img.webp", "featured_alt": "cover"},
                    )
                )

        self.assertEqual(out["status"], "failed")
        self.assertIn("upload_failed", out["errors"])
        create_post.assert_not_awaited()

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
