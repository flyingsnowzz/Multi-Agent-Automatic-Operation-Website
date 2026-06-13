import unittest
import asyncio
import json
from unittest.mock import patch, MagicMock

from agents.topic_agent.tools.topic_candidate_reader import TopicCandidateReader
from agents.topic_agent import TopicAgent
from workflows.topic_candidate_workflow import run_candidate_to_topics_workflow


class TestTopicCandidateIntegration(unittest.TestCase):
    def test_topic_candidate_reader_parses_json(self):
        # 模拟 read_pending 返回的数据
        fake_data = {
            "success": True,
            "data": [
                {
                    "id": 1,
                    "title": "Raw Title",
                    "content": "Raw Content",
                    "source_url": "https://example.com/source",
                    "raw_data": {
                        "id": 1,
                        "title": "Raw Title",
                        "content": "Raw Content",
                        "routing_payload": json.dumps({
                            "material_score": 88.5,
                            "route_tier": "publish_candidate",
                            "rewrite_required": False,
                            "publish_candidate": True,
                            "topic_hint": "Parsed Topic Hint",
                            "source_title": "Parsed Title",
                            "source_summary": "Parsed Summary",
                            "source_url": "https://example.com/source_payload",
                        })
                    }
                }
            ],
            "total": 1
        }

        async def mock_read_pending(*args, **kwargs):
            return fake_data

        reader = TopicCandidateReader({"type": "mysql", "table": "test"})
        reader.read_pending = mock_read_pending

        async def run():
            res = await reader.read_candidates(limit=10)
            return res

        res = asyncio.run(run())
        self.assertTrue(res["success"])
        cand = res["data"][0]
        self.assertEqual(cand["id"], 1)
        self.assertEqual(cand["material_score"], 88.5)
        self.assertEqual(cand["route_tier"], "publish_candidate")
        self.assertEqual(cand["rewrite_required"], False)
        self.assertEqual(cand["publish_candidate"], True)
        self.assertEqual(cand["topic_hint"], "Parsed Topic Hint")
        self.assertEqual(cand["source_title"], "Parsed Title")
        self.assertEqual(cand["source_url"], "https://example.com/source_payload")
        self.assertEqual(cand["source_content"], "Raw Content")
        self.assertEqual(cand["raw_data"]["title"], "Raw Title")

    @patch("agents.topic_agent.tools.keyword_research.KeywordResearchTool.research_keywords")
    @patch("agents.topic_agent.tools.serp_analysis.SERPAnalysisTool.analyze_serp")
    def test_topic_agent_executes_on_candidates(self, mock_serp, mock_kw):
        agent = TopicAgent(mode="mock")
        candidates = [
            {
                "id": 1,
                "material_score": 88.5,
                "route_tier": "publish_candidate",
                "rewrite_required": False,
                "publish_candidate": True,
                "topic_hint": "EMBA 报考条件",
                "source_title": "EMBA 报考条件",
                "source_summary": "Summary text",
                "source_url": "https://example.com/source",
                "search_volume": 600,
                "keyword_difficulty": 5.0,
            }
        ]

        async def run():
            res = await agent.execute_on_candidates(candidates, mode="mock")
            return res

        out = asyncio.run(run())
        self.assertIn("topics", out)
        topics = out["topics"]
        self.assertEqual(len(topics), 1)
        topic = topics[0]
        self.assertEqual(topic["candidate_id"], 1)
        self.assertEqual(topic["route_tier"], "publish_candidate")
        self.assertEqual(topic["rewrite_required"], False)
        self.assertEqual(topic["publish_candidate"], True)
        self.assertEqual(topic["material_score"], 88.5)
        self.assertEqual(topic["source_url"], "https://example.com/source")
        self.assertEqual(topic["primary_keyword"], "EMBA 报考条件")
        self.assertEqual(topic["secondary_keywords"], [])
        self.assertEqual(topic["content_angle"], "conditions")
        self.assertEqual(topic["source_content"], "Summary text")

    @patch("workflows.topic_candidate_workflow._get_postgres_engine")
    @patch("workflows.topic_candidate_workflow.update_crawler_status")
    @patch("workflows.topic_candidate_workflow.TopicCandidateReader.read_candidates")
    def test_workflow_e2e_accepted_rewrite_candidate(self, mock_read, mock_update, mock_pg):
        # 1. Mock 读出的 candidates 数据 (符合条件，会被接受)
        mock_read.return_value = {
            "success": True,
            "data": [
                {
                    "id": 1,
                    "material_score": 75.0,
                    "route_tier": "rewrite_candidate",
                    "rewrite_required": True,
                    "publish_candidate": False,
                    "topic_hint": "EMBA 报考条件",
                    "source_title": "EMBA 报考条件",
                    "source_summary": "Summary text",
                    "source_url": "https://example.com/source",
                    "routing_payload": {"original_key": "val"},
                    "search_volume": 600,
                    "keyword_difficulty": 5.0
                }
            ]
        }

        # 2. Mock 状态更新
        mock_update.return_value = {"success": True}

        # 3. Mock PostgreSQL 引擎
        fake_conn = MagicMock()
        fake_res = MagicMock()
        fake_res.fetchone.return_value = ("fake-uuid-123",)
        fake_conn.execute.return_value = fake_res
        
        fake_engine = MagicMock()
        fake_engine.connect.return_value.__enter__.return_value = fake_conn
        mock_pg.return_value = fake_engine

        async def run():
            res = await run_candidate_to_topics_workflow(
                limit=10,
                topic_agent_mode="mock",
                crawler_config_dir="agents/crawler_processor_agent",
                topic_config_dir="agents/topic_agent",
                dry_run=False
            )
            return res

        out = asyncio.run(run())
        self.assertTrue(out["success"])
        self.assertEqual(out["processed_count"], 1)
        
        topic = out["topics"][0]
        self.assertEqual(topic["postgres_topic_id"], "fake-uuid-123")
        self.assertEqual(topic["route_tier"], "rewrite_candidate")
        self.assertEqual(topic["rewrite_required"], True)
        self.assertEqual(topic["publish_candidate"], False)
        self.assertEqual(topic["is_accepted"], True)
        
        # 验证 PG 插入 (应该插入了 topic 和 task，即 execute 应该被调用两次)
        self.assertEqual(fake_conn.execute.call_count, 2)
        
        # 验证第一次 execute 是插入 topics 并且包含 workflow_route
        first_call_args = fake_conn.execute.call_args_list[0]
        self.assertIn("INSERT INTO topics", str(first_call_args[0][0]))
        self.assertEqual(first_call_args[0][1]["outline"]["candidate_metadata"]["workflow_route"], "full_rewrite_flow")

        # 验证第二次 execute 是插入 tasks
        second_call_args = fake_conn.execute.call_args_list[1]
        self.assertIn("INSERT INTO tasks", str(second_call_args[0][0]))
        self.assertEqual(second_call_args[0][1]["agent_name"], "ResearchAgent")
        self.assertNotEqual(second_call_args[0][1]["agent_name"], "WriterAgent")
        self.assertEqual(second_call_args[0][1]["task_type"], "research_for_rewrite")
        self.assertEqual(second_call_args[0][1]["status"], "pending")
        task_input = second_call_args[0][1]["input_data"]
        self.assertEqual(task_input["workflow_route"], "full_rewrite_flow")
        self.assertEqual(task_input["route_tier"], "rewrite_candidate")
        self.assertEqual(task_input["rewrite_required"], True)
        self.assertEqual(task_input["publish_candidate"], False)
        self.assertEqual(task_input["topic_id"], "fake-uuid-123")
        self.assertEqual(task_input["candidate_id"], 1)
        self.assertEqual(task_input["title"], topic["title"])
        self.assertEqual(task_input["primary_keyword"], "EMBA 报考条件")
        self.assertEqual(task_input["secondary_keywords"], [])
        self.assertEqual(task_input["target_keywords"], ["EMBA 报考条件"])
        self.assertEqual(task_input["search_intent"], "informational")
        self.assertEqual(task_input["content_type"], "guide")
        self.assertEqual(task_input["content_angle"], "conditions")
        self.assertEqual(task_input["source_title"], "EMBA 报考条件")
        self.assertEqual(task_input["source_summary"], "Summary text")
        self.assertEqual(task_input["source_url"], "https://example.com/source")
        self.assertEqual(task_input["source_content"], "Summary text")
        self.assertEqual(task_input["material_score"], 75.0)
        self.assertEqual(task_input["evaluation"], {})
        self.assertEqual(task_input["dedup"], {})
        self.assertEqual(task_input["routing_payload"]["original_key"], "val")
        self.assertEqual(task_input["routing_payload"]["topic_accepted"], True)
        self.assertEqual(task_input["routing_payload"]["workflow_route"], "full_rewrite_flow")
        
        # 验证 MySQL 状态更新
        self.assertTrue(mock_update.called)
        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        self.assertEqual(kwargs.get("record_id"), 1)
        self.assertEqual(kwargs.get("new_status"), "topic_accepted")
        self.assertEqual(kwargs.get("routing_payload").get("topic_accepted"), True)
        self.assertEqual(kwargs.get("routing_payload").get("workflow_route"), "full_rewrite_flow")


    @patch("workflows.topic_candidate_workflow._get_postgres_engine")
    @patch("workflows.topic_candidate_workflow.update_crawler_status")
    @patch("workflows.topic_candidate_workflow.TopicCandidateReader.read_candidates")
    def test_workflow_e2e_rejected_rewrite_candidate(self, mock_read, mock_update, mock_pg):
        # 1. Mock 读出的 candidates 数据 (质量 check 失败，因为 topic_hint 不包含 MBA/EMBA/商学院)
        mock_read.return_value = {
            "success": True,
            "data": [
                {
                    "id": 2,
                    "material_score": 50.0,
                    "route_tier": "rewrite_candidate",
                    "rewrite_required": True,
                    "publish_candidate": False,
                    "topic_hint": "普通行业怎么选",
                    "source_title": "普通行业怎么选",
                    "source_summary": "Summary text",
                    "source_url": "https://example.com/source",
                    "routing_payload": {"original_key": "val"}
                }
            ]
        }

        # 2. Mock 状态更新
        mock_update.return_value = {"success": True}

        # 3. Mock PostgreSQL 引擎
        fake_conn = MagicMock()
        fake_engine = MagicMock()
        fake_engine.connect.return_value.__enter__.return_value = fake_conn
        mock_pg.return_value = fake_engine

        async def run():
            res = await run_candidate_to_topics_workflow(
                limit=10,
                topic_agent_mode="mock",
                crawler_config_dir="agents/crawler_processor_agent",
                topic_config_dir="agents/topic_agent",
                dry_run=False
            )
            return res

        out = asyncio.run(run())
        self.assertTrue(out["success"])
        self.assertEqual(out["processed_count"], 0)
        self.assertEqual(out["rejected_count"], 1)
        
        topic = out["rejected"][0]
        self.assertNotIn("postgres_topic_id", topic)
        self.assertEqual(topic["is_accepted"], False)
        self.assertIsNotNone(topic["reject_reason"])
        
        # 验证 PG 插入没有被执行 (因为拒绝了选题)
        self.assertFalse(fake_conn.execute.called)
        
        # 验证 MySQL 状态被更新为 topic_rejected 并写入 reject_reason
        self.assertTrue(mock_update.called)
        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        self.assertEqual(kwargs.get("record_id"), 2)
        self.assertEqual(kwargs.get("new_status"), "topic_rejected")
        self.assertEqual(kwargs.get("routing_payload").get("topic_accepted"), False)
        self.assertIn("低于设定阈值", kwargs.get("routing_payload").get("reject_reason"))

    @patch("workflows.topic_candidate_workflow._get_postgres_engine")
    @patch("workflows.topic_candidate_workflow.update_crawler_status")
    @patch("workflows.topic_candidate_workflow.TopicCandidateReader.read_candidates")
    def test_workflow_e2e_accepted_publish_candidate_creates_light_publish_task(self, mock_read, mock_update, mock_pg):
        # 1. Mock 读出的 candidates 数据 (score >= 80, publish_candidate)
        mock_read.return_value = {
            "success": True,
            "data": [
                {
                    "id": 3,
                    "material_score": 85.0,
                    "route_tier": "publish_candidate",
                    "rewrite_required": False,
                    "publish_candidate": True,
                    "topic_hint": "EMBA 院校怎么选",
                    "source_title": "EMBA 院校怎么选",
                    "source_summary": "Summary text",
                    "source_url": "https://example.com/source",
                    "routing_payload": {"original_key": "val"},
                    "search_volume": 600,
                    "keyword_difficulty": 5.0,
                }
            ]
        }

        # 2. Mock 状态更新
        mock_update.return_value = {"success": True}

        # 3. Mock PostgreSQL 引擎
        fake_conn = MagicMock()
        fake_res = MagicMock()
        fake_res.fetchone.return_value = ("fake-uuid-456",)
        fake_conn.execute.return_value = fake_res
        
        fake_engine = MagicMock()
        fake_engine.connect.return_value.__enter__.return_value = fake_conn
        mock_pg.return_value = fake_engine

        async def run():
            res = await run_candidate_to_topics_workflow(
                limit=10,
                topic_agent_mode="mock",
                crawler_config_dir="agents/crawler_processor_agent",
                topic_config_dir="agents/topic_agent",
                dry_run=False
            )
            return res

        out = asyncio.run(run())
        self.assertTrue(out["success"])
        self.assertEqual(out["processed_count"], 1)
        
        topic = out["topics"][0]
        self.assertEqual(topic["postgres_topic_id"], "fake-uuid-456")
        self.assertEqual(topic["route_tier"], "publish_candidate")
        self.assertEqual(topic["is_accepted"], True)
        self.assertEqual(topic["workflow_route"], "light_publish_flow")
        
        # 验证 PG 执行了两次插入 (插入 topic 和 task)
        self.assertEqual(fake_conn.execute.call_count, 2)
        first_call_args = fake_conn.execute.call_args_list[0]
        self.assertIn("INSERT INTO topics", str(first_call_args[0][0]))
        self.assertEqual(first_call_args[0][1]["outline"]["candidate_metadata"].get("workflow_route"), "light_publish_flow")
        
        second_call_args = fake_conn.execute.call_args_list[1]
        self.assertIn("INSERT INTO tasks", str(second_call_args[0][0]))
        self.assertEqual(second_call_args[0][1]["agent_name"], "CMSAgent")
        self.assertEqual(second_call_args[0][1]["task_type"], "publish")
        self.assertEqual(second_call_args[0][1]["input_data"].get("workflow_route"), "light_publish_flow")
        
        # 验证 MySQL 状态被更新为 topic_accepted (默认 accepted_status)
        self.assertTrue(mock_update.called)
        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        self.assertEqual(kwargs.get("record_id"), 3)
        self.assertEqual(kwargs.get("new_status"), "topic_accepted")
        self.assertEqual(kwargs.get("routing_payload").get("topic_accepted"), True)
        self.assertEqual(kwargs.get("routing_payload").get("workflow_route"), "light_publish_flow")

    @patch("workflows.topic_candidate_workflow._get_postgres_engine")
    @patch("workflows.topic_candidate_workflow.update_crawler_status")
    @patch("workflows.topic_candidate_workflow.TopicCandidateReader.read_candidates")
    def test_workflow_e2e_rejected_publish_candidate_no_task_created(self, mock_read, mock_update, mock_pg):
        mock_read.return_value = {
            "success": True,
            "data": [
                {
                    "id": 13,
                    "material_score": 86.0,
                    "route_tier": "publish_candidate",
                    "rewrite_required": False,
                    "publish_candidate": True,
                    "topic_hint": "EMBA方法",
                    "source_title": "EMBA方法",
                    "source_summary": "Summary",
                    "source_url": "https://example.com/source",
                    "routing_payload": {},
                }
            ]
        }

        mock_update.return_value = {"success": True}

        fake_conn = MagicMock()
        fake_engine = MagicMock()
        fake_engine.connect.return_value.__enter__.return_value = fake_conn
        mock_pg.return_value = fake_engine

        async def run():
            res = await run_candidate_to_topics_workflow(
                limit=10,
                topic_agent_mode="mock",
                crawler_config_dir="agents/crawler_processor_agent",
                topic_config_dir="agents/topic_agent",
                dry_run=False
            )
            return res

        out = asyncio.run(run())
        self.assertTrue(out["success"])
        self.assertEqual(out["processed_count"], 0)
        self.assertEqual(out["rejected_count"], 1)
        self.assertFalse(fake_conn.execute.called)

        rejected_topic = out["rejected"][0]
        self.assertEqual(rejected_topic["route_tier"], "publish_candidate")
        self.assertFalse(rejected_topic["is_accepted"])
        self.assertIn("违禁模式", rejected_topic["reject_reason"])
        self.assertIsNone(rejected_topic["workflow_route"])

        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        self.assertEqual(kwargs.get("record_id"), 13)
        self.assertEqual(kwargs.get("new_status"), "topic_rejected")
        self.assertEqual(kwargs.get("routing_payload").get("topic_accepted"), False)
        self.assertIn("违禁模式", kwargs.get("routing_payload").get("reject_reason"))

    @patch("workflows.topic_candidate_workflow._get_postgres_engine")
    @patch("workflows.topic_candidate_workflow.update_crawler_status")
    @patch("workflows.topic_candidate_workflow.TopicCandidateReader.read_candidates")
    def test_workflow_screening_cases(self, mock_read, mock_update, mock_pg):
        # 1. Mock 读出的 candidates 数据 (三个被拒绝的 case)
        mock_read.return_value = {
            "success": True,
            "data": [
                {
                    "id": 10,
                    "material_score": 50.0,
                    "route_tier": "rewrite_candidate",
                    "rewrite_required": True,
                    "publish_candidate": False,
                    "topic_hint": "", # Empty hint
                    "source_title": "Empty Hint Title",
                    "source_summary": "Summary text",
                    "source_url": "https://example.com/source",
                    "routing_payload": {}
                },
                {
                    "id": 11,
                    "material_score": 50.0,
                    "route_tier": "rewrite_candidate",
                    "rewrite_required": True,
                    "publish_candidate": False,
                    "topic_hint": "EMBA 课程", # Will have low priority score due to low search volume / high difficulty
                    "source_title": "EMBA 课程",
                    "source_summary": "Summary",
                    "source_url": "https://example.com/source",
                    "routing_payload": {},
                    "search_volume": 10, # Very low
                    "keyword_difficulty": 99.0, # Very high
                    "competition_score": 99.0
                },
                {
                    "id": 12,
                    "material_score": 50.0,
                    "route_tier": "rewrite_candidate",
                    "rewrite_required": True,
                    "publish_candidate": False,
                    "topic_hint": "EMBA方法", # Forbidden keyword
                    "source_title": "EMBA方法",
                    "source_summary": "Summary",
                    "source_url": "https://example.com/source",
                    "routing_payload": {}
                }
            ]
        }

        # 2. Mock 状态更新
        mock_update.return_value = {"success": True}

        # 3. Mock PostgreSQL 引擎
        fake_conn = MagicMock()
        fake_engine = MagicMock()
        fake_engine.connect.return_value.__enter__.return_value = fake_conn
        mock_pg.return_value = fake_engine

        async def run():
            res = await run_candidate_to_topics_workflow(
                limit=10,
                topic_agent_mode="mock",
                crawler_config_dir="agents/crawler_processor_agent",
                topic_config_dir="agents/topic_agent",
                dry_run=False
            )
            return res

        out = asyncio.run(run())
        self.assertTrue(out["success"])
        self.assertEqual(out["processed_count"], 0)
        self.assertEqual(out["rejected_count"], 3)

        # 验证 PG 插入没有被执行
        self.assertFalse(fake_conn.execute.called)

        # 验证这三个 candidate 状态都更新为 topic_rejected，且带了相应的 reject_reason
        self.assertEqual(mock_update.call_count, 3)
        
        # 验证第一个：空 hint
        call_0_kwargs = mock_update.call_args_list[0][1]
        self.assertEqual(call_0_kwargs.get("record_id"), 10)
        self.assertEqual(call_0_kwargs.get("new_status"), "topic_rejected")
        self.assertIn("为空", call_0_kwargs.get("routing_payload").get("reject_reason"))

        # 验证第二个：低 score
        call_1_kwargs = mock_update.call_args_list[1][1]
        self.assertEqual(call_1_kwargs.get("record_id"), 11)
        self.assertEqual(call_1_kwargs.get("new_status"), "topic_rejected")
        self.assertIn("低于最低限制", call_1_kwargs.get("routing_payload").get("reject_reason"))

        # 验证第三个：违禁词
        call_2_kwargs = mock_update.call_args_list[2][1]
        self.assertEqual(call_2_kwargs.get("record_id"), 12)
        self.assertEqual(call_2_kwargs.get("new_status"), "topic_rejected")
        self.assertIn("违禁模式", call_2_kwargs.get("routing_payload").get("reject_reason"))


if __name__ == "__main__":
    unittest.main()
