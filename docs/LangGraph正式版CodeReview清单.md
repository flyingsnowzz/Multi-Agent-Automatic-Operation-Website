# LangGraph 正式版 Code Review 清单

这份清单用于 review 当前唯一正式链路：LangGraph 批处理流水线。

## 1. 必看文件

| 文件 | Review 重点 |
| --- | --- |
| `scripts/run_langgraph_batch.py` | feeder、batch scoring、loop、deadletter、mark-used、异常不中断 |
| `scripts/langgraph_daemon.sh` | 本机后台启动/停止/status/logs，stdout/stderr 是否写入 `logs/langgraph_batch.log` |
| `workflows/langgraph_article_pipeline.py` | LangGraph 节点顺序、条件分支、状态字段、审计落库 |
| `scripts/publish_common.py` | 封面复用/生成规则、发布前校验、slug、audit 更新 |
| `docker-compose.yml` | 默认 `pipeline` 是否只跑 LangGraph，是否不再包含 LangGraph pipeline |
| `Dockerfile` | 默认 CMD 是否指向 LangGraph |
| `Makefile` | `make run` / `make docker-run` 是否指向 LangGraph |
| `.env.example` | LangGraph 参数是否完整，是否没有混入旧队列参数 |
| `README.md` | 快速启动、真实发布、运维说明是否是 LangGraph 口径 |
| `docs/独立LangGraph架构说明.md` | 正式架构说明是否准确 |
| `docs/LangGraph迁移报告.md` | 迁移结论、删除旧队列后的风险说明是否准确 |
| `15-新电脑环境配置教程.md` | 新机器启动说明是否默认 LangGraph |
| `tests/test_langgraph_article_pipeline.py` | LangGraph 行为测试 |
| `tests/test_worker_publish_cover.py` | 封面规则测试 |

## 2. 重点风险

- 项目不应再包含 LangGraph 之外的旧 worker、旧队列 profile 或旧队列回滚入口。
- `LANGGRAPH_FEED_STATE_PATH` 只能在一批处理完成后推进，不能扫描后立刻推进。
- `--production` 默认仍是 CMS dry-run；真发布必须同时满足 `--publish` 和 `CMS_ENABLE_REAL_PUBLISH=true`。
- 重写文章必须生成新封面；重跑时应复用已生成的新封面；未重写文章复用原图。
- image 和 CMS 节点涉及外部副作用，review 时重点看是否有重复调用、空图发布、真实发布误触发。

## 3. 建议验证命令

```bash
python3 -m py_compile scripts/run_langgraph_batch.py workflows/langgraph_article_pipeline.py scripts/publish_common.py
bash -n scripts/langgraph_daemon.sh
python3 -m unittest tests.test_worker_publish_cover tests.test_langgraph_article_pipeline tests.test_langgraph_batch_runner
docker compose config >/tmp/multi-agent-compose.yml
```
