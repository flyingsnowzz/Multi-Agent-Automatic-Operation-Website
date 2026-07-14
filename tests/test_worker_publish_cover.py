import unittest

from scripts.publish_common import (
    cover_decision,
    is_forwarded_article,
    validate_cover_ready,
    validate_publish_prerequisites,
)


class TestWorkerPublishCoverDecision(unittest.TestCase):
    def test_forwarded_article_reuses_source_cover(self):
        item = {"article_id": 1, "title": "Original news", "content": "source content"}

        decision = cover_decision(
            item,
            existing_cover={"image_url": "https://generated.example/old.png"},
            source_image="https://source.example/cover.jpg",
            title="Original news",
        )

        self.assertTrue(is_forwarded_article(item))
        self.assertFalse(decision["should_generate"])
        self.assertEqual(decision["reason"], "forwarded_source_cover")
        self.assertEqual(decision["image_url"], "https://source.example/cover.jpg")

    def test_forwarded_article_without_source_cover_does_not_generate(self):
        decision = cover_decision(
            {"article_id": 2, "title": "No image", "description": "source description"},
            existing_cover={},
            source_image="",
            title="No image",
        )

        self.assertFalse(decision["should_generate"])
        self.assertEqual(decision["reason"], "forwarded_missing_source_cover")
        self.assertEqual(decision["image_url"], "")
        self.assertEqual(decision["image_local_path"], "")

    def test_rewritten_article_can_generate_when_no_cover_exists(self):
        item = {"article_id": 3, "title": "Rewrite", "content_md": "rewritten content"}

        decision = cover_decision(item, existing_cover={}, source_image="", title="Rewrite")

        self.assertFalse(is_forwarded_article(item))
        self.assertTrue(decision["should_generate"])
        self.assertEqual(decision["reason"], "rewritten_generate_cover")

    def test_rewritten_article_reuses_existing_generated_cover(self):
        item = {"article_id": 4, "content_md": "rewritten content"}

        decision = cover_decision(
            item,
            existing_cover={"image_url": "https://generated.example/cover.png"},
            source_image="https://source.example/cover.jpg",
            title="Rewrite",
        )

        self.assertFalse(decision["should_generate"])
        self.assertEqual(decision["reason"], "existing_cover")
        self.assertEqual(decision["image_url"], "https://generated.example/cover.png")

    def test_rewritten_article_ignores_existing_source_cover(self):
        item = {"article_id": 40, "content_md": "rewritten content"}

        decision = cover_decision(
            item,
            existing_cover={"image_url": "https://source.example/cover.jpg"},
            source_image="https://source.example/cover.jpg",
            title="Rewrite",
        )

        self.assertTrue(decision["should_generate"])
        self.assertEqual(decision["reason"], "rewritten_generate_cover")
        self.assertEqual(decision["image_url"], "")

    def test_rewritten_article_requires_generated_cover_before_publish(self):
        item = {"article_id": 5, "content_md": "rewritten content", "quality_after": 78.0}
        cover = cover_decision(item, existing_cover={}, source_image="https://source.example/cover.jpg", title="Rewrite")

        with self.assertRaisesRegex(RuntimeError, "rewritten_cover_missing"):
            validate_cover_ready(item, cover, featured_image="")

        validate_cover_ready(item, cover, featured_image="https://coze.example/new.jpeg")

    def test_rewritten_article_requires_rewrite_outputs_before_publish(self):
        with self.assertRaisesRegex(RuntimeError, "quality_after"):
            validate_publish_prerequisites(
                {"article_id": 6, "content_md": "rewritten content"},
                title="Rewrite",
                content="rewritten content",
            )

        validate_publish_prerequisites(
            {"article_id": 6, "content_md": "rewritten content", "quality_after": 76.0},
            title="Rewrite",
            content="rewritten content",
        )
