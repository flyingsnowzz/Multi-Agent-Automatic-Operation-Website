import unittest

from agents.quality_agent.tools.article_quality_scorer import (
    build_quality_output_payload,
    build_quality_prompt,
    route_by_quality,
    should_enter_quality,
    should_enter_research_writer,
    should_retry_writer_quality,
)


class QualityAgentTest(unittest.TestCase):
    def test_prompt_scores_writing_quality_not_topic_value(self):
        prompt = build_quality_prompt(
            {
                "id": 1,
                "title": "科大获准兴办新医学院",
                "content": "这是一篇短通稿。",
                "article_score": 88,
            }
        )

        self.assertIn("成稿质量", prompt)
        self.assertIn("不要因为学校/机构/事件重要就自动给高质量分", prompt)
        self.assertIn("word_count_score", prompt)
        self.assertIn("attractiveness_score", prompt)
        self.assertIn("ai_generated_probability", prompt)

    def test_quality_routing_thresholds(self):
        self.assertEqual(route_by_quality(69), "needs_research_writer")
        self.assertEqual(route_by_quality(70), "manual_review")
        self.assertEqual(route_by_quality(85), "ready_to_store")
        self.assertTrue(should_enter_quality(76))
        self.assertFalse(should_enter_quality(75))
        self.assertTrue(should_enter_research_writer(82, 66))
        self.assertFalse(should_enter_research_writer(82, 72))
        self.assertTrue(should_retry_writer_quality(84))
        self.assertFalse(should_retry_writer_quality(86))

    def test_build_quality_output_payload(self):
        payload = build_quality_output_payload(
            {
                "candidate_id": 3,
                "source_article_id": 36,
                "original_url": "https://example.edu/news/36",
                "article_score": 82,
                "title": "标题",
                "content": "正文",
            },
            {
                "quality_score": 88,
                "dimensions": {
                    "word_count_score": 100,
                    "fluency_score": 90,
                    "structure_score": 85,
                    "attractiveness_score": 82,
                    "ai_feel_score": 80,
                },
                "ai_generated_probability": 20,
                "route": "ready_to_store",
                "rewrite_feedback_prompt": "继续保持。",
            },
            source_kind="original",
            model="deepseek-chat",
        )

        self.assertEqual(payload["quality_status"], "scored")
        self.assertEqual(payload["candidate_id"], 3)
        self.assertEqual(payload["quality_score"], 88)
        self.assertEqual(payload["word_count_score"], 100)
        self.assertEqual(payload["attractiveness_score"], 82)
        self.assertEqual(payload["route"], "ready_to_store")
        self.assertEqual(payload["quality_model"], "deepseek-chat")


if __name__ == "__main__":
    unittest.main()
