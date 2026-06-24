import json
import unittest

from agents.topic_agent.tools.article_score_writer import (
    ArticleScoreDBWriter,
    build_article_score_update_payload,
)


class ArticleScoreWriterTest(unittest.TestCase):
    def test_build_article_score_update_payload_serializes_json_fields(self):
        payload = build_article_score_update_payload(
            {
                "article_id": 12,
                "overall_score": 82.5,
                "title_style_score": 80,
                "is_notice": False,
                "notice_score": 100,
                "content_importance_score": 85,
                "raw_content_importance_score": 95,
                "freshness_score": 65,
                "freshness_factor": 0.8,
                "freshness_weight_active": True,
                "score_breakdown": {"title_style_score": 20.0},
                "word_count": 900,
                "topic_count": 2,
                "topics": ["招生简章", "报名流程"],
                "reasons": ["标题较新颖"],
                "ai_used": True,
                "ai_reason": "AI reason",
            },
            model="deepseek-chat",
        )

        self.assertEqual(payload["article_id"], 12)
        self.assertEqual(payload["article_ai_used"], 1)
        self.assertEqual(payload["article_is_notice"], 0)
        self.assertEqual(payload["article_notice_score"], 100)
        self.assertNotIn("article_length_score", payload)
        self.assertEqual(payload["article_raw_content_importance_score"], 95)
        self.assertEqual(payload["article_freshness_factor"], 0.8)
        self.assertEqual(payload["article_freshness_weight_active"], 1)
        self.assertEqual(payload["article_scoring_model"], "deepseek-chat")
        self.assertEqual(json.loads(payload["article_topics"]), ["招生简章", "报名流程"])
        self.assertEqual(json.loads(payload["article_score_breakdown"]), {"title_style_score": 20.0})

    def test_invalid_table_identifier_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_identifier"):
            ArticleScoreDBWriter({"table": "crawler_news_main;drop table x"})


if __name__ == "__main__":
    unittest.main()
