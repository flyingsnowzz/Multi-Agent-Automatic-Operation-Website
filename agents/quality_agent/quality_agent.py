"""QualityAgent facade."""

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
        return await self.client.score(article)

    def build_prompt(self, article: Mapping[str, Any]) -> str:
        return build_quality_prompt(article)
