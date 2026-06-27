import unittest
from unittest.mock import patch

from agents.scoring_agent import AIArticleReview, TopicAgent, WeightSystem, summarize_crawler_topics


class FakeAIClient:
    def review_article(self, article, candidate_topics):
        return AIArticleReview(
            title_style_score=88,
            content_importance_score=96,
            is_notice=False,
            reason="AI认为这是一篇重要招生信息。",
        )


class LowValueAIClient:
    def review_article(self, article, candidate_topics):
        return AIArticleReview(
            title_style_score=42,
            content_importance_score=30,
            is_notice=True,
            reason="AI认为这是一篇低价值活动信息。",
        )


class TopicSummaryTest(unittest.TestCase):
    def test_summarize_crawler_topics_scores_articles(self):
        articles = [
            {
                "id": 1,
                "title": "2026年MBA招生简章发布",
                "keywords": "MBA,招生简章,报名",
                "description": "介绍MBA招生专业目录与报名流程",
                "category": 1,
                "college_name": "示例大学",
                "score": 82,
                "weight": 8,
                "views": 1200,
                "publish_date": "2026-06-01",
            },
            {
                "id": 2,
                "title": "MBA复试录取名单公示",
                "keywords": "MBA,复试,录取",
                "category": 2,
                "college_name": "示例大学",
                "score": 70,
                "weight": 6,
                "views": 300,
                "publish_date": "2026-05-10",
            },
            {
                "id": 3,
                "title": "EMBA调剂公告",
                "keywords": "EMBA,调剂",
                "category": 5,
                "college_name": "另一所大学",
                "score": 90,
                "weight": 9,
                "views": 900,
                "publish_date": "2026-06-10",
            },
        ]

        result = summarize_crawler_topics(articles, output_count=10)

        self.assertEqual(result["summary"]["article_count"], 3)
        self.assertEqual(result["summary"]["scored_count"], 3)
        self.assertNotIn("tier_counts", result["summary"])
        self.assertNotIn("topics", result)
        self.assertEqual(len(result["article_scores"]), 3)
        self.assertIn("title_style_score", result["article_scores"][0])
        self.assertIn("overall_score", result["article_scores"][0])
        self.assertIn("is_notice", result["article_scores"][0])
        self.assertIn("notice_score", result["article_scores"][0])
        self.assertIn("content_importance_score", result["article_scores"][0])
        self.assertIn("raw_content_importance_score", result["article_scores"][0])
        self.assertIn("freshness_score", result["article_scores"][0])
        self.assertIn("freshness_factor", result["article_scores"][0])
        self.assertNotIn("recommendation_tier", result["article_scores"][0])
        self.assertIn("topics", result["article_scores"][0])

    def test_weight_system_accepts_manual_scores(self):
        weights = WeightSystem(
            {
                "manual_quality": {"weight": 0.7},
                "manual_business_value": {"weight": 0.3},
            }
        )

        score = weights.score(
            {
                "manual_quality": 80,
                "manual_business_value": 60,
            }
        )

        self.assertEqual(score.total_score, 74)
        self.assertEqual(score.weights["manual_quality"], 0.7)

    def test_topic_agent_exposes_article_summary_entrypoint(self):
        agent = TopicAgent()
        result = agent.summarize_from_articles(
            [
                {
                    "id": 10,
                    "title": "MEM报名流程通知",
                    "keywords": "MEM,报名流程",
                    "score": 50,
                    "weight": 5,
                }
            ],
            manual_article_scores={
                10: {
                    "title_style_score": 95,
                    "length_score": 70,
                    "content_importance_score": 80,
                    "freshness_score": 90,
                }
            },
        )

        self.assertEqual(result["summary"]["article_count"], 1)
        self.assertGreater(result["article_scores"][0]["overall_score"], 0)

    def test_acronym_topic_matching_uses_boundaries(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 20,
                    "title": "EMBA调剂公告",
                    "keywords": "EMBA,调剂",
                    "score": 80,
                    "weight": 8,
                }
            ],
            output_count=10,
        )

        matched_topics = set(result["article_scores"][0]["topics"])
        self.assertIn("EMBA", matched_topics)
        self.assertNotIn("MBA", matched_topics)

    def test_short_but_important_article_is_not_discarded(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 30,
                    "title": "关于停止招收本科生的通知",
                    "keywords": "停止招收,通知",
                    "content": "经学校研究决定，自2026年起停止招收本科生。",
                    "category": 2,
                    "publish_date": "2026-06-10",
                }
            ],
            output_count=5,
            ai_client=FakeAIClient(),
        )

        score = result["article_scores"][0]
        self.assertGreaterEqual(score["content_importance_score"], 80)
        self.assertGreater(score["overall_score"], 0)

    def test_low_value_mismatched_short_article_scores_low(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 31,
                    "title": "活动回顾",
                    "keywords": "活动",
                    "content": "活动顺利举行。",
                }
            ],
            output_count=5,
            ai_client=LowValueAIClient(),
        )

        score = result["article_scores"][0]
        self.assertLess(score["content_importance_score"], 60)
        self.assertNotIn("length_score", score)

    def test_ai_client_can_enhance_article_scores(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 40,
                    "title": "重要招生政策调整",
                    "keywords": "招生,调整",
                    "content": "学校发布重要招生政策调整，请考生及时关注。",
                    "publish_date": "2026-06-10",
                }
            ],
            ai_client=FakeAIClient(),
        )

        score = result["article_scores"][0]
        self.assertTrue(score["ai_used"])
        self.assertEqual(score["ai_reason"], "AI认为这是一篇重要招生信息。")
        self.assertGreaterEqual(score["content_importance_score"], 80)
        self.assertFalse(score["is_notice"])
        self.assertEqual(score["notice_score"], 100)

    def test_ai_reviews_can_run_with_concurrency(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 401,
                    "title": "重要招生政策调整",
                    "content": "学校发布重要招生政策调整，请考生及时关注。",
                    "publish_date": "2026-06-10",
                },
                {
                    "id": 402,
                    "title": "教授科研心路故事",
                    "content": "教授分享多年科研探索、团队成长与突破背后的故事。",
                    "publish_date": "2026-06-10",
                },
            ],
            ai_client=FakeAIClient(),
            ai_concurrency=2,
        )

        self.assertEqual(result["summary"]["ai_used_count"], 2)
        self.assertEqual(len(result["article_scores"]), 2)

    def test_use_ai_without_api_key_skips_ai_review(self):
        with patch.dict(
            "os.environ",
            {
                "ARTICLE_SCORING_API_KEY": "",
                "DEEPSEEK_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            result = summarize_crawler_topics(
                [
                    {
                        "id": 41,
                        "title": "测试文章",
                        "keywords": "测试",
                        "content": "这是一篇用于测试无 API Key 时 AI mock 的文章。",
                    }
                ],
                use_ai=True,
            )

        score = result["article_scores"][0]
        self.assertFalse(score["ai_used"])
        self.assertIsNone(score["ai_reason"])
        self.assertIsNone(score["overall_score"])
        self.assertIsNone(score["title_style_score"])
        self.assertIsNone(score["content_importance_score"])
        self.assertIsNone(score["is_notice"])
        self.assertIsNone(score["notice_score"])
        self.assertIn("freshness_score", score)

    def test_recent_articles_ignore_freshness_weight_and_normalize_other_weights(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 45,
                    "title": "招生新闻",
                    "content": "学校发布招生新闻。",
                    "publish_date": "2026-06-20",
                }
            ],
            ai_client=FakeAIClient(),
        )

        score = result["article_scores"][0]
        self.assertFalse(score["freshness_weight_active"])
        self.assertIsNone(score["score_breakdown"]["freshness_score"])
        self.assertGreater(score["overall_score"], 80)

    def test_importance_is_penalized_by_freshness_factor(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 46,
                    "title": "往年招生政策调整",
                    "content": "学校发布重要招生政策调整。",
                    "publish_date": "2025-01-01",
                }
            ],
            ai_client=FakeAIClient(),
        )

        score = result["article_scores"][0]
        self.assertEqual(score["freshness_factor"], 0.5)
        self.assertEqual(score["raw_content_importance_score"], 96)
        self.assertAlmostEqual(score["content_importance_score"], 48)

    def test_recent_articles_get_higher_freshness_score(self):
        result = summarize_crawler_topics(
            [
                {
                    "id": 50,
                    "title": "最新招生政策通知",
                    "keywords": "招生,政策",
                    "content": "学校发布最新招生政策通知。",
                    "publish_date": "2026-06-20",
                },
                {
                    "id": 51,
                    "title": "往年招生政策通知",
                    "keywords": "招生,政策",
                    "content": "学校发布往年招生政策通知。",
                    "publish_date": "2020-01-01",
                },
            ]
        )

        scores = {item["article_id"]: item for item in result["article_scores"]}
        self.assertGreater(scores[50]["freshness_score"], scores[51]["freshness_score"])


if __name__ == "__main__":
    unittest.main()
