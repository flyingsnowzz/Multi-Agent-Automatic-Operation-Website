import unittest
import asyncio

from agents.image_agent.tools.image_generator import ImageGenerator, OpenAIImageStyle
from agents.image_agent.tools.alt_text_generator import AltTextGenerator


class TestImageAgentTools(unittest.TestCase):
    def test_generate_without_api_key_returns_success_false(self):
        gen = ImageGenerator(api_key="")
        try:
            out = asyncio.run(gen.generate(prompt="x"))
            self.assertFalse(out.get("success"))
            self.assertIn("images", out)
        finally:
            asyncio.run(gen.close())

    def test_openai_style_enum_has_expected_values(self):
        self.assertEqual(OpenAIImageStyle.VIVID.value, "vivid")
        self.assertEqual(OpenAIImageStyle.NATURAL.value, "natural")

    def test_dalle_3_n_validation(self):
        gen = ImageGenerator(api_key="k")
        try:
            out = asyncio.run(gen.generate(prompt="x", model="dall-e-3", n=2))
            self.assertFalse(out.get("success"))
            self.assertEqual(out.get("error"), "dall_e_3_only_supports_n_1")
        finally:
            asyncio.run(gen.close())

    def test_alt_text_language_auto(self):
        g = AltTextGenerator()
        zh = g.generate("商务人士在办公室工作", keywords=["EMBA"], language="auto")
        self.assertIn("EMBA", zh.get("alt_text") or "")
        en = g.generate("A business professional reading a book in modern office", keywords=["EMBA"], language="auto")
        self.assertIn("EMBA", en.get("alt_text") or "")

    def test_format_html_escapes(self):
        g = AltTextGenerator()
        alt = {"src": 'x" onerror="alert(1)', "alt_text": 'a"b', "title": 't"t'}
        html_out = g.format_html(alt)
        self.assertNotIn('onerror="alert(1)', html_out)
        self.assertIn('src="#"', html_out)


if __name__ == "__main__":
    unittest.main()
