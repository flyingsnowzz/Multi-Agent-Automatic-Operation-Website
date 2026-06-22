import unittest


class TestReadabilityChecker(unittest.TestCase):
    def test_chinese_paragraph_count(self):
        from agents.writer_agent.tools.readability_checker import ReadabilityChecker

        text = "第一段内容。\n\n第二段内容。"
        r = ReadabilityChecker().check(text, language="chinese")
        self.assertGreaterEqual(r.paragraph_count, 2)

    def test_english_sentence_split_and_complex_word_threshold(self):
        from agents.writer_agent.tools.readability_checker import ReadabilityChecker

        text = (
            "This is a short sentence. "
            "This is another short sentence. "
            "Internationalization is hard. "
            "But clarity matters."
        )
        r = ReadabilityChecker().check(text, language="english")
        self.assertLessEqual(r.avg_sentence_length, 15)
        self.assertNotIn("Too many complex words", r.issues)

