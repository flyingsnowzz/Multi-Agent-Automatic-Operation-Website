import unittest


class TestWorkflowEditResultFieldAlignment(unittest.TestCase):
    def test_langgraph_and_hybrid_use_editor_agent(self):
        with open("workflows/langgraph_workflow.py", "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("EditorAgent", text)
        self.assertIn("edit_result", text)

        with open("workflows/hybrid_workflow.py", "r", encoding="utf-8") as f:
            text2 = f.read()
        self.assertIn("EditorAgent", text2)
        self.assertIn("edit_result", text2)


if __name__ == "__main__":
    unittest.main()

