import unittest


class _DummyResp:
    def __init__(self, content: str):
        self.content = content


class _DummyLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return _DummyResp(self._content)


class TestWriterAgentJsonExtract(unittest.IsolatedAsyncioTestCase):
    async def test_extract_from_code_block(self):
        from agents.writer_agent import WriterAgent

        materials = {"sources": [{"url": "https://example.com/s"}], "citations": []}
        payload = {
            "article": {
                "title": "T",
                "meta_description": "D",
                "content_md": "X\n\n## 参考来源\n- https://example.com/s\n",
            },
            "seo_analysis": {},
            "internal_links": [],
            "image_alt_texts": [],
            "statistics": {"word_count": 10, "reading_time_minutes": 1},
            "quality_checks": {},
            "warnings": [],
        }
        dummy = _DummyLLM(content="```json\n" + __import__("json").dumps(payload, ensure_ascii=False) + "\n```")
        agent = WriterAgent(llm=dummy)
        out = await agent.execute(topic={"title": "T", "min_word_count": 0, "max_word_count": 10000}, materials=materials)
        self.assertEqual(out.get("article", {}).get("title"), "T")

