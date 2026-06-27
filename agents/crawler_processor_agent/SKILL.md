# CrawlerProcessorAgent - 爬虫入口处理器

## 核心职责

1. **读取待处理内容** - 从爬虫数据库读取 `status=pending` 的记录
2. **结构标准化** - 统一字段格式，整理为稳定的输入结构
3. **基础校验** - 只校验 `title`、`content`、`source_url` 等最低要求
4. **交接 Review** - 为后续独立 `Review` 阶段生成统一 payload
5. **状态回写** - dry-run 关闭时写回中性处理状态

## 不属于它的职责

- 不做去重检测
- 不做质量、相关性、SEO 评分
- 不做 `publish / rewrite / discard` 业务分流
- 不直接决定是否进入 `CMSAgent`

## 输入

- `crawler_db_config` - 爬虫数据库连接配置
- `evaluation_criteria.input_required_fields` - 基础字段自检范围
- `target_keywords` - 作为后续 Review 阶段的上下文一并传递

## 输出

- `processing_result` - `handoff_to_review` 或 `error`
- `next_payload` - 传递给 `Review` 阶段的标准化素材
- `validation` - 基础字段校验结果

## 工作流程

1. **读取爬虫数据库** - 调用 `crawler_db_reader` 读取待处理内容
2. **标准化输入** - 清洗标题、正文、来源链接等基础字段
3. **基础校验** - 只做技术层自检，不做业务判断
4. **生成交接 payload** - 把素材交给后续 `Review` 阶段
5. **更新状态** - 标记 crawler 已完成入口处理

## 工具清单

| 工具名 | 功能 | 路径 |
|--------|------|------|
| `crawler_db_reader` | 读取爬虫数据库（按 `status=pending` 读取） | `tools/crawler_db_reader.py` |

## 与其他 Agent 的关系

- **上游**：爬虫数据库（外部系统）
- **下游**：独立 `Review` 阶段
- **边界**：`Review` 才承担去重、评分和后续业务分流

## 使用示例

```python
from agents.crawler_processor_agent import CrawlerProcessorAgent

agent = CrawlerProcessorAgent()
result = await agent.execute(limit=10, target_keywords=["AI", "多Agent系统"], dry_run=True)

for item in result.get("items", []):
    if item.get("decision") == "handoff_to_review":
        payload = item.get("next_payload")
```
