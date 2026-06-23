import unittest

from agents.topic_agent import summarize_crawler_topics


DUMMY_ARTICLES = [
    {
        "id": 101,
        "title": "2026年MBA招生简章：报名条件、招生计划与学费说明",
        "keywords": "MBA,招生简章,报名条件,招生计划,学费",
        "description": "完整介绍2026年MBA招生政策。",
        "content": "2026年MBA招生简章公布，包含报名条件、招生计划、学费标准、奖学金政策、复试安排等内容。" * 40,
        "category": 1,
        "college_name": "示例商学院",
        "publish_date": "2026-06-01",
    },
    {
        "id": 102,
        "title": "关于停止招收本科生的通知",
        "keywords": "停止招收,招生,通知",
        "description": "自2026年起停止招收本科生。",
        "content": "经学校研究决定，自2026年起停止招收本科生。",
        "category": 2,
        "college_name": "示例大学",
        "publish_date": "2026-06-10",
    },
    {
        "id": 103,
        "title": "2026年硕士研究生调剂公告",
        "keywords": "调剂,调剂公告,调剂名额,调剂系统",
        "description": "部分专业接收调剂考生。",
        "content": "根据招生计划，部分专业接收调剂考生。调剂系统开放时间、调剂名额、复试安排将陆续公布。" * 18,
        "category": 5,
        "college_name": "示例大学",
        "publish_date": "2026-04-02",
    },
    {
        "id": 104,
        "title": "复试名单公示",
        "keywords": "复试名单,复试,公示",
        "description": "复试名单公示。",
        "content": "复试名单现予以公示，请考生按要求参加资格审查。",
        "category": 2,
        "college_name": "示例大学",
        "publish_date": "2026-03-20",
    },
    {
        "id": 105,
        "title": "活动回顾",
        "keywords": "活动",
        "description": "活动回顾。",
        "content": "活动顺利举行。",
        "category": 2,
        "college_name": "示例大学",
        "publish_date": "2026-05-01",
    },
    {
        "id": 106,
        "title": "学院新闻",
        "keywords": "新闻,会议",
        "description": "学院召开工作会议。",
        "content": "学院召开工作会议，参会人员进行了交流发言。",
        "category": 2,
        "college_name": "示例大学",
        "publish_date": "2026-05-08",
    },
    {
        "id": 107,
        "title": "2026年考试科目调整说明",
        "keywords": "考试科目,调整,参考书目",
        "description": "考试科目发生调整。",
        "content": "根据培养方案调整，2026年硕士研究生招生考试部分专业考试科目和参考书目发生调整，请考生及时查看。" * 12,
        "category": 2,
        "college_name": "示例大学",
        "publish_date": "2026-02-10",
    },
    {
        "id": 108,
        "title": "2026年MBA报考条件变化：哪些考生需要重点关注",
        "keywords": "MBA,报考条件,申请条件,报考资格",
        "description": "说明MBA报考条件变化。",
        "content": "本文梳理2026年MBA报考条件、申请条件、报考资格、工作年限要求以及材料准备建议。" * 30,
        "category": 2,
        "college_name": "示例商学院",
        "publish_date": "2026-06-11",
    },
]


class TopicSummaryDummyDataTest(unittest.TestCase):
    def test_dummy_data_returns_article_score_dimensions(self):
        result = summarize_crawler_topics(DUMMY_ARTICLES, output_count=15)
        first_score = result["article_scores"][0]

        self.assertIn("overall_score", first_score)
        self.assertIn("title_style_score", first_score)
        self.assertIn("length_score", first_score)
        self.assertIn("content_importance_score", first_score)
        self.assertIn("freshness_score", first_score)
        self.assertNotIn("recommendation_tier", first_score)

    def test_short_important_articles_are_kept(self):
        result = summarize_crawler_topics(DUMMY_ARTICLES, output_count=10)
        scores = {item["article_id"]: item for item in result["article_scores"]}

        self.assertGreaterEqual(scores[102]["content_importance_score"], 80)
        self.assertGreater(scores[102]["overall_score"], 0)
        self.assertGreaterEqual(scores[104]["content_importance_score"], 70)
        self.assertGreater(scores[104]["overall_score"], 0)

    def test_low_value_articles_score_lower_on_importance(self):
        result = summarize_crawler_topics(DUMMY_ARTICLES, output_count=10)
        scores = {item["article_id"]: item for item in result["article_scores"]}

        self.assertLess(scores[105]["content_importance_score"], scores[102]["content_importance_score"])
        self.assertLess(scores[106]["content_importance_score"], scores[102]["content_importance_score"])

    def test_article_scores_keep_matched_topics_for_explanation(self):
        result = summarize_crawler_topics(DUMMY_ARTICLES, output_count=15)
        scores = {item["article_id"]: item for item in result["article_scores"]}

        self.assertNotIn("topics", result)
        self.assertIn("招生简章", scores[101]["topics"])
        self.assertIn("调剂信息", scores[103]["topics"])
        self.assertIn("考试大纲", scores[107]["topics"])
        self.assertIn("报考条件", scores[108]["topics"])


if __name__ == "__main__":
    unittest.main()
