from typing import Any, Dict, List, Optional


class CrawlerProcessorAgent:
    """Crawler 入口层封装。

    当前职责：
    - 负责把爬虫原始内容送入 crawler 工作流做读取、标准化和基础校验
    - 为后续独立 `Review` 阶段生成统一交接 payload

    当前限制：
    - 本类自身不实现去重、评分或业务分流逻辑
    - 真实的状态落库与目录外工作流联动仍由 `workflows.crawler_workflow`
      负责
    """

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
        from yaojiayk.workflows.crawler_workflow import run_crawler_workflow

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
