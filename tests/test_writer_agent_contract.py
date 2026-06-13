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


class TestWriterAgentContract(unittest.IsolatedAsyncioTestCase):
    async def test_contract_fields_present(self):
        from agents.writer_agent import WriterAgent

        materials = {"sources": [{"url": "https://example.com/s"}], "citations": []}
        content_md = "A.\n\nB.\n\n## 参考来源\n- https://example.com/s\n"
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
        self.assertIn("article", out)
        self.assertIn("content_md", out.get("article") or {})
        self.assertIn("statistics", out)
        self.assertIn("quality_checks", out)
        self.assertIn("warnings", out)

    async def test_context_exposes_research_brief_guidance(self):
        from agents.writer_agent import WriterAgent

        agent = WriterAgent(llm=_DummyLLM(content=json.dumps({
            "article": {"title": "T", "content_md": "A\n\n## 参考来源\n- https://example.com/s", "meta_description": "D"},
            "seo_analysis": {},
            "internal_links": [],
            "image_alt_texts": [],
            "statistics": {"word_count": 10, "reading_time_minutes": 1},
            "quality_checks": {},
            "warnings": [],
        }, ensure_ascii=False)))
        context = agent._context(
            topic={
                "title": "EMBA报考条件详解",
                "primary_keyword": "EMBA报考条件",
                "secondary_keywords": ["EMBA申请流程"],
                "content_type": "guide",
                "search_intent": "informational",
            },
            outline=None,
            materials={
                "research_brief": {
                    "source_snapshot": {
                        "source_title": "EMBA 报考条件",
                        "source_summary": "申请前通常需要满足工作年限与管理经验要求。",
                    },
                    "source_highlights": ["需要一定工作年限", "申请流程包括材料准备与面试"],
                    "key_facts": [{"fact": "不同项目具体要求存在差异"}],
                    "rewrite_constraints": ["不要把项目差异写成统一标准"],
                    "risk_points": ["证据不足时避免确定性表述"],
                    "suggested_sections": ["适用人群", "申请流程"],
                    "writer_outline": {
                        "sections": [
                            {"title": "适用人群", "key_points": ["工作背景", "管理经验"]},
                            {"title": "申请流程", "key_points": ["材料准备", "时间安排"]},
                        ]
                    },
                },
                "sources": [{"url": "https://example.com/s"}],
            },
            brand_config={},
        )
        self.assertIn("工作年限与管理经验要求", context["research_brief_summary"])
        self.assertIn("不要把项目差异写成统一标准", context["research_brief_constraints"])
        self.assertIn("证据不足时避免确定性表述", context["research_brief_risk_points"])
        self.assertIn("适用人群", context["research_brief_suggested_sections"])
        self.assertIn("申请流程", context["research_brief_writer_outline"])
        self.assertIsInstance(context["hierarchy_outline"], dict)
