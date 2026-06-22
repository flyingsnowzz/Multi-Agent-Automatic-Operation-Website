import unittest


class TestEditorPromptHasContentPlaceholder(unittest.TestCase):
    def test_prompt_contains_content_placeholder(self):
        with open("agents/editor_agent/prompt.md", "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("{content}", text)


if __name__ == "__main__":
    unittest.main()

