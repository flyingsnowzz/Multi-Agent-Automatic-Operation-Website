# CrawlerProcessorAgent - 爬虫内容处理专家

## 核心职责

1. **读取爬虫数据库** - 从爬虫数据库读取待处理内容（status=pending）
2. **内容评估** - 评估素材是否可作为后续 TopicAgent 的候选输入
3. **决策引擎** - 决定内容处理方式（discard/pass_to_topic）
4. **去重检测** - 与已发布内容对比，避免重复
5. **流水线对接** - 根据决策结果，将内容路由到对应处理流程

## 输入

- `crawler_db_config` - 爬虫数据库连接配置
- `published_content_db_config` - 已发布内容数据库连接配置
- `evaluation_criteria` - 内容评估标准（`material_score_threshold`、`input_required_fields`、URL/topic 要求）
- `dedup_threshold` - 去重相似度阈值（默认 0.8）
- `decision_rules` - 决策规则（仅 `discard` / `pass_to_topic`）

## 输出

- `processing_result` - 处理结果（discard/pass_to_topic）
- `next_payload` - 传递给 TopicAgent 的素材 payload（如果决策为 pass_to_topic）
- `evaluation_report` - 评估报表（`material_score`、`has_risk`、`source_ok`、`topic_hint`）

## 工作流程

1. **读取爬虫数据库** - 调用 `crawler_db_reader` 工具，读取 status=pending 的内容
2. **去重检测** - 调用 `dedup_checker` 工具，与已发布内容对比
3. **内容评估** - 调用 `content_evaluator` 工具，输出统一素材评估结果
4. **决策** - 根据评估结果与决策规则，决定处理方式
5. **路由** - 仅保留两种结果：
   - `pass_to_topic`：标记 status=pass_to_topic，传递给 TopicAgent
   - `discard`：标记 status=discarded，结束
6. **更新状态** - 更新爬虫数据库中的 status 字段

## 工具清单

| 工具名 | 功能 | 路径 |
|--------|------|------|
| `crawler_db_reader` | 读取爬虫数据库（按 status=pending 读取） | `tools/crawler_db_reader.py` |
| `content_evaluator` | 评估素材可用性并输出统一字段 | `tools/content_evaluator.py` |
| `dedup_checker` | 去重检测（与已发布内容对比） | `tools/dedup_checker.py` |

## 与其他 Agent 的关系

- **上游**：爬虫数据库（外部系统）
- **下游**：
  - 如果决策为 `pass_to_topic` → TopicAgent
  - 如果决策为 `discard` → 结束
- **协作**：DataAgent（获取已发布内容数据用于去重）

## 配置依赖

- **数据库连接**：爬虫数据库、已发布内容数据库
- **LLM 配置**：用于内容评估（可选，部分评估可用规则引擎）
- **决策规则**：丢弃条件与 `pass_to_topic` 条件

## 使用示例

```python
# 初始化 CrawlerProcessorAgent
from agents.crawler_processor_agent import CrawlerProcessorAgent

agent = CrawlerProcessorAgent()

# 处理爬虫内容（默认 dry_run=True，不落库、不调用 LLM）
result = await agent.execute(limit=10, target_keywords=["AI", "多Agent系统"], dry_run=True)

# 根据决策结果路由
for item in result.get("items", []):
    if item.get("decision") == "pass_to_topic":
        payload = item.get("next_payload")
```
