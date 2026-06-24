import unittest

from agents.writer_agent.tools.article_generation_writer import (
    WriterArticleDB,
    build_writer_generation_prompt,
    build_writer_output_payload,
)


class WriterArticleGenerationWriterTest(unittest.TestCase):
    def test_build_writer_generation_prompt_includes_source_score_and_prompt(self):
        prompt = build_writer_generation_prompt(
            {
                "id": 3,
                "source_article_id": 36,
                "original_url": "https://example.edu/news/36",
                "title": "科大获准兴办新医学院",
                "article_score": 80.05,
                "research_brief": {
                    "word_count_instruction": {
                        "standard_min_words": 900,
                        "standard_max_words": 1200,
                        "target_word_count": 1100,
                        "is_notice": False,
                    }
                },
                "writer_prompt": "你是 WriterAgent，请输出 JSON。",
            }
        )

        self.assertIn('"candidate_id": 3', prompt)
        self.assertIn('"source_article_id": 36', prompt)
        self.assertIn('"article_score": 80.05', prompt)
        self.assertIn("content_md 必须写到 900-1200 字", prompt)
        self.assertIn("你是 WriterAgent，请输出 JSON。", prompt)

    def test_build_writer_output_payload_for_generated_article(self):
        payload = build_writer_output_payload(
            {
                "id": 3,
                "source_article_id": 36,
                "original_url": "https://example.edu/news/36",
                "title": "原题",
                "article_score": 80.05,
                "writer_prompt": "prompt",
            },
            {
                "article": {
                    "title": "新标题",
                    "meta_description": "摘要",
                    "content_md": "正文",
                },
                "quality_checks": {"ok": True},
                "warnings": [],
            },
            writer_model="deepseek-chat",
        )

        self.assertEqual(payload["candidate_id"], 3)
        self.assertEqual(payload["generation_status"], "generated")
        self.assertEqual(payload["generated_title"], "新标题")
        self.assertEqual(payload["generated_content_md"], "正文")
        self.assertEqual(payload["writer_model"], "deepseek-chat")

    def test_build_writer_output_payload_for_failed_generation(self):
        payload = build_writer_output_payload(
            {
                "id": 3,
                "source_article_id": 36,
                "original_url": "https://example.edu/news/36",
                "title": "原题",
                "article_score": 80.05,
                "writer_prompt": "prompt",
            },
            None,
            writer_model="deepseek-chat",
            error_message="writer_agent_api_key_missing",
        )

        self.assertEqual(payload["generation_status"], "failed")
        self.assertEqual(payload["error_message"], "writer_agent_api_key_missing")

    def test_invalid_table_identifier_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_identifier"):
            WriterArticleDB({"output_table": "writer_article_outputs;drop"})


if __name__ == "__main__":
    unittest.main()
