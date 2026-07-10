# Redis Legacy 流水线代码阅读指南

> 当前正式生产入口是 LangGraph：`scripts/run_langgraph_batch.py --production`。
> Redis Streams 版本已经整体移动到 `legacy/redis_pipeline/`，这份文档只用于回滚、对照和 code review。

## 1. 旧链路结构

```text
MySQL crawler data
  -> legacy/redis_pipeline/redis_feeder.py 或 redis_fill.py
  -> pipeline:scoring
  -> worker_scoring.py
  -> pipeline:quality
  -> worker_quality.py
  -> pipeline:rewrite 或 pipeline:publish
  -> worker_rewrite.py 或 worker_publish.py
  -> pipeline:image
  -> worker_image.py
  -> pipeline:cms
  -> worker_cms.py
```

## 2. 文件位置

| 文件 | 用途 |
| --- | --- |
| `legacy/redis_pipeline/run_redis_workers.py` | Redis legacy supervisor |
| `legacy/redis_pipeline/redis_pipeline.py` | stream 名称、consumer group、ack、retry、deadletter |
| `legacy/redis_pipeline/redis_feeder.py` | MySQL 持续灌入 Redis |
| `legacy/redis_pipeline/redis_fill.py` | 一次性灌入 Redis |
| `legacy/redis_pipeline/worker_scoring.py` | 旧 scoring worker |
| `legacy/redis_pipeline/worker_quality.py` | 旧 quality worker |
| `legacy/redis_pipeline/worker_rewrite.py` | 旧 rewrite worker |
| `legacy/redis_pipeline/worker_publish.py` | 旧 SEO/pre-publish worker |
| `legacy/redis_pipeline/worker_image.py` | 旧 image worker |
| `legacy/redis_pipeline/worker_cms.py` | 旧 CMS worker |
| `legacy/redis_pipeline/monitor_pipeline.py` | 旧 Redis pending/deadletter 监控 |

## 3. 运行方式

本机：

```bash
python3 legacy/redis_pipeline/run_redis_workers.py --feed --dry-run
```

Docker：

```bash
docker compose --profile redis-legacy up -d --build mysql redis redis-pipeline
```

不要和正式 LangGraph `pipeline` 同时处理同一批文章。

## 4. 和 LangGraph 正式版的关系

Redis legacy 的 worker 阶段已经被下面文件替代：

| Redis legacy | LangGraph 正式版 |
| --- | --- |
| `redis_feeder.py` / `redis_fill.py` | `run_langgraph_batch.py --feed` |
| `worker_scoring.py` | batch scoring in `run_langgraph_batch.py` |
| `worker_quality.py` | `quality_node` |
| `worker_rewrite.py` | `rewrite_node` |
| `worker_publish.py` | `seo_node` |
| `worker_image.py` | `image_node` |
| `worker_cms.py` | `cms_node` |
| Redis deadletter | `output/langgraph_deadletter.jsonl` |

## 5. Code Review 时怎么看

如果 review 的目标是 LangGraph 正式化，不需要逐行审 Redis legacy；只需要确认：

- 旧文件都在 `legacy/redis_pipeline/`。
- 旧 supervisor 内部启动路径已经改到 `legacy/redis_pipeline/*`。
- 旧测试 `tests/test_redis_pipeline.py` 已改为从 `legacy.redis_pipeline` import。
- Docker 默认不再启动 Redis worker，只有 `redis-legacy` profile 会启动。
