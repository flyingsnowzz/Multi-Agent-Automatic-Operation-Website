import unittest

from workflows.topic_to_hybrid_adapter import select_best_topic, topic_item_to_hybrid_topic


class TestTopicToHybridAdapter(unittest.TestCase):
    def test_select_best_topic_by_priority_score(self):
        picked = select_best_topic(
            {
                "topics": [
                    {"id": "1", "priority_score": 10},
                    {"id": "2", "priority_score": 99},
                    {"id": "3", "priority_score": 50},
                ]
            }
        )
        self.assertIsNotNone(picked)
        self.assertEqual(picked["id"], "2")

    def test_topic_item_to_hybrid_topic_contract(self):
        out = topic_item_to_hybrid_topic(
            {
                "id": "topic_001",
                "title": "标题",
                "target_keywords": ["主词", "次词1", "次词2"],
                "content_type": "guide",
                "priority": "high",
                "priority_score": 88.5,
                "candidate_id": "cand_999",
                "route_tier": "rewrite_candidate",
                "rewrite_required": True,
                "publish_candidate": False,
                "material_score": 75.0,
                "workflow_route": "full_rewrite_flow",
                "source_title": "源标题",
                "source_url": "https://example.com/source",
                "source_summary": "摘要内容",
            }
        )
        self.assertEqual(out["id"], "topic_001")
        self.assertEqual(out["title"], "标题")
        self.assertEqual(out["primary_keyword"], "主词")
        self.assertEqual(out["secondary_keywords"], ["次词1", "次词2"])
        self.assertEqual(out["content_type"], "guide")
        self.assertEqual(out["min_word_count"], 1500)
        self.assertEqual(out["max_word_count"], 3000)
        self.assertEqual(out["source_record_id"], "cand_999")
        self.assertEqual(out["route_tier"], "rewrite_candidate")
        self.assertEqual(out["rewrite_required"], True)
        self.assertEqual(out["publish_candidate"], False)
        self.assertEqual(out["material_score"], 75.0)
        self.assertEqual(out["workflow_route"], "full_rewrite_flow")
        self.assertEqual(out["source_title"], "源标题")
        self.assertEqual(out["source_url"], "https://example.com/source")
        self.assertEqual(out["source_summary"], "摘要内容")

    def test_topic_item_to_hybrid_topic_publish_candidate_route(self):
        out = topic_item_to_hybrid_topic(
            {
                "id": "topic_002",
                "title": "轻发布标题",
                "target_keywords": ["轻发布主词"],
                "content_type": "guide",
                "priority": "medium",
                "priority_score": 72.0,
                "candidate_id": "cand_1000",
                "route_tier": "publish_candidate",
                "rewrite_required": False,
                "publish_candidate": True,
                "material_score": 88.0,
                "workflow_route": "light_publish_flow",
                "source_title": "原始标题",
                "source_url": "https://example.com/publish",
                "source_summary": "摘要",
            }
        )
        self.assertEqual(out["route_tier"], "publish_candidate")
        self.assertFalse(out["rewrite_required"])
        self.assertTrue(out["publish_candidate"])
        self.assertEqual(out["workflow_route"], "light_publish_flow")


if __name__ == "__main__":
    unittest.main()
