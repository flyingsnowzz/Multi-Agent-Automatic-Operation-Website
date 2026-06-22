import os
import asyncio
import unittest
import base64

from agents.cms_agent import CMSAgent
from agents.cms_agent.tools.cms_client import CMSClient
from agents.cms_agent.tools.media_uploader import MediaUploader


def _enabled(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


class TestCustomCMSContract(unittest.TestCase):
    @unittest.skipUnless(_enabled("CMS_CONTRACT_TEST"), "CMS_CONTRACT_TEST not enabled")
    def test_custom_contract_roundtrip(self):
        agent = CMSAgent()
        cfg = agent.config or {}
        cms_cfg = cfg.get("cms") or {}
        api_cfg = cms_cfg.get("api") or {}
        provider = cms_cfg.get("provider") or "custom"
        publishing = cfg.get("publishing") or {}

        if provider != "custom":
            self.skipTest("provider is not custom")
        if bool(publishing.get("dry_run", True)):
            self.skipTest("publishing.dry_run is true")
        if not _enabled("CMS_ENABLE_REAL_PUBLISH"):
            self.skipTest("CMS_ENABLE_REAL_PUBLISH not enabled")

        client = CMSClient(
            provider="custom",
            base_url=api_cfg.get("base_url"),
            api_key=api_cfg.get("api_key"),
            api_version=api_cfg.get("version"),
            contract=cfg,
        )
        uploader = MediaUploader(
            provider="custom",
            base_url=api_cfg.get("base_url"),
            api_key=api_cfg.get("api_key"),
            api_version=api_cfg.get("version"),
            contract=cfg,
        )

        async def run():
            auth = await client.authenticate_if_needed()
            if not auth.get("success", True):
                return {"ok": False, "error": auth}

            existing = await client.find_posts_by_slug("trae-contract-test")

            png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
                b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xb1\xd9\x8a\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            png_b64 = base64.b64encode(png).decode("ascii")
            media = await uploader.upload_file(
                file_data=png_b64,
                file_name="contract.png",
                mime_type="image/png",
                alt_text="contract test",
                title="contract test",
            )
            if not media.get("success"):
                return {"ok": False, "error": media}

            r = await client.create_post(
                title="Contract Test",
                content="<p>contract test</p>",
                slug="trae-contract-test",
                status="draft",
                categories="contract-test",
                tags=["contract-test"],
                featured_image=media.get("media_id") or media.get("url"),
                meta_title="Contract Test",
                meta_description="Contract Test",
                content_html="<p>contract test</p>",
                content_md="contract test",
                excerpt="Contract Test",
                focus_keyword="contract",
            )
            return {
                "ok": True,
                "existing": existing,
                "media": media,
                "post": r,
            }

        try:
            out = asyncio.run(run())
            self.assertTrue(out["ok"])
            post = out["post"]
            self.assertTrue(post.get("success"), post)
            self.assertIsNotNone(post.get("post_id"), post)
            self.assertTrue(isinstance(post.get("post_url", ""), str))
        finally:
            asyncio.run(uploader.close())
            asyncio.run(client.close())


if __name__ == "__main__":
    unittest.main()
