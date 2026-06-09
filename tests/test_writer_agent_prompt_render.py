import unittest
import json


class _DummyResp:
    def __init__(self, content: str):
        self.content = content


class _DummyLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return _DummyResp(self._content)


class TestWriterAgentPromptRender(unittest.IsolatedAsyncioTestCase):
    async def test_no_placeholder_left(self):
        from agents.writer_agent import WriterAgent

        materials = {
            "sources": [{"url": "https://example.com/source"}],
            "citations": [],
            "outline": {"h1": "t", "sections": []},
        }
        content_md = "第一段。\n\n第二段。\n\n## 参考来源\n- https://example.com/source\n"
        payload = {
            "article": {"title": "T", "content_md": content_md, "meta_description": "D"},
            "seo_analysis": {},
            "internal_links": [],
            "image_alt_texts": [],
            "statistics": {"word_count": 10, "reading_time_minutes": 1},
            "quality_checks": {},
            "warnings": [],
        }
        dummy = _DummyLLM(content=json.dumps(payload, ensure_ascii=False))
        agent = WriterAgent(llm=dummy)
        out = await agent.execute(topic={"title": "T", "min_word_count": 0, "max_word_count": 10000}, materials=materials)
        self.assertNotIn("prompt_placeholders_not_filled", out.get("warnings") or [])
