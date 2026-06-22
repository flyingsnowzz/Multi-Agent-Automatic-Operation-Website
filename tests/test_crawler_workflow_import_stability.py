import importlib
import sys
import unittest


def _clear_modules():
    targets = [
        "agents.crawler_processor_agent",
        "agents.crawler_processor_agent.crawler_processor_agent",
        "workflows.crawler_workflow",
    ]
    for name in targets:
        sys.modules.pop(name, None)


class TestCrawlerWorkflowImportStability(unittest.TestCase):
    def test_import_agent_then_workflow(self):
        _clear_modules()
        importlib.import_module("agents.crawler_processor_agent")
        mod = importlib.import_module("workflows.crawler_workflow")
        self.assertTrue(hasattr(mod, "run_crawler_workflow"))

    def test_import_workflow_then_agent(self):
        _clear_modules()
        mod = importlib.import_module("workflows.crawler_workflow")
        agent_mod = importlib.import_module("agents.crawler_processor_agent")
        self.assertTrue(hasattr(mod, "run_crawler_workflow"))
        self.assertTrue(hasattr(agent_mod, "CrawlerProcessorAgent"))

    def test_from_import_symbol_is_stable(self):
        _clear_modules()
        from agents.crawler_processor_agent import CrawlerProcessorAgent

        mod = importlib.import_module("workflows.crawler_workflow")
        self.assertTrue(callable(CrawlerProcessorAgent))
        self.assertTrue(hasattr(mod, "run_crawler_workflow"))
