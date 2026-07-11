"""QualityAgent facade.

Beginner mental model:
    This is a thin wrapper around the real quality scoring tool. The pipeline
    does not import the tool directly; it imports QualityAgent so the public API
    stays small and stable.

What it scores:
    Writing quality of a specific article, not whether the topic is valuable.
    Topic value is handled earlier by ScoringAgent.

Used by:
    - LangGraph quality_node for original crawler articles
    - LangGraph rewrite_quality_node for rewritten drafts
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from agents.quality_agent.tools.article_quality_scorer import (
    OpenAICompatibleQualityClient,
    QualityLLMConfig,
    build_quality_prompt,
)


class QualityAgent:
    """Evaluate article writing quality, not topic value."""

    def __init__(self, llm_config: Optional[QualityLLMConfig] = None, client: Optional[Any] = None):
        self.client = client or OpenAICompatibleQualityClient(llm_config)

    async def score_article(self, article: Mapping[str, Any]) -> Dict[str, Any]:
        # Public async entry used by workers. The client builds the prompt,
        # calls the configured model, and normalizes the score/result shape.
        return await self.client.score(article)

    def build_prompt(self, article: Mapping[str, Any]) -> str:
        # Debug/helper path: returns the prompt without calling the model.
        return build_quality_prompt(article)
