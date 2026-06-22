import unittest

from agents.seo_agent.tools.schema_generator import SchemaGenerator


class TestSEOAgentSchemaGenerator(unittest.TestCase):
    def test_blog_type_mapping(self):
        g = SchemaGenerator()
        schema = g.generate(
            {
                "title": "t",
                "meta_description": "d",
                "featured_image_url": "https://example.com/i.png",
                "logo_url": "https://example.com/logo.png",
                "url": "https://example.com/post",
                "published_date": "2026-06-09T09:00:00+08:00",
                "modified_date": "2026-06-09T09:00:00+08:00",
            },
            schema_type="blog",
        )
        self.assertEqual(schema.get("@type"), "BlogPosting")

    def test_strict_validation_fails_on_bad_date(self):
        g = SchemaGenerator()
        schema = g.generate(
            {
                "title": "t",
                "meta_description": "d",
                "featured_image_url": "https://example.com/i.png",
                "logo_url": "https://example.com/logo.png",
                "url": "https://example.com/post",
                "published_date": "bad-date",
            },
            schema_type="article",
        )
        v = g.validate_schema(schema)
        self.assertFalse(v.get("valid"))
        self.assertTrue(any("datePublished" in e for e in (v.get("errors") or [])))


if __name__ == "__main__":
    unittest.main()

