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
            }
        )
        self.assertEqual(out["id"], "topic_001")
        self.assertEqual(out["title"], "标题")
        self.assertEqual(out["primary_keyword"], "主词")
        self.assertEqual(out["secondary_keywords"], ["次词1", "次词2"])
        self.assertEqual(out["content_type"], "guide")
        self.assertEqual(out["min_word_count"], 1500)
        self.assertEqual(out["max_word_count"], 3000)


if __name__ == "__main__":
    unittest.main()

