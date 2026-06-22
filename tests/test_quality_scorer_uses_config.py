import unittest

from agents.editor_agent.tools.quality_scorer import QualityScorer


class TestQualityScorerUsesConfig(unittest.TestCase):
    def test_pass_threshold_and_prohibited_words(self):
        cfg = {
            "quality": {"pass_threshold": 90},
            "quality_scoring": {
                "weights": {
                    "content_quality": 0.3,
                    "logical_clarity": 0.2,
                    "language_expression": 0.2,
                    "seo_optimization": 0.15,
                    "brand_consistency": 0.15,
                }
            },
            "brand_consistency": {"prohibited_words": {"enabled": True, "action": "flag", "words": ["绝对"]}},
        }
        scorer = QualityScorer(config=cfg)
        out = scorer.score({"title": "t" * 12, "content_md": "这是一个绝对好的文章。", "primary_keyword": "文章", "meta_description": "x" * 160})
        self.assertTrue(out.get("success"))
        self.assertFalse(out.get("pass"))
        issues = out.get("issues_found") or []
        self.assertTrue(any(i.get("type") == "禁用词" for i in issues if isinstance(i, dict)))


if __name__ == "__main__":
    unittest.main()

