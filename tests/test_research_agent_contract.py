import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch


class TestResearchAgentContract(unittest.TestCase):
    def test_research_agent_importable(self):
        from agents.research_agent import ResearchAgent

        self.assertTrue(callable(ResearchAgent))

    def test_execute_mock_contract_fields(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        topic = {
            "title": "2026年EMBA报考条件详解：适合人群、申请流程与准备建议",
            "primary_keyword": "EMBA报考条件",
            "secondary_keywords": ["EMBA申请流程", "EMBA院校怎么选"],
            "content_type": "guide",
            "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
            "search_intent": "informational",
            "outline_points": ["报考条件", "申请流程", "院校选择"],
        }
        out = asyncio.run(agent.execute(topic=topic, mode="mock"))

        self.assertIsInstance(out, dict)
        self.assertIsInstance(out.get("background"), dict)
        for k in ("statistics", "cases", "quotes", "sources", "citations", "warnings"):
            self.assertIsInstance(out.get(k), list)
        self.assertIsInstance(out.get("outline"), dict)
        self.assertIsInstance((out.get("outline") or {}).get("sections"), list)
        self.assertGreaterEqual(len(out["outline"]["sections"]), 3)
        for section in out["outline"]["sections"]:
            self.assertIsInstance(section, dict)
            self.assertIsInstance(section.get("title"), str)
            self.assertNotIn("报考条件报考条件", section.get("title"))
            self.assertNotIn("读EMBA报", section.get("title"))
            self.assertIsInstance(section.get("key_points"), list)
            self.assertGreater(len(section.get("key_points")), 0)
            self.assertEqual(section.get("notes"), "mock")

        expected_citation_keys = {"title", "url", "source", "authority", "citation", "note"}
        for item in out.get("citations") or []:
            self.assertIsInstance(item, dict)
            self.assertEqual(set(item.keys()), expected_citation_keys)
            self.assertIsInstance(item.get("title"), str)
            self.assertIsInstance(item.get("url"), str)
            self.assertEqual(item.get("source"), "mock_source")
            self.assertEqual(item.get("authority"), "low")
            self.assertIsInstance(item.get("citation"), str)
            self.assertEqual(item.get("note"), "mock_source")
        self.assertTrue(out.get("is_mock"))
        self.assertEqual(out.get("data_confidence"), "low")
        json.dumps(out, ensure_ascii=False)

    def test_missing_topic_fields_does_not_crash(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        out = asyncio.run(agent.execute(topic={"title": "EMBA"}, mode="mock"))
        self.assertIsInstance(out.get("warnings"), list)
        self.assertTrue(any("missing_topic_field" in str(x) for x in out.get("warnings")))

    def test_rewrite_candidate_returns_rule_based_research_brief_without_collector(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "rewrite_required": True,
            "publish_candidate": False,
            "topic_id": "topic_123",
            "candidate_id": 123,
            "title": "2026年EMBA报考条件详解：适合人群、申请流程与准备建议",
            "primary_keyword": "EMBA报考条件",
            "secondary_keywords": ["EMBA申请流程"],
            "target_keywords": ["EMBA报考条件", "EMBA申请流程"],
            "search_intent": "informational",
            "content_type": "guide",
            "content_angle": "conditions",
            "source_title": "EMBA 报考条件",
            "source_summary": "EMBA报考通常需要一定工作年限与管理经验，申请前需准备材料并关注时间线。",
            "source_url": "https://example.com/source",
            "source_content": "EMBA报考通常需要一定工作年限与管理经验。申请流程包括材料准备、面试与时间安排。不同项目的具体要求可能存在差异，申请者应以项目官方口径为准。",
            "material_score": 75.0,
            "evaluation": {"source_ok": True, "has_risk": False},
            "dedup": {"similarity_score": 0.2},
            "routing_payload": {"original_key": "val"},
        }

        with patch("agents.research_agent.research_agent.DataCollector.collect", new=AsyncMock(side_effect=AssertionError("collector should not be called"))):
            out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))

        self.assertIsInstance(out, dict)
        self.assertIn("research_brief", out)
        brief = out["research_brief"]
        self.assertEqual(brief["brief_type"], "rewrite_candidate_research_brief")
        self.assertEqual(brief["workflow_route"], "full_rewrite_flow")
        self.assertEqual(brief["route_tier"], "rewrite_candidate")
        self.assertEqual(brief["topic_id"], "topic_123")
        self.assertEqual(brief["candidate_id"], 123)
        self.assertEqual(brief["primary_keyword"], "EMBA报考条件")
        self.assertEqual(brief["target_keywords"], ["EMBA报考条件", "EMBA申请流程"])
        self.assertIn("source_snapshot", brief)
        self.assertIn("source_highlights", brief)
        self.assertIn("key_facts", brief)
        self.assertIn("risk_points", brief)
        self.assertIn("rewrite_constraints", brief)
        self.assertIn("title_instruction", brief)
        self.assertIn("word_count_instruction", brief)
        self.assertIn("style_instruction", brief)
        self.assertIn("writer_outline", brief)
        self.assertIn("writer_prompt", brief)
        self.assertIsInstance(brief["source_highlights"], list)
        self.assertIsInstance(brief["key_facts"], list)
        self.assertIsInstance(brief["risk_points"], list)
        self.assertIsInstance(brief["rewrite_constraints"], list)
        self.assertEqual(brief["style_instruction"]["style_id"], "human_editorial_feature")
        self.assertIsInstance(brief["writer_prompt"].get("prompt_text"), str)
        self.assertIn("WriterAgent", brief["writer_prompt"]["prompt_text"])
        self.assertIn("不要像是在逐条执行提示词", brief["writer_prompt"]["prompt_text"])
        self.assertIn("不要刻意制造“长段+单句短段”的节奏", brief["writer_prompt"]["prompt_text"])
        self.assertIn("非对称写作思维", brief["writer_prompt"]["prompt_text"])
        self.assertIn("保留一点颗粒感", brief["writer_prompt"]["prompt_text"])
        self.assertIn("对于……对于……", brief["writer_prompt"]["prompt_text"])
        self.assertIn("不要按“人物引入→获奖→领域介绍", brief["writer_prompt"]["prompt_text"])
        self.assertIn("少写路标句", brief["writer_prompt"]["prompt_text"])
        self.assertIn("地图、灯塔、航程", brief["writer_prompt"]["prompt_text"])
        self.assertIn("少解释学科，多写人", brief["writer_prompt"]["prompt_text"])
        self.assertIn("从理论论文发表到实验验证", brief["writer_prompt"]["prompt_text"])
        self.assertIn("信息密度不均衡", brief["writer_prompt"]["prompt_text"])
        self.assertIn("这些词不是绝对禁止", brief["writer_prompt"]["prompt_text"])
        self.assertNotIn("article", out)
        self.assertNotIn("cms_result", out)
        self.assertIn("outline", out)
        self.assertIn("sources", out)
        self.assertIn("citations", out)
        self.assertIsInstance((out.get("outline") or {}).get("sections"), list)
        self.assertIsInstance(out.get("sources"), list)
        self.assertIsInstance(out.get("citations"), list)
        self.assertFalse(out.get("is_mock"))
        json.dumps(out, ensure_ascii=False)

    def test_rewrite_candidate_builds_title_and_word_count_instructions_from_scores(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "rewrite_required": True,
            "topic_id": "topic_score_rules",
            "candidate_id": 456,
            "title": "普通标题",
            "primary_keyword": "教授故事",
            "content_type": "case_study",
            "content_angle": "general",
            "source_title": "普通标题",
            "source_summary": "一位教授分享研究和教学中的关键转折。",
            "source_content": "教授分享了自己的研究经历、教学思考和团队成长。这个故事包含人物、转折、方法和启发。",
            "source_url": "https://example.com/story",
            "article_overall_score": 82,
            "article_title_style_score": 65,
            "word_count_score": 62,
            "article_word_count": 120,
        }

        out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))
        brief = out["research_brief"]

        self.assertEqual(brief["title_instruction"]["rewrite_mode"], "major_rewrite")
        self.assertEqual(brief["word_count_instruction"]["word_count_score"], 62)
        self.assertTrue(brief["word_count_instruction"]["should_adjust_word_count"])
        self.assertIn("扩写", brief["word_count_instruction"]["instruction"])
        self.assertIn("重新生成字数要求", brief["word_count_instruction"]["instruction"])
        self.assertIn("writer_prompt", brief)
        self.assertIn("重新生成标题", brief["writer_prompt"]["prompt_text"])
        self.assertIn("article.title_options", brief["writer_prompt"]["prompt_text"])
        self.assertIn("扩写", brief["writer_prompt"]["prompt_text"])
        self.assertIn("不要沿用原文字数结构", brief["writer_prompt"]["prompt_text"])

    def test_rewrite_candidate_adjusts_word_count_when_word_count_score_is_low(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "title": "教授心路历程：从课堂到科研团队的十年探索",
            "primary_keyword": "教授心路历程",
            "content_type": "case_study",
            "source_content": "教授回顾了从课堂教学到科研团队建设的经历。",
            "article_overall_score": 84,
            "article_title_style_score": 80,
            "word_count_score": 58,
            "article_word_count": 900,
        }

        out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))
        brief = out["research_brief"]

        self.assertTrue(brief["word_count_instruction"]["should_adjust_word_count"])
        self.assertEqual(brief["word_count_instruction"]["action"], "扩写")
        self.assertIn("字数分为 58", brief["word_count_instruction"]["instruction"])
        self.assertIn("重新生成字数要求", brief["writer_prompt"]["prompt_text"])

    def test_rewrite_candidate_notice_uses_shorter_word_count_range(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "title": "关于复试材料提交时间的通知",
            "primary_keyword": "复试材料提交",
            "content_type": "notice",
            "source_content": "考生需在规定时间内提交复试材料。",
            "article_overall_score": 80,
            "article_title_style_score": 75,
            "word_count_score": 75,
            "article_is_notice": True,
            "article_word_count": 360,
        }

        out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))
        brief = out["research_brief"]
        instruction = brief["word_count_instruction"]

        self.assertTrue(instruction["is_notice"])
        self.assertEqual(instruction["standard_min_words"], 300)
        self.assertEqual(instruction["standard_max_words"], 800)
        self.assertFalse(instruction["should_adjust_word_count"])
        self.assertIn("不强制写成长文", instruction["instruction"])

    def test_rewrite_candidate_keeps_title_minor_when_title_score_is_good(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "title": "教授心路历程：从课堂到科研团队的十年探索",
            "primary_keyword": "教授心路历程",
            "content_type": "case_study",
            "source_content": "教授回顾了从课堂教学到科研团队建设的经历。",
            "article_overall_score": 88,
            "article_title_style_score": 78,
            "word_count_score": 78,
            "article_word_count": 900,
        }

        out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))
        brief = out["research_brief"]

        self.assertEqual(brief["title_instruction"]["rewrite_mode"], "minor_rewrite")
        self.assertFalse(brief["word_count_instruction"]["should_adjust_word_count"])
        self.assertEqual(brief["word_count_instruction"]["standard_min_words"], 900)
        self.assertEqual(brief["word_count_instruction"]["standard_max_words"], 1200)
        self.assertIn("template_id", brief["writer_outline"])
        self.assertGreaterEqual(len(brief["writer_outline"]["sections"]), 3)

    def test_outline_template_matches_story_article_without_random_choice(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "title": "戴希教授的科研心路：从理论预测到未来科学大奖",
            "primary_keyword": "戴希教授 科研故事",
            "content_type": "case_study",
            "source_summary": "戴希教授分享科研之路、团队坚持和拓扑材料研究的关键转折。",
            "source_content": "戴希教授将科研比作寻宝，讲述自己和团队多年坚持、经历转折、最终获得未来科学大奖的故事。",
            "article_overall_score": 86,
            "article_title_style_score": 82,
            "article_word_count": 900,
        }

        out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))
        outline = out["research_brief"]["writer_outline"]

        self.assertEqual(outline["template_id"], "story_profile")
        self.assertEqual(outline["template_name"], "人物故事型")
        self.assertIn(outline["variant_id"], {"journey_turning_point", "team_story", "quote_led_story"})
        self.assertTrue(outline["variant_name"])

    def test_outline_template_matches_admissions_process_article(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "title": "2026年MBA招生申请流程及材料要求",
            "primary_keyword": "MBA申请流程",
            "content_type": "guide",
            "content_angle": "process",
            "source_summary": "文章介绍MBA招生报名、申请材料、面试流程和时间线。",
            "source_content": "考生需要关注报名时间、申请条件、材料准备、复试录取安排和项目学费。",
            "article_overall_score": 80,
            "article_title_style_score": 74,
            "article_word_count": 650,
        }

        out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))
        outline = out["research_brief"]["writer_outline"]

        self.assertEqual(outline["template_id"], "practical_guide")
        self.assertEqual(outline["template_name"], "实用指南型")
        self.assertIn(outline["variant_id"], {"condition_checklist", "process_timeline", "materials_preparation"})
        self.assertTrue(outline["variant_name"])
        self.assertIn("细分写法：", out["research_brief"]["writer_prompt"]["prompt_text"])

    def test_same_template_articles_rotate_across_variants(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        variants = set()
        for idx in range(10):
            rewrite_task = {
                "workflow_route": "full_rewrite_flow",
                "route_tier": "rewrite_candidate",
                "candidate_id": idx,
                "topic_id": f"story_{idx}",
                "title": f"教授科研心路与团队坚持故事 {idx}",
                "primary_keyword": "教授 科研故事",
                "content_type": "case_study",
                "source_summary": "教授分享科研经历、团队坚持和关键转折。",
                "source_content": "教授讲述自己和团队多年坚持、经历转折、一起完成科研突破的故事。",
                "article_overall_score": 86,
                "article_title_style_score": 82,
                "article_word_count": 900,
            }
            out = asyncio.run(agent.execute(topic=rewrite_task, mode="mock"))
            outline = out["research_brief"]["writer_outline"]
            self.assertEqual(outline["template_id"], "story_profile")
            variants.add(outline["variant_id"])

        self.assertGreaterEqual(len(variants), 2)

    def test_rewrite_candidate_can_use_llm_outline_in_live_mode(self):
        from agents.research_agent import ResearchAgent

        class FakeMessage:
            content = json.dumps(
                {
                    "writer_outline": {
                        "title": "AI生成的大纲",
                        "sections": [
                            {"title": "开篇", "key_points": ["交代事件"], "writing_tips": ["写清楚背景"], "notes": "llm"},
                            {"title": "展开", "key_points": ["拆解价值"], "writing_tips": ["结合事实"], "notes": "llm"},
                            {"title": "总结", "key_points": ["给出启发"], "writing_tips": ["不要夸大"], "notes": "llm"},
                        ],
                    }
                },
                ensure_ascii=False,
            )

        class FakeLLM:
            async def ainvoke(self, messages):
                self.messages = messages
                return FakeMessage()

        llm = FakeLLM()
        agent = ResearchAgent(llm=llm)
        rewrite_task = {
            "workflow_route": "full_rewrite_flow",
            "route_tier": "rewrite_candidate",
            "title": "教授故事",
            "primary_keyword": "教授故事",
            "source_content": "教授讲述自己的研究道路和团队建设。",
            "article_overall_score": 80,
            "article_title_style_score": 80,
            "article_word_count": 700,
        }

        out = asyncio.run(agent.execute(topic=rewrite_task, mode="live"))
        outline = out["research_brief"]["writer_outline"]

        self.assertEqual(outline["title"], "AI生成的大纲")
        self.assertEqual(outline["sections"][0]["notes"], "llm")
        self.assertIn("template_id", outline)
        self.assertTrue(getattr(llm, "messages", None))


if __name__ == "__main__":
    unittest.main()
