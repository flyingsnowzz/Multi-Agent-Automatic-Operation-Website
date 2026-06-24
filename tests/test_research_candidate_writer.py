import unittest

from agents.research_agent.tools.research_candidate_writer import (
    ResearchCandidateDBWriter,
    build_research_candidate_payload,
    build_research_candidate_payloads,
    should_keep_research_candidate,
)


class ResearchCandidateWriterTest(unittest.TestCase):
    def test_keeps_75_to_90_score_article_with_url(self):
        article = {
            "id": 7,
            "title": "教授团队发现一种新的量子材料筛选方法",
            "original_url": "https://example.edu/news/7",
            "college_name": "示例大学",
            "publish_date": "2026-06-24",
        }
        score = {
            "article_id": 7,
            "overall_score": 82.5,
            "title_style_score": 78,
            "content_importance_score": 86,
            "is_notice": False,
        }

        keep, reason = should_keep_research_candidate(article, score)
        self.assertTrue(keep)
        self.assertIn("score_in_75_90", reason)

    def test_skips_notice_admin_or_missing_url(self):
        base = {
            "id": 8,
            "title": "研究团队发布新成果",
            "original_url": "https://example.edu/news/8",
        }

        self.assertFalse(should_keep_research_candidate(base, {"overall_score": 74})[0])
        self.assertFalse(should_keep_research_candidate(base, {"overall_score": 91})[0])
        self.assertFalse(
            should_keep_research_candidate(base, {"overall_score": 82, "is_notice": True})[0]
        )
        self.assertFalse(
            should_keep_research_candidate(
                {"id": 9, "title": "研究团队发布新成果"},
                {"overall_score": 82, "is_notice": False},
            )[0]
        )
        self.assertFalse(
            should_keep_research_candidate(
                {**base, "title": "关于2026年暑假值班安排的通知"},
                {"overall_score": 82, "is_notice": False},
            )[0]
        )

    def test_build_payload_stores_writer_prompt(self):
        article = {
            "id": 12,
            "title": "一位教授的十五年科研长跑",
            "original_url": "https://example.edu/news/12",
            "college_name": "示例大学",
            "specialty_name": "物理",
            "category": 2,
            "publish_date": "2026-06-20",
        }
        score = {
            "article_id": 12,
            "overall_score": 84,
            "title_style_score": 66,
            "content_importance_score": 90,
            "raw_content_importance_score": 90,
            "length_score": 75,
            "freshness_score": 100,
            "word_count": 420,
            "is_notice": False,
        }
        research_result = {
            "research_brief": {
                "writer_outline": {"template_id": "story_profile"},
                "writer_prompt": {
                    "prompt_type": "writer_prompt_from_research_brief",
                    "prompt_text": "请交给 WriterAgent 写作。",
                },
            }
        }

        payload = build_research_candidate_payload(article, score, research_result)

        self.assertEqual(payload["source_article_id"], 12)
        self.assertEqual(payload["original_url"], "https://example.edu/news/12")
        self.assertEqual(payload["article_score"], 84)
        self.assertEqual(payload["research_status"], "generated")
        self.assertEqual(payload["writer_prompt"], "请交给 WriterAgent 写作。")
        self.assertEqual(payload["writer_prompt_type"], "writer_prompt_from_research_brief")
        self.assertEqual(payload["research_brief"]["writer_outline"]["template_id"], "story_profile")

    def test_build_payloads_returns_skipped_reasons(self):
        result = build_research_candidate_payloads(
            [
                {
                    "id": 1,
                    "title": "教授团队发现一种新的量子材料筛选方法",
                    "original_url": "https://example.edu/news/1",
                    "article_overall_score": 80,
                    "article_is_notice": 0,
                },
                {
                    "id": 2,
                    "title": "关于会议安排的通知",
                    "original_url": "https://example.edu/news/2",
                    "article_overall_score": 80,
                    "article_is_notice": 0,
                },
            ]
        )

        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "unimportant_admin_title")

    def test_invalid_identifier_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_identifier"):
            ResearchCandidateDBWriter({"table": "research_article_candidates;drop"})


if __name__ == "__main__":
    unittest.main()
