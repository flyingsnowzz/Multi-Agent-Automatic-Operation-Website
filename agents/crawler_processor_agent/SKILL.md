# CrawlerProcessorAgent - 爬虫内容处理专家

## 核心职责

1. **读取爬虫数据库** - 从爬虫数据库读取待处理内容（status=pending）
2. **门禁评估** - 评估素材是否可以进入 ScoringAgent
3. **决策引擎** - 决定内容处理方式（discard/pass_to_scoring）
4. **去重检测** - 与已发布内容对比，避免重复
5. **流水线对接** - 根据决策结果，将内容路由到文章重要性链路

## 输入

- `crawler_db_config` - 爬虫数据库连接配置
- `published_content_db_config` - 已发布内容数据库连接配置
- `evaluation_criteria` - 门禁标准（`min_base_relevance_score`、`min_base_usability_score`、`require_source_ok`、`require_content_complete` 等）
- `dedup_threshold` - 去重相似度阈值（默认 0.8）
- `decision_rules` - 决策规则（仅 `discard` / `pass_to_scoring`）

## 输出

- `processing_result` - 处理结果（discard/pass_to_scoring）
- `next_payload` - 传递给 ScoringAgent 的素材 payload（如果决策为 pass_to_scoring）
- `evaluation_report` - 门禁评估报表（`base_relevance_score`、`base_usability_score`、`source_ok`、`content_complete`、`noise_ratio`）

## 工作流程

1. **读取爬虫数据库** - 调用 `crawler_db_reader` 工具，读取 status=pending 的内容
2. **去重检测** - 调用 `dedup_checker` 工具，与已发布内容对比
3. **门禁评估** - 调用 `content_evaluator` 工具，输出统一门禁评估结果
4. **决策** - 根据评估结果与决策规则，决定处理方式
5. **路由** - 仅保留两种结果：
   - `pass_to_scoring`：标记为通过门禁，传递给 ScoringAgent
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
  - 如果决策为 `pass_to_scoring` → ScoringAgent
  - 如果决策为 `discard` → 结束
- **协作**：DataAgent（获取已发布内容数据用于去重）

说明：

- 当前仓库中仍存在 `TopicAgent` 相关链路，但它属于历史兼容痕迹。
- crawler 的目标架构不再以 `TopicAgent` 作为正式下游，而是以 `ScoringAgent` 作为下一层。

## 配置依赖

- **数据库连接**：爬虫数据库、已发布内容数据库
- **LLM 配置**：可选，仅用于补充评估解释，不作为 crawler 核心职责
- **决策规则**：丢弃条件与 `pass_to_scoring` 条件

## 门禁边界

- **门禁项**：重复、版权风险、来源异常、内容残缺/采集异常、高噪声、不相关、不可用
- **非门禁项**：内容重要性、时效性、通知属性、原文质量、最终 publish/rewrite 分流

## 使用示例

```python
# 初始化 CrawlerProcessorAgent
from agents.crawler_processor_agent import CrawlerProcessorAgent

agent = CrawlerProcessorAgent()

# 处理爬虫内容（默认 dry_run=True，不落库、不调用 LLM）
result = await agent.execute(limit=10, target_keywords=["AI", "多Agent系统"], dry_run=True)

# 根据决策结果路由
for item in result.get("items", []):
    if item.get("decision") == "pass_to_scoring":
        payload = item.get("next_payload")
```
