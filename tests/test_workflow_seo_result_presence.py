import unittest


class TestWorkflowSEOResultPresence(unittest.TestCase):
    def test_langgraph_has_seo_result_assignment(self):
        with open("workflows/langgraph_workflow.py", "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn('state["seo_result"]', text)

    def test_crewai_seo_agent_has_tools(self):
        with open("workflows/crewai_workflow.py", "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("get_keyword_analyzer_tool", text)
        self.assertIn("get_meta_generator_tool", text)
        self.assertIn("get_schema_generator_tool", text)


if __name__ == "__main__":
    unittest.main()

