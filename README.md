# 多Agent自动运营网站

当前生产候选入口是 Redis Streams 流水线：

```text
MySQL crawler_news_main
  -> Redis pipeline:scoring
  -> worker_scoring
  -> Redis pipeline:quality
  -> worker_quality
  -> Redis pipeline:rewrite 或 pipeline:publish
  -> worker_rewrite
  -> worker_publish
  -> CMSAgent dry-run / real publish
```

> `scripts/run_production.py` 是旧入口，不作为上线链路使用。

## 快速开始

换电脑或从零配置时，先看 [15-新电脑环境配置教程.md](15-新电脑环境配置教程.md)。下面是日常快速启动摘要。

### 1. 准备环境变量

```bash
cp .env.example .env
```

至少需要配置：

- `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE`
- `REDIS_URL`，或 `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD`
- `ARTICLE_SCORING_API_KEY` / `ARTICLE_SCORING_MODEL` / `ARTICLE_SCORING_BASE_URL`
- `WRITER_AGENT_API_KEY` / `WRITER_AGENT_MODEL` / `WRITER_AGENT_BASE_URL`

CMS 未接入时保持：

```bash
CMS_ENABLE_REAL_PUBLISH=false
```

### 2. Docker 一键部署

推荐部署方式是 Docker Compose：MySQL、Redis 和 pipeline worker 都由 Docker 托管，容器退出后会自动重启。

```bash
docker compose up -d --build mysql redis pipeline
```

或用 Makefile：

```bash
make docker-run
```

查看 pipeline 日志：

```bash
docker compose logs -f pipeline
```

检查队列健康状态：

```bash
docker compose exec pipeline python3 scripts/monitor_pipeline.py --json
```

默认 `pipeline` 使用 `.env` 里的 `PIPELINE_ARGS` 启动，默认是 dry-run：

```env
PIPELINE_ARGS=--feed --dry-run --feed-interval 600 --feed-limit 100 --feed-max-inflight 300 --scoring 1 --quality 1 --rewrite 4 --publish-workers 2
```

真实发布时需要同时打开 `.env` 和命令参数：

```env
CMS_ENABLE_REAL_PUBLISH=true
PIPELINE_ARGS=--feed --publish --feed-interval 600 --feed-limit 100 --feed-max-inflight 300 --scoring 1 --quality 1 --rewrite 4 --publish-workers 2
```

### 3. 只启动基础服务，本机跑 Python

推荐用 Docker 启动 MySQL 和 Redis。MySQL 首次启动会通过 `docker/mysql/init/01-create-databases.sql` 创建 `multi_agent_cms` 和 `research_article_data`。

```bash
docker compose up -d mysql redis
```

如果还需要本地 Postgres/Mongo/MinIO/Qdrant 辅助服务：

```bash
docker compose up -d
```

### 4. 运行 Redis 流水线

只启动 worker，dry-run 发布：

```bash
python3 scripts/run_redis_workers.py --dry-run
```

启动 MySQL feeder，持续把新增爬虫文章推入 Redis：

```bash
python3 scripts/run_redis_workers.py --feed --dry-run --feed-interval 60 --feed-limit 20 --feed-max-inflight 20
```

`--feed-interval` 是数据库轮询间隔，`--feed-limit` 是每轮最多灌入篇数，`--feed-max-inflight` 是整条 Redis 流水线未完成文章上限；达到上限时 feeder 会暂停灌入，等 worker 消化后再继续。

一次性灌入一批文章再启动 worker：

```bash
python3 scripts/run_redis_workers.py --fill --dry-run
```

真实发布需要同时满足命令行和环境变量两道开关：

```bash
CMS_ENABLE_REAL_PUBLISH=true python3 scripts/run_redis_workers.py --feed --publish --feed-interval 60 --feed-limit 20 --feed-max-inflight 20
```

## 运维说明

- 失败消息会按 `REDIS_MAX_RETRIES` 重试，超过阈值后进入 `pipeline:deadletter`。
- worker 启动时会按 `REDIS_PENDING_IDLE_MS` / `REDIS_PENDING_CLAIM_COUNT` 尝试接管超时 pending 消息，并在日志里记录 claim 结果。
- prompt、scoring reason/breakdown、quality reason/suggestion 等大段审计内容写入 `PROMPT_AUDIT_LOG_DIR` 下的 JSONL 文件，默认 `logs/prompt_audit/YYYY-MM-DD.jsonl`，不写入 MySQL。
- `worker_scoring` 会把正文 payload 传给后续 Quality/Rewrite，避免空内容评分。
- `scripts/monitor_pipeline.py` 可检查 deadletter、pending 和本机 worker 进程，适合接 cron/systemd 告警。
- `scripts/run_auto.sh` 和 `make run` 都会调用 Redis 版 supervisor。
- TopicAgent 是废弃入口，相关旧测试已标记跳过。

## 常用命令

```bash
python3 -m pytest -q
python3 -m compileall agents workflows scripts
make run
python3 scripts/monitor_pipeline.py --require-workers
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
