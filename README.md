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

旧队列版本已经删除。当前项目只保留 LangGraph 正式流水线，避免两套调度器同时消费文章。

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

`MYSQL_DATABASE` 是一个数据库/库名，不是单张表名。LangGraph 会在这个库里读取爬虫主表和正文分片表，并把结果写入 `pipeline_audit`。默认表名是 `crawler_news_main` + `crawler_news_0..4`；如果接现有 CMS 库，在 `.env` 里改 `CRAWLER_MAIN_TABLE`、`CRAWLER_SHARD_PREFIX`、`CRAWLER_SHARD_COUNT` 即可。

CMS 未接入时保持：

```bash
CMS_ENABLE_REAL_PUBLISH=false
```

测试站 BFF 发布接口使用：

```env
CMS_API_URL=https://api-zyxw.cymba.cn
BFF_API_SECRET=your_bff_api_secret_here
CMS_API_KEY=
CMS_PUBLIC_ARTICLE_URL_TEMPLATE=https://zyxw.cymba.cn/article/{articleid}
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

本机长期无人值守跑 LangGraph。这个命令会放到后台运行，不占用当前终端，运行日志写到 `logs/langgraph_batch.log`：

```bash
make run
```

查看、停止、确认状态：

```bash
make logs
make status
make stop
```

追溯某一篇文章的分数明细、prompt、SEO/image/CMS payload：

```bash
make trace ARTICLE_ID=213
TRACE_ARGS=--full make trace ARTICLE_ID=213
```

只跑一轮，便于调试：

```bash
python3 scripts/run_langgraph_batch.py --feed --limit 30
```

指定少量文章调试：

```bash
python3 scripts/run_langgraph_batch.py --ids 1961 --limit 1 --persist-audit
```

如果还需要本地 Postgres/Mongo/MinIO/Qdrant 辅助服务：

```bash
docker compose up -d
```

## 运维说明

- LangGraph feeder 状态写在 `LANGGRAPH_FEED_STATE_PATH`，默认 `output/langgraph_feeder_state.json`。状态只会在一批处理完成后推进，避免中途崩溃跳过文章。
- batch 或 graph 异常写入 `LANGGRAPH_DEADLETTER_PATH`，默认 `output/langgraph_deadletter.jsonl`。
- 运行日志写入 `LANGGRAPH_RUN_LOG`，默认 `logs/langgraph_batch.log`。格式是人读的一行事件，例如 `2026-07-11 10:20:30 INFO langgraph.batch: article_done article_id=1961 ai_score=82.4 quality_score=73.0 cms_status=dry_run stop_reason=- audit=True title="..."`。
- 日志详细程度由 `LANGGRAPH_LOG_LEVEL` 控制，默认 `INFO`。第三方 HTTP 日志由 `LANGGRAPH_THIRD_PARTY_LOG_LEVEL` 控制，默认 `WARNING`，避免 `httpx` 请求刷屏。
- prompt、scoring reason/breakdown、quality reason/suggestion、生成正文、编辑后正文、SEO/CMS payload 等大段审计内容写入 `PROMPT_AUDIT_LOG_DIR` 下的 JSONL 文件，默认 `logs/prompt_audit/YYYY-MM-DD.jsonl`，不写入 MySQL。
- 长文本审计由 `PROMPT_AUDIT_TEXT_LIMIT` 控制，默认每个文本字段最多保留 `12000` 字符；设为 `0` 可不截断。
- `pipeline_audit` 保存分数、改写内容、SEO、图片、CMS 关键状态。
- `make run` 和 `docker compose up pipeline` 默认调用 LangGraph 正式版。
- `make run` / `make run-bg` 都是后台运行；只有 `make run-fg` 才会占用当前终端，适合临时调试。
- TopicAgent 是废弃入口，相关旧测试已标记跳过。

## 常用命令

```bash
python3 -m pytest -q
python3 -m compileall agents workflows scripts
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
