import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class _DummyLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return SimpleNamespace(content=self._content)


class TestLangGraphImageCMSFlow(unittest.TestCase):
    def _build_workflow(self):
        with patch("workflows.langgraph_workflow.ChatOpenAI", return_value=SimpleNamespace()):
            from workflows.langgraph_workflow import MultiAgentWorkflow

            return MultiAgentWorkflow(config_dir="agents", image_mode="plan_only")

    def test_image_node_writes_normalized_contract(self):
        wf = self._build_workflow()
        wf.llm = _DummyLLM(
            """
            {
              "featured_prompt": "campus skyline at sunset",
              "inline_images": [
                {"prompt": "students in classroom", "position": "section-1"}
              ]
            }
            """
        )
        state = {
            "topic": {"id": "topic-1", "title": "EMBA 选校", "primary_keyword": "EMBA"},
            "seo_result": {"meta_title": "T", "meta_description": "D"},
            "trace_id": "trace001",
        }

        out = wf._image_node(state)

        self.assertEqual(out["current_stage"], "image")
        self.assertIsNone(out["error"])
        self.assertEqual(out["image_result"]["featured_prompt"], "campus skyline at sunset")
        self.assertEqual(out["image_result"]["featured_image_url"], "")
        self.assertEqual(out["image_result"]["license"]["source"], "planned")
        self.assertEqual(out["image_result"]["license"]["provider"], "openai")
        self.assertEqual(len(out["image_result"]["inline_images"]), 1)
        self.assertEqual(out["image_result"]["inline_images"][0]["prompt"], "students in classroom")
        self.assertEqual(out["image_result"]["inline_images"][0]["position"], "section-1")
        self.assertEqual(out["image_result"]["inline_images"][0]["url"], "")
        self.assertEqual(out["image_result"]["inline_images"][0]["alt"], "")

    def test_cms_node_consumes_image_and_seo_results(self):
        wf = self._build_workflow()
        state = {
            "topic": {
                "id": "topic-2",
                "title": "EMBA 申请指南",
                "content_type": "guide",
                "secondary_keywords": ["EMBA项目", "EMBA报考条件"],
            },
            "write_result": {
                "article": {
                    "title": "旧标题",
                    "content_md": "# 内容",
                    "meta_description": "旧描述",
                }
            },
            "edit_result": {
                "article": {
                    "title": "编辑后标题",
                    "content_md": "# 编辑后内容",
                    "meta_description": "编辑后描述",
                }
            },
            "seo_result": {
                "optimized_article": {"title": "SEO 标题", "content": "<p>SEO 内容</p>"},
                "meta_title": "SEO Meta Title",
                "meta_description": "SEO Meta Description",
                "og_tags": {"og:title": "SEO 标题"},
                "twitter_tags": {"twitter:title": "SEO 标题"},
                "schema_json": {"@type": "Article"},
            },
            "image_result": {
                "featured_image_url": "https://img.example.com/cover.webp",
                "featured_alt": "EMBA 申请指南封面图",
                "featured_prompt": "cover prompt",
                "inline_images": [],
                "license": {"source": "planned", "provider": "openai"},
            },
            "trace_id": "trace002",
        }

        async def _fake_execute(*, article, page_info, images):
            self.assertEqual(article["title"], "SEO 标题")
            self.assertEqual(article["content"], "<p>SEO 内容</p>")
            self.assertEqual(article["meta"]["meta_title"], "SEO Meta Title")
            self.assertEqual(article["meta"]["meta_description"], "SEO Meta Description")
            self.assertEqual(page_info["category"], "guide")
            self.assertEqual(page_info["tags"], ["EMBA项目", "EMBA报考条件"])
            self.assertEqual(images["featured_image_url"], "https://img.example.com/cover.webp")
            self.assertEqual(images["featured_alt"], "EMBA 申请指南封面图")
            return {
                "status": "dry_run",
                "article_url": None,
                "payload": {"slug": "emba-guide"},
            }

        with patch("agents.cms_agent.cms_agent.CMSAgent.execute", new=AsyncMock(side_effect=_fake_execute)):
            out = wf._cms_node(state)

        self.assertEqual(out["current_stage"], "cms")
        self.assertIsNone(out["error"])
        self.assertEqual(out["cms_result"]["status"], "dry_run")
        self.assertIn("payload", out["cms_result"])


if __name__ == "__main__":
    unittest.main()
