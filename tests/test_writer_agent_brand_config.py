import unittest


class _DummyResp:
    def __init__(self, content: str):
        self.content = content


class _DummyLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return _DummyResp(self._content)


class TestWriterAgentBrandConfig(unittest.TestCase):
    def test_brand_guide_path_is_resolved(self):
        from agents.writer_agent import WriterAgent

        agent = WriterAgent(llm=_DummyLLM("{}"))
        resolved = agent._resolve_brand_config({"brand_guide": "config/brand_guidelines.yaml"})
        self.assertIn("tone", resolved)
        self.assertIn("prohibited_words", resolved)
        self.assertIn("recommended_words", resolved)

