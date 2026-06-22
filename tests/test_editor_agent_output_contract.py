import asyncio
import unittest

from agents.editor_agent import EditorAgent


class TestEditorAgentOutputContract(unittest.TestCase):
    def test_execute_returns_article_and_aliases(self):
        async def run():
            agent = EditorAgent()
            out = await agent.execute(
                article={"title": "标题" * 4, "content_md": "这是一个绝对好的文章。" + ("内容" * 400), "meta_description": "x" * 160},
                topic={"primary_keyword": "文章", "content_type": "blog"},
                dry_run=True,
            )
            self.assertTrue(out.get("success"))
            self.assertIn("article", out)
            self.assertIn("content_md", out["article"])
            self.assertIn("reviewed_article", out)
            self.assertIn("revised_article", out)
            self.assertIn(out.get("approval_status"), {"approved", "conditional", "rejected"})

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

