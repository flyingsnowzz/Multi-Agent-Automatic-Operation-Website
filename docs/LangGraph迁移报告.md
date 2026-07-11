# LangGraph 正式化迁移报告

## 1. 当前结论

项目主链路已经切到独立 LangGraph 批处理模式，旧队列 worker 版本已经删除。

```text
正式入口: scripts/run_langgraph_batch.py --production
少量文章调试: scripts/run_langgraph_batch.py --ids <id> --limit 1 --persist-audit
运行日志: logs/langgraph_batch.log
```

Docker、Makefile 和 README 都指向 LangGraph 正式版。项目不再提供 LangGraph 之外的旧 worker 回滚入口，避免两套调度器竞争同一批文章。

## 2. LangGraph 覆盖的主链路

| 功能 | LangGraph 正式版 |
| --- | --- |
| 从 MySQL 取文章 | `run_langgraph_batch.py --feed` |
| 批量 scoring | `summarize_crawler_topics(... ai_concurrency=...)` |
| 原文正文读取 | `load_source_node` 读取 `crawler_news_0..4` |
| quality 分流 | LangGraph conditional edge |
| rewrite 链路 | Research/Writer/Quality/Editor 节点 |
| SEO | `seo_node` |
| image | `image_node` |
| CMS | `cms_node` |
| 审计落库 | `save_audit_node` 写 `pipeline_audit` |
| prompt 日志 | `logs/prompt_audit/*.jsonl` |
| 已处理标记 | `--mark-used` |

## 3. 迁移后的关键变化

- LangGraph 之外的旧队列 worker 代码已经删除。
- 阶段间状态集中在 `ArticleGraphState`，流程分支集中在 LangGraph 定义里。
- 批量评分由 batch runner 统一执行，避免单篇 raw scoring 造成批内分数不可比。
- feed 状态只在一批处理完成后推进，降低中途异常导致文章被跳过的风险。
- batch / graph 异常进入 `output/langgraph_deadletter.jsonl`，用于长期无人值守排查。
- 主 `.env.example` 只保留 LangGraph 正式链路配置。

## 4. 还需要重点 review 的风险

- `scripts/run_langgraph_batch.py` 的 feed 游标、mark-used、异常继续运行是否符合生产预期。
- `workflows/langgraph_article_pipeline.py` 的每个 node 是否都足够幂等，尤其 image 和 CMS。
- `scripts/publish_common.py` 的封面规则是否符合“重写用新图，没重写用原图，重跑复用已生成图”。
- `.env.example` 和 `docker-compose.yml` 是否不再包含旧 LangGraph 之外的旧 worker 参数。
- Docker entrypoint 是否只等待 MySQL，不再等待旧队列服务。

## 5. 回滚方式

当前仓库不再保留 LangGraph 之外的旧 worker 回滚入口。需要回滚时应从 Git 历史恢复旧实现，并先停止 LangGraph `pipeline`，避免重复处理同一批文章。
