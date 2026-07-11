# LangGraph 正式版代码阅读指南

这份文档的目标不是做完整 code review，而是帮你把代码读懂。建议按顺序读，不要一开始就钻进每个 Agent 的 LLM 细节。

## 1. 先看整体入口

从这里开始：

```text
scripts/run_langgraph_batch.py
```

第一遍只看这些函数：

| 函数 | 你要看懂什么 |
| --- | --- |
| `parse_args()` | 命令行参数和 `.env` 怎么控制运行方式 |
| `_load_feed_states()` | LangGraph feeder 怎么从 MySQL 找下一批文章 |
| `_run_one_batch()` | 一批文章如何评分、进 graph、mark used |
| `main()` | 为什么 `--production` 可以长期无人值守 |
| `_feed_idle_sleep_seconds()` | feeder 没文章时为什么按 1/2/4/8/12/24 小时退避 |

重点理解：

```text
--production
  = --feed
  + --loop
  + --persist-audit
  + --mark-used
```

注意：CMS 真实发布不是 `--production` 自动打开的，还必须额外加 `--publish`，并且 `.env` 里 `CMS_ENABLE_REAL_PUBLISH=true`。

feed 模式没有文章时不会每 60 秒一直空跑。默认退避节奏是：

```text
第一次空 -> 1 小时后再看
第二次空 -> 2 小时后再看
第三次空 -> 4 小时后再看
第四次空 -> 8 小时后再看
第五次空 -> 12 小时后再看
之后持续 -> 24 小时检查一次
```

只要某一轮发现并处理了文章，退避计数就清零，恢复正常 `LANGGRAPH_LOOP_INTERVAL_SECONDS`。

## 2. 再看 Graph 主流程

第二个文件：

```text
workflows/langgraph_article_pipeline.py
```

先看 `build_article_graph()`，不要先看每个 node 的内部实现。

你要先把这条线看懂：

```text
load_source
  -> scoring
  -> quality
  -> rewrite 或 seo
  -> image
  -> cms
  -> save_audit
```

然后再按这个顺序看 node：

| Node | 重点 |
| --- | --- |
| `load_source_node()` | 文章是不是从 `crawler_news_main` + 分片正文表读全了 |
| `scoring_node()` | batch runner 已经给了 `ai_score` 时，这里只是 pass-through |
| `quality_node()` | 原文质量分怎么决定直发还是重写 |
| `rewrite_node()` | Research/Writer/二次 Quality/Editor 怎么串起来 |
| `seo_node()` | SEO 字段如何生成 |
| `image_node()` | 封面到底复用还是生成 |
| `cms_node()` | dry-run 和真实发布如何隔离 |
| `save_audit_node()` | 最终状态怎么落到 `pipeline_audit` |

## 3. 单独看封面规则

第三个文件：

```text
scripts/publish_common.py
```

重点看：

```text
is_forwarded_article()
cover_decision()
validate_cover_ready()
validate_publish_prerequisites()
fetch_existing_cover()
```

当前封面规则：

```text
未重写/转发文章
  -> 复用原文封面

已重写文章，且之前没生成过图
  -> 生成新封面

已重写文章，且 pipeline_audit 已有生成图
  -> 复用已生成的新封面
```

这块是你之前问 1961 / 1962 封面问题时最该看的地方。

## 4. 再看正式启动方式

第四批文件：

```text
docker-compose.yml
Dockerfile
Makefile
start.sh
.env.example
README.md
15-新电脑环境配置教程.md
```

你要确认这些事情：

- 默认 `pipeline` 服务跑的是 LangGraph。
- Compose 中不应再出现 LangGraph 之外的旧 worker/profile。
- `make run` 跑的是 LangGraph。
- `.env.example` 里的 LangGraph 参数是完整的。
- 真实发布必须有双保险：`--publish` + `CMS_ENABLE_REAL_PUBLISH=true`。

## 5. 看日志和追踪工具

LangGraph 之外的旧 worker 已删除，排查时不要再找队列或 stream。当前追踪入口是：

```text
logs/langgraph_batch.log
logs/prompt_audit/YYYY-MM-DD.jsonl
output/langgraph_deadletter.jsonl
scripts/trace_langgraph_article.py
```

## 6. 推荐阅读顺序

第一遍，只读主线：

```text
README.md
docs/独立LangGraph架构说明.md
scripts/run_langgraph_batch.py
workflows/langgraph_article_pipeline.py 的 build_article_graph()
```

第二遍，看关键风险：

```text
scripts/run_langgraph_batch.py::_load_feed_states
scripts/run_langgraph_batch.py::_run_one_batch
workflows/langgraph_article_pipeline.py::image_node
workflows/langgraph_article_pipeline.py::cms_node
scripts/publish_common.py::cover_decision
```

第三遍，看部署和追踪：

```text
docker-compose.yml
Makefile
Dockerfile
scripts/langgraph_daemon.sh
scripts/trace_langgraph_article.py
```

第四遍，看测试：

```text
tests/test_langgraph_article_pipeline.py
tests/test_worker_publish_cover.py
```

## 7. 读代码时带着这些问题

读 `run_langgraph_batch.py` 时问：

- 文章从哪里来？
- 哪些文章会被跳过？
- 哪些文章会被标记 used？
- 进程崩了会不会跳过文章？
- batch 异常会不会把长期任务打死？

读 `langgraph_article_pipeline.py` 时问：

- 每个 node 输入输出哪些 state 字段？
- stop_reason 在哪里设置？
- 低分、低质量、重写失败、图片失败分别怎么结束？
- image 和 CMS 会不会重复执行外部副作用？

读 `publish_common.py` 时问：

- 什么算“重写文章”？
- 什么算“转发/未重写文章”？
- 重跑时为什么不会重复生成封面？
- 什么情况下禁止发布？

## 8. 最小验证命令

```bash
python3 -m py_compile scripts/run_langgraph_batch.py workflows/langgraph_article_pipeline.py scripts/publish_common.py
python3 -m unittest tests.test_worker_publish_cover tests.test_langgraph_article_pipeline tests.test_langgraph_batch_runner
```

如果本机有 Docker Compose：

```bash
docker compose config >/tmp/multi-agent-compose.yml
```
