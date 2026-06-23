"""Lazy exports for TopicAgent tools."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "get_keyword_research_tool": ("keyword_research", "get_keyword_research_tool"),
    "KeywordResearchTool": ("keyword_research", "KeywordResearchTool"),
    "get_serp_analysis_tool": ("serp_analysis", "get_serp_analysis_tool"),
    "SERPAnalysisTool": ("serp_analysis", "SERPAnalysisTool"),
    "get_trend_detection_tool": ("trend_detection", "get_trend_detection_tool"),
    "TrendDetectionTool": ("trend_detection", "TrendDetectionTool"),
    "get_topic_candidate_reader_tool": ("topic_candidate_reader", "get_topic_candidate_reader_tool"),
    "TopicCandidateReader": ("topic_candidate_reader", "TopicCandidateReader"),
    "ArticleScoreDBWriter": ("article_score_writer", "ArticleScoreDBWriter"),
    "build_article_score_update_payload": (
        "article_score_writer",
        "build_article_score_update_payload",
    ),
    "write_article_scores_to_db": ("article_score_writer", "write_article_scores_to_db"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
