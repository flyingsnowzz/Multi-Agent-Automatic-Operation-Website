# CrawlerProcessorAgent - 爬虫内容处理专家

## 核心职责

1. **读取爬虫数据库** - 从爬虫数据库读取待处理内容（status=pending）
2. **内容评估** - 评估内容质量、相关性、SEO潜力
3. **决策引擎** - 决定内容处理方式（丢弃/直接发布/改写）
4. **去重检测** - 与已发布内容对比，避免重复
5. **流水线对接** - 根据决策结果，将内容路由到对应处理流程

## 输入

- `crawler_db_config` - 爬虫数据库连接配置
- `published_content_db_config` - 已发布内容数据库连接配置
- `evaluation_criteria` - 内容评估标准（质量阈值、相关性阈值、SEO潜力阈值）
- `dedup_threshold` - 去重相似度阈值（默认 0.8）
- `decision_rules` - 决策规则（丢弃条件、直接发布条件、改写条件）

## 输出

- `processing_result` - 处理结果（discard/publish/rewrite）
- `content_to_publish` - 直接发布的内容（如果决策为 publish）
- `content_to_rewrite` - 需要改写的内容（如果决策为 rewrite）
- `rewrite_brief` - 改写概要（传递给 WriterAgent）
- `evaluation_report` - 评估报表（质量评分、相关性评分、SEO潜力评分）

## 工作流程

1. **读取爬虫数据库** - 调用 `crawler_db_reader` 工具，读取 status=pending 的内容
2. **去重检测** - 调用 `dedup_checker` 工具，与已发布内容对比
3. **内容评估** - 调用 `content_evaluator` 工具，评估质量、相关性、SEO潜力
4. **决策** - 根据评估结果与决策规则，决定处理方式
5. **路由** - 根据决策结果，路由到对应流程：
   - 丢弃：标记 status=discarded，结束
   - 直接发布：标记 status=ready_to_publish，传递给 CMSAgent
   - 改写：标记 status=ready_to_rewrite，传递改写概要给 WriterAgent
6. **更新状态** - 更新爬虫数据库中的 status 字段

## 工具清单

| 工具名 | 功能 | 路径 |
|--------|------|------|
| `crawler_db_reader` | 读取爬虫数据库（按 status=pending 读取） | `tools/crawler_db_reader.py` |
| `content_evaluator` | 评估内容质量、相关性、SEO潜力 | `tools/content_evaluator.py` |
| `dedup_checker` | 去重检测（与已发布内容对比） | `tools/dedup_checker.py` |

## 与其他 Agent 的关系

- **上游**：爬虫数据库（外部系统）
- **下游**：
  - 如果决策为"直接发布" → CMSAgent
  - 如果决策为"改写" → WriterAgent → EditorAgent → SEOAgent → ImageAgent → CMSAgent
- **协作**：DataAgent（获取已发布内容数据用于去重）

## 配置依赖

- **数据库连接**：爬虫数据库、已发布内容数据库
- **LLM 配置**：用于内容评估（可选，部分评估可用规则引擎）
- **决策规则**：丢弃条件、直接发布条件、改写条件

## 使用示例

```python
# 初始化 CrawlerProcessorAgent
from agents.crawler_processor_agent import CrawlerProcessorAgent

agent = CrawlerProcessorAgent()

# 处理爬虫内容（默认 dry_run=True，不落库、不调用 LLM）
result = await agent.execute(limit=10, target_keywords=["AI", "多Agent系统"], dry_run=True)

# 根据决策结果路由
for item in result.get("items", []):
    if item.get("decision") == "publish":
        payload = item.get("next_payload")
    elif item.get("decision") == "rewrite":
        payload = item.get("next_payload")
```
