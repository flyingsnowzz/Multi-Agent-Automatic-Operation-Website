# 选题Agent (TopicAgent)

## Agent概述

选题Agent是多Agent内容生产流水线的入口，负责发现高价值的内容选题。

## 核心职责

1. **关键词研究** - 发现用户搜索意图强、竞争度适中的关键词
2. **热点挖掘** - 追踪行业热点和趋势话题
3. **选题生成** - 基于数据和趋势生成具体选题建议
4. **竞争分析** - 评估选题的竞争程度和可执行性

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 行业关键词 | 种子关键词列表 | config/keywords.yaml |
| 历史文章数据 | 已发布文章的表现数据 | 数据库 |
| 竞品动态 | 竞品最近发布的内容 | 竞品Agent |
| 热点趋势 | 行业热点和搜索趋势 | 趋势API |

## 输出

| 输出项 | 说明 |
|--------|------|
| 选题列表 | 5-10个选题建议 |
| 每个选题包含 | 标题、目标关键词、搜索量、竞争度、推荐理由、预估难度 |

## 配置文件

- [[config.yaml]] - Agent参数配置
- [[prompt.md]] - Agent提示词模板
- [[tools/]] - Agent使用的工具

## 实现文件

- `tools/keyword_research.py` - 关键词研究工具
- `tools/trend_detection.py` - 趋势检测工具
- `tools/serp_analysis.py` - SERP分析工具

## 运行方式

```python
from agents.topic_agent import TopicAgent

agent = TopicAgent()
result = await agent.execute(
    keywords=["EMBA", "商学院"],
    min_search_volume=100,
    max_kd=30,
    mode="mock"
)
```

## 相关文档

- [[../../00-方案概述]]
- [[../../01-Agent架构图]]
- [[../../03-工作流编排]]
- [[../../04-定时任务方案]]
