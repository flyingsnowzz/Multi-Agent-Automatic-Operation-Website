import unittest
import os
from unittest.mock import AsyncMock, patch

from workflows.langgraph_article_pipeline import (
    build_article_graph,
    _ensure_reference_section,
    _ensure_reprint_credit,
    _ensure_reprint_title,
    _strip_public_source_markers,
    image_node,
    route_after_quality,
    route_after_rewrite_quality,
    route_after_scoring,
    summarize_graph_result,
)


class LangGraphArticlePipelineTests(unittest.TestCase):
    # These tests focus on graph routing contracts. They avoid live model/API
    # calls so code review can quickly verify the business branch thresholds.
    def test_route_after_scoring_uses_threshold(self):
        """Verify AI score routing stops weak articles and keeps strong ones."""
        with patch.dict(os.environ, {"AI_SCORE_THRESHOLD": "75"}):
            self.assertEqual(route_after_scoring({"ai_score": 75}), "quality")
            self.assertEqual(route_after_scoring({"ai_score": 74.9}), "stop_low_score")
            self.assertEqual(route_after_scoring({"stop_reason": "source_missing"}), "stop")

    def test_route_after_quality_splits_direct_and_rewrite(self):
        """Verify quality routing either rewrites, publishes late stages, or stops."""
        with patch.dict(os.environ, {"QUALITY_PASS_THRESHOLD": "70"}):
            self.assertEqual(route_after_quality({"quality_score": 70, "run_late_stages": True}), "rewrite")
            self.assertEqual(route_after_quality({"quality_score": 71, "run_late_stages": True}), "seo")
            self.assertEqual(route_after_quality({"quality_score": 71, "run_late_stages": False}), "done")

    def test_route_after_rewrite_quality_requires_threshold(self):
        """Verify rewritten articles must pass the second quality threshold."""
        with patch.dict(os.environ, {"REWRITE_QUALITY_THRESHOLD": "70"}):
            self.assertEqual(route_after_rewrite_quality({"rewrite_quality_after": 69.9}), "rewrite_blocked")
            self.assertEqual(route_after_rewrite_quality({"rewrite_quality_after": 70}), "edit")
            self.assertEqual(route_after_rewrite_quality({"stop_reason": "writer_failed"}), "stop")

    def test_summarize_graph_result_is_compact(self):
        """Verify graph summaries keep key fields without dumping full payloads."""
        summary = summarize_graph_result(
            {
                "article_id": 1,
                "title": "T",
                "ai_score": 80,
                "quality_score": 72,
                "seo_meta_title": "SEO",
                "errors": ["x"],
            }
        )
        self.assertEqual(summary["article_id"], 1)
        self.assertEqual(summary["seo_meta_title"], "SEO")
        self.assertEqual(summary["errors"], ["x"])
        self.assertIn("cms_status", summary)

    def test_graph_compiles_when_langgraph_is_installed(self):
        """Verify graph construction produces an invokable compiled graph."""
        # Compilation catches broken node names or conditional edge targets
        # without running any external providers.
        graph = build_article_graph()
        self.assertTrue(hasattr(graph, "ainvoke"))

    def test_editor_reference_section_is_restored_when_removed(self):
        """Verify Editor output keeps Writer's source section."""
        generated = "正文。\n\n## 参考来源\n- https://example.com/source"
        edited = "编辑后的正文。"

        restored = _ensure_reference_section(edited, generated)

        self.assertIn("## 参考来源", restored)
        self.assertIn("https://example.com/source", restored)

    def test_forwarded_article_keeps_title_prefix_without_visible_credit(self):
        """Verify forwarded articles keep title prefix but no visible source credit."""
        title = _ensure_reprint_title("原始标题")
        content = _ensure_reprint_credit("正文第一段。", source_title="原始标题", source_url="https://example.com/original")

        self.assertEqual(title, "转载｜原始标题")
        self.assertEqual("正文第一段。", content)

    def test_forwarded_article_uses_raw_content_without_reprint_credit(self):
        """Verify raw HTML content can be converted before forwarded publishing."""
        from scripts.publish_common import normalize_forwarded_content_md

        raw = "<section><p>第一段。</p><p>第二段。</p><p>第三段。</p></section>"
        content = _ensure_reprint_credit(
            normalize_forwarded_content_md(raw),
            source_title="原始标题",
            source_url="https://example.com/original",
        )

        self.assertIn("第一段。\n\n第二段。\n\n第三段。", content)
        self.assertNotIn("转载来源", content)

    def test_public_source_markers_are_stripped_before_cms(self):
        """Verify published body hides reprint and reference source markers."""
        content = "正文。\n\n> 转载来源：[原文](https://example.com)\n\n## 参考来源\n- https://example.com/source"

        cleaned = _strip_public_source_markers(content)

        self.assertEqual(cleaned, "正文。")

    def test_image_node_falls_back_to_source_cover_on_provider_failure(self):
        """Verify image provider outages do not block articles with source covers."""
        import asyncio

        class FailingProvider:
            async def generate(self, *, prompt, n):
                return {"success": False, "error": "insufficient_balance"}

            async def close(self):
                return None

        state = {
            "article_id": 24165,
            "title": "Rewrite title",
            "source_image": "https://source.example/cover.jpg",
            "edited_title": "Rewrite title",
            "edited_content_md": "rewritten body",
            "quality_after": 86.5,
        }

        with patch.dict(os.environ, {"PROMPT_AUDIT_ENABLED": "false", "IMAGE_PROMPT_LLM_ENABLED": "false"}):
            with patch("workflows.langgraph_article_pipeline.fetch_existing_cover", new=AsyncMock(return_value={})):
                with patch("agents.image_agent.tools.provider_factory.get_image_provider", return_value=FailingProvider()):
                    result = asyncio.run(image_node(state))

        self.assertNotIn("stop_reason", result)
        self.assertEqual(result["image_url"], "https://source.example/cover.jpg")
        self.assertIn("image_generation_failed_fallback_to_source:insufficient_balance", result["warnings"])

    def test_image_node_falls_back_to_source_cover_on_missing_image_error(self):
        """Verify missing-image provider responses reuse the original cover."""
        import asyncio

        class MissingImageProvider:
            async def generate(self, *, prompt, n):
                return {"success": False, "error": "missing_cover_image"}

            async def close(self):
                return None

        state = {
            "article_id": 24166,
            "title": "Rewrite title",
            "source_image": "https://source.example/original.jpg",
            "edited_title": "Rewrite title",
            "edited_content_md": "rewritten body",
            "quality_after": 86.5,
        }

        with patch.dict(
            os.environ,
            {
                "PROMPT_AUDIT_ENABLED": "false",
                "IMAGE_PROMPT_LLM_ENABLED": "false",
                "IMAGE_FALLBACK_TO_SOURCE_ON_GENERATION_FAILURE": "false",
            },
        ):
            with patch("workflows.langgraph_article_pipeline.fetch_existing_cover", new=AsyncMock(return_value={})):
                with patch("agents.image_agent.tools.provider_factory.get_image_provider", return_value=MissingImageProvider()):
                    result = asyncio.run(image_node(state))

        self.assertNotIn("stop_reason", result)
        self.assertEqual(result["image_url"], "https://source.example/original.jpg")
        self.assertIn("image_generation_failed_fallback_to_source:missing_cover_image", result["warnings"])

    def test_image_node_uses_llm_generated_cover_prompt(self):
        """Verify rewritten articles send the generated cover prompt to provider."""
        import asyncio

        calls = []

        class RecordingProvider:
            async def generate(self, *, prompt, n):
                calls.append(prompt)
                return {
                    "success": True,
                    "images": [{"url": "https://image.example/cover.jpg", "local_path": "", "run_id": "run-1", "index": 0}],
                }

            async def close(self):
                return None

        state = {
            "article_id": 24167,
            "title": "Original title",
            "edited_title": "Rewrite title",
            "edited_content_md": "这是一篇关于商学院数字化转型的重写文章。",
            "quality_after": 86.5,
        }

        prompt_result = {
            "prompt": "现代商学院课堂与数据屏幕结合的新闻摄影风封面，真实光线，专业构图，无文字无Logo",
            "used_llm": True,
            "reason": "ok",
            "request_prompt": "request",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "model": "deepseek-v4-flash",
        }

        with patch.dict(os.environ, {"PROMPT_AUDIT_ENABLED": "false"}):
            with patch("workflows.langgraph_article_pipeline.fetch_existing_cover", new=AsyncMock(return_value={})):
                with patch("workflows.langgraph_article_pipeline._generate_cover_prompt_with_llm", new=AsyncMock(return_value=prompt_result)):
                    with patch("agents.image_agent.tools.provider_factory.get_image_provider", return_value=RecordingProvider()):
                        result = asyncio.run(image_node(state))

        self.assertEqual(calls, [prompt_result["prompt"]])
        self.assertEqual(result["image_prompt"], prompt_result["prompt"])
        self.assertEqual(result["image_url"], "https://image.example/cover.jpg")


if __name__ == "__main__":
    unittest.main()
