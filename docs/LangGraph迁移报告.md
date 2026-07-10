# LangGraph 正式化迁移报告

## 1. 当前结论

项目主链路已经从 Redis Streams worker 模式切到独立 LangGraph 批处理模式。

```text
正式入口: scripts/run_langgraph_batch.py --production
单篇调试: scripts/run_langgraph_pipeline.py --article-id <id>
旧版归档: legacy/redis_pipeline/
```

Redis 版没有删除，已经整体归档到 `legacy/redis_pipeline/`，用于回滚、差异对照和旧逻辑 code review。默认 Docker、Makefile 和 README 都指向 LangGraph 正式版。

## 2. 为什么可以替代 Redis 版

LangGraph 版已经覆盖 Redis 主链路的核心功能：

| 功能 | Redis 版 | LangGraph 正式版 |
| --- | --- | --- |
| 从 MySQL 取文章 | `redis_feeder.py` / `redis_fill.py` | `run_langgraph_batch.py --feed` |
| 批量 scoring | `worker_scoring.py` | `summarize_crawler_topics(... ai_concurrency=...)` |
| 原文正文读取 | feeder/worker 读取分片表 | `load_source_node` 读取 `crawler_news_0..4` |
| quality 分流 | `worker_quality.py` | graph conditional edge |
| rewrite 链路 | `worker_rewrite.py` | rewrite node 内部调用 Research/Writer/Quality/Editor |
| SEO | `worker_publish.py` | `seo_node` |
| image | `worker_image.py` | `image_node` |
| CMS | `worker_cms.py` | `cms_node` |
| 审计落库 | `pipeline_audit` | `save_audit_node` |
| prompt 日志 | JSONL | JSONL |
| 已处理标记 | `article_usage_status='used'` | `--mark-used` |

## 3. 迁移后的关键变化

- Redis stream 不再是正式链路的调度器。
- 阶段间状态集中在 `ArticleGraphState`，流程分支集中在 LangGraph 定义里。
- 批量评分仍保持 Redis scoring worker 的 batch-normalized 口径，避免单篇 raw scoring 和旧结果不一致。
- feed 状态只在一批处理完成后推进，降低中途异常导致文章被跳过的风险。
- batch / graph 异常进入 `output/langgraph_deadletter.jsonl`，用于长期无人值守排查。
- Redis 版 worker 数量参数仍保留在 `.env.example`，但只服务于 `legacy/redis_pipeline/`。

## 4. 还需要重点 review 的风险

- `scripts/run_langgraph_batch.py` 的 feed 游标、mark-used、异常继续运行是否符合生产预期。
- `workflows/langgraph_article_pipeline.py` 的每个 node 是否都足够幂等，尤其 image 和 CMS。
- `scripts/publish_common.py` 的封面规则是否符合“重写用新图，没重写用原图，重跑复用已生成图”。
- `.env.example` 和 `docker-compose.yml` 是否不会同时启动 LangGraph 和 Redis legacy 处理同一批文章。
- Redis legacy 归档后，旧监控和测试是否已经指向 `legacy/redis_pipeline/`。

## 5. 回滚方式

本机回滚到 Redis legacy：

```bash
python3 legacy/redis_pipeline/run_redis_workers.py --feed --dry-run
```

Docker 回滚到 Redis legacy：

```bash
docker compose --profile redis-legacy up -d --build mysql redis redis-pipeline
```

不要让 Redis legacy 和 LangGraph `pipeline` 同时处理同一套未使用文章，否则会竞争 `crawler_news_main` 行。
