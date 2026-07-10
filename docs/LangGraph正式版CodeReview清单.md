# LangGraph 正式版 Code Review 清单

这份清单用于 review “Redis 版归档、LangGraph 版转正式”的改动。

## 1. 必看文件

| 文件 | Review 重点 |
| --- | --- |
| `scripts/run_langgraph_batch.py` | feeder、batch scoring、loop、deadletter、mark-used、异常不中断 |
| `workflows/langgraph_article_pipeline.py` | LangGraph 节点顺序、条件分支、状态字段、审计落库 |
| `scripts/publish_common.py` | 封面复用/生成规则、发布前校验、slug、audit 更新 |
| `docker-compose.yml` | 默认 `pipeline` 是否跑 LangGraph；Redis legacy 是否只在 profile 中运行 |
| `Dockerfile` | 默认 CMD 是否指向 LangGraph |
| `Makefile` | `make run` / `make docker-run` 是否指向 LangGraph |
| `.env.example` | LangGraph 参数是否完整，Redis 参数是否标注 legacy |
| `README.md` | 快速启动、真实发布、运维说明是否是 LangGraph 口径 |
| `docs/独立LangGraph架构说明.md` | 正式架构说明是否准确 |
| `docs/LangGraph迁移报告.md` | 迁移结论、风险、回滚说明是否准确 |
| `15-新电脑环境配置教程.md` | 新机器启动说明是否默认 LangGraph |
| `16-Redis流水线代码阅读指南.md` | Redis 是否明确为 legacy |
| `legacy/redis_pipeline/` | 旧文件是否完整归档，内部 import 和启动路径是否可运行 |
| `tests/test_langgraph_article_pipeline.py` | LangGraph 行为测试 |
| `tests/test_worker_publish_cover.py` | 封面规则测试 |
| `tests/test_redis_pipeline.py` | Redis legacy 基础设施测试 |

## 2. 重点风险

- LangGraph 和 Redis legacy 不能同时消费同一批未使用文章。
- `LANGGRAPH_FEED_STATE_PATH` 只能在一批处理完成后推进，不能扫描后立刻推进。
- `--production` 默认仍是 CMS dry-run；真发布必须同时满足 `--publish` 和 `CMS_ENABLE_REAL_PUBLISH=true`。
- 重写文章必须生成新封面；重跑时应复用已生成的新封面；未重写文章复用原图。
- image 和 CMS 节点涉及外部副作用，review 时重点看是否有重复调用、空图发布、真实发布误触发。

## 3. 建议验证命令

```bash
python3 -m py_compile scripts/run_langgraph_batch.py scripts/run_langgraph_pipeline.py workflows/langgraph_article_pipeline.py scripts/publish_common.py
python3 -m py_compile legacy/redis_pipeline/*.py
python3 -m unittest tests.test_worker_publish_cover tests.test_langgraph_article_pipeline tests.test_redis_pipeline
docker compose config >/tmp/multi-agent-compose.yml
```
