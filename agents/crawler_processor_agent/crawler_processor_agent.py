from typing import Any, Dict, List, Optional

from workflows.crawler_workflow import run_crawler_workflow


class CrawlerProcessorAgent:
    def __init__(self, config_dir: str = "agents/crawler_processor_agent"):
        self.config_dir = config_dir

    async def execute(
        self,
        *,
        limit: int = 10,
        min_id: Optional[int] = None,
        max_id: Optional[int] = None,
        target_keywords: Optional[List[str]] = None,
        dry_run: bool = True,
        items: Optional[List[Dict[str, Any]]] = None,
        published_articles: Optional[List[Dict[str, Any]]] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await run_crawler_workflow(
            limit=limit,
            min_id=min_id,
            max_id=max_id,
            target_keywords=target_keywords,
            dry_run=dry_run,
            items=items,
            published_articles=published_articles,
            config=config,
            config_dir=self.config_dir,
            **kwargs,
        )

