__all__ = ["CrawlerProcessorAgent"]


def __getattr__(name):
    if name == "CrawlerProcessorAgent":
        from .crawler_processor_agent import CrawlerProcessorAgent

        return CrawlerProcessorAgent
    raise AttributeError(name)
