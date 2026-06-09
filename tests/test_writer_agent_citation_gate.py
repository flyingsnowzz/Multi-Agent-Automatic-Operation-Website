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


class TestWriterAgentCitationGate(unittest.IsolatedAsyncioTestCase):
    async def test_missing_citation_backlink_triggers_warning(self):
        from agents.writer_agent import WriterAgent

        materials = {"sources": [{"url": "https://example.com/s"}], "citations": []}
        content_md = "正文里没有引用。\n\n## 参考来源\n- https://not-in-materials.example.com/x\n"
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
        self.assertIn("missing_citation_backlinks", out.get("warnings") or [])
