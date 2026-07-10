# 独立 LangGraph 正式架构说明

这份说明描述当前正式生产入口：`scripts/run_langgraph_batch.py` 批量读取文章、保持 Redis 版 batch scoring 口径，然后把单篇文章交给 `workflows/langgraph_article_pipeline.py` 的 LangGraph 状态图处理。

Redis Streams 版本已经移动到 `legacy/redis_pipeline/`，只用于回滚、对照和代码审查。

## 1. 正式链路

```text
MySQL crawler_news_main
  -> run_langgraph_batch.py
       -> feed/latest/article-ids 选文章
       -> load_source_node 读取原文和分片正文
       -> summarize_crawler_topics 批量评分
       -> run_article_graph
            -> scoring pass-through
            -> quality
            -> rewrite 或 seo
            -> image
            -> cms
            -> save_audit
  -> pipeline_audit / prompt JSONL / CMS
```

## 2. 核心文件

| 文件 | 职责 |
| --- | --- |
| `scripts/run_langgraph_batch.py` | 正式批处理入口，负责 feeder、batch scoring、循环运行、deadletter、mark-used |
| `scripts/run_langgraph_pipeline.py` | 单篇文章调试入口 |
| `workflows/langgraph_article_pipeline.py` | LangGraph 节点、条件分支、审计落库 |
| `scripts/publish_common.py` | 发布前校验、封面复用/生成决策、slug、审计字段更新 |
| `scripts/pipeline_text.py` | 原文正文抽取和清洗 |
| `scripts/prompt_db_logger.py` | prompt / reason / breakdown JSONL 审计 |
| `legacy/redis_pipeline/` | Redis Streams 旧版本，非默认生产入口 |

## 3. 长期无人值守策略

- `--production` 会打开 `--feed --loop --persist-audit --mark-used`。
- CMS 默认仍是 dry-run；真发布必须额外加 `--publish` 且 `.env` 中 `CMS_ENABLE_REAL_PUBLISH=true`。
- feeder 状态写入 `LANGGRAPH_FEED_STATE_PATH`，默认 `output/langgraph_feeder_state.json`。
- feeder 游标在整批处理完成后才推进，避免 scoring/API/DB 中途异常导致文章 id 被跳过。
- feeder 没有候选文章时会按 `LANGGRAPH_FEED_IDLE_BACKOFF_HOURS` 退避检查，默认 `1,2,4,8,12,24` 小时；一旦某轮处理到文章，退避立刻重置，恢复正常 `LANGGRAPH_LOOP_INTERVAL_SECONDS`。
- batch 或单篇 graph 异常写入 `LANGGRAPH_DEADLETTER_PATH`，默认 `output/langgraph_deadletter.jsonl`。
- 低分、source 缺失、重写失败、图片失败等终态会落入 `pipeline_audit`，便于后续排查。

## 4. 封面规则

封面决策集中在 `scripts/publish_common.py::cover_decision`：

- 未重写/转发文章：复用原文封面，不调用图片生成。
- 已重写文章：第一次生成新封面。
- 已重写文章重跑：如果 `pipeline_audit` 里已有生成过的 `image_url` 或 `image_local_path`，复用已生成封面，不重复扣图片额度。

## 5. 常用命令

```bash
# 长期无人值守，CMS dry-run
python3 scripts/run_langgraph_batch.py --production

# 长期无人值守，真实发布
CMS_ENABLE_REAL_PUBLISH=true python3 scripts/run_langgraph_batch.py --production --publish

# 只跑一批，适合调试
python3 scripts/run_langgraph_batch.py --feed --limit 30

# 指定文章调试
python3 scripts/run_langgraph_pipeline.py --article-id 1961 --persist-audit
```

## 6. Code Review 优先看

1. `scripts/run_langgraph_batch.py`
2. `workflows/langgraph_article_pipeline.py`
3. `scripts/publish_common.py`
4. `docker-compose.yml`
5. `.env.example`
6. `README.md`
7. `legacy/redis_pipeline/`
