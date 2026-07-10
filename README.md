# 多Agent自动运营网站

当前正式入口是 LangGraph 批处理流水线：

```text
MySQL crawler_news_main
  -> run_langgraph_batch.py feeder
  -> batch ScoringAgent
  -> LangGraph ArticleState
  -> load_source
  -> quality
  -> rewrite 或 seo
  -> image
  -> cms
  -> CMSAgent dry-run / real publish
```

Redis Streams 版本已经归档到 `legacy/redis_pipeline/`，只作为回滚、对照和 code review 参考，不再作为默认生产入口。

## 快速开始

换电脑或从零配置时，先看 [15-新电脑环境配置教程.md](15-新电脑环境配置教程.md)。下面是日常快速启动摘要。

### 1. 准备环境变量

```bash
cp .env.example .env
```

至少需要配置：

- `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE`
- `ARTICLE_SCORING_API_KEY` / `ARTICLE_SCORING_MODEL` / `ARTICLE_SCORING_BASE_URL`
- `WRITER_AGENT_API_KEY` / `WRITER_AGENT_MODEL` / `WRITER_AGENT_BASE_URL`

CMS 未接入时保持：

```bash
CMS_ENABLE_REAL_PUBLISH=false
```

### 2. Docker 一键部署

推荐部署方式是 Docker Compose：MySQL 和 LangGraph pipeline 都由 Docker 托管，容器退出后会自动重启。

```bash
docker compose up -d --build mysql pipeline
```

或用 Makefile：

```bash
make docker-run
```

查看 pipeline 日志：

```bash
docker compose logs -f pipeline
```

检查运行文件是否可编译：

```bash
docker compose exec pipeline python3 -m py_compile scripts/run_langgraph_batch.py workflows/langgraph_article_pipeline.py
```

默认 `pipeline` 运行：

```bash
python3 scripts/run_langgraph_batch.py --production
```

`--production` 等同于 `--feed --loop --persist-audit --mark-used`，CMS 仍然默认 dry-run。常用 LangGraph 参数：

```env
LANGGRAPH_BATCH_LIMIT=30
LANGGRAPH_LOOP_INTERVAL_SECONDS=60
LANGGRAPH_SCORING_AI_CONCURRENCY=4
LANGGRAPH_FEED_EXISTING=true
LANGGRAPH_FEED_FROM_ID=0
LANGGRAPH_ARGS=
```

真实发布时需要同时打开 `.env` 和命令参数：

```env
CMS_ENABLE_REAL_PUBLISH=true
LANGGRAPH_ARGS=--publish
```

### 3. 只启动基础服务，本机跑 Python

推荐用 Docker 启动 MySQL。MySQL 首次启动会通过 `docker/mysql/init/01-create-databases.sql` 创建 `multi_agent_cms` 和 `research_article_data`。

```bash
docker compose up -d mysql
```

本机长期无人值守跑 LangGraph：

```bash
python3 scripts/run_langgraph_batch.py --production
```

只跑一轮，便于调试：

```bash
python3 scripts/run_langgraph_batch.py --feed --limit 30
```

指定文章调试：

```bash
python3 scripts/run_langgraph_pipeline.py --article-id 1961 --persist-audit
```

如果还需要本地 Postgres/Mongo/MinIO/Qdrant 辅助服务：

```bash
docker compose up -d
```

### 4. 运行 Redis legacy 版本

Redis 代码已经移动到 `legacy/redis_pipeline/`。只在回滚或对比时运行，不要和 LangGraph 正式 pipeline 同时处理同一批文章。

```bash
python3 legacy/redis_pipeline/run_redis_workers.py --dry-run
```

Docker 启动 Redis legacy：

```bash
docker compose --profile redis-legacy up -d --build mysql redis redis-pipeline
```

检查 Redis legacy：

```bash
python3 legacy/redis_pipeline/monitor_pipeline.py --require-workers
```

## 运维说明

- LangGraph feeder 状态写在 `LANGGRAPH_FEED_STATE_PATH`，默认 `output/langgraph_feeder_state.json`。状态只会在一批处理完成后推进，避免中途崩溃跳过文章。
- batch 或 graph 异常写入 `LANGGRAPH_DEADLETTER_PATH`，默认 `output/langgraph_deadletter.jsonl`。
- prompt、scoring reason/breakdown、quality reason/suggestion 等大段审计内容写入 `PROMPT_AUDIT_LOG_DIR` 下的 JSONL 文件，默认 `logs/prompt_audit/YYYY-MM-DD.jsonl`，不写入 MySQL。
- `pipeline_audit` 保存分数、改写内容、SEO、图片、CMS 关键状态。
- `make run` 和 `docker compose up pipeline` 默认调用 LangGraph 正式版。
- TopicAgent 是废弃入口，相关旧测试已标记跳过。

## 常用命令

```bash
python3 -m pytest -q
python3 -m compileall agents workflows scripts legacy
make run
python3 scripts/run_langgraph_batch.py --feed --limit 5
```

## MySQL 迁移

```bash
set -a
source .env
set +a

docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < sql/create_pipeline_audit.sql
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < sql/alter_pipeline_audit_seo_meta.sql
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < sql/alter_crawler_article_usage_status.sql
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < sql/alter_pipeline_audit_drop_large_audit_payloads.sql
```
