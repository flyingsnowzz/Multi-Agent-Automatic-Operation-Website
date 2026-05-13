# 写作Agent (WriterAgent)

## Agent概述

写作Agent是多Agent内容生产流水线的核心，负责根据大纲和素材生成高质量的原创文章。

## 核心职责

1. **文章撰写** - 根据大纲撰写完整的Markdown文章
2. **品牌调性保持** - 确保文章风格符合品牌调性
3. **SEO友好** - 合理布局关键词，优化内容结构
4. **可读性优化** - 使用简洁段落、适当列表、吸引人的开头结尾

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 选题信息 | 标题、目标关键词、搜索意图、内容类型 | 选题Agent |
| 文章大纲 | 详细的大纲结构（多级标题） | 调研Agent |
| 调研素材 | 收集的数据、案例、引用来源 | 调研Agent |
| 品牌指南 | 品牌调性、禁止词、必须用词 | config/brand_guidelines.yaml |
| 参考范文 | 过往高质量文章的写作风格 | 数据库 |

## 输出

| 输出项 | 说明 |
|--------|------|
| 文章正文 | Markdown格式的完整文章 |
| Meta Description | SEO元描述（150-160字符） |
| 关键词布局 | 关键词出现位置统计 |
| 内链建议 | 推荐在哪些位置添加内链 |
| 图片alt文本建议 | 每张配图建议的alt文本 |
| 字数统计 | 总字数、各段落字数 |
| 阅读时间预估 | 基于字数计算 |

## 配置文件

- [[config.yaml]] - Agent参数配置
- [[prompt.md]] - Agent提示词模板
- [[tools/]] - Agent使用的工具

## 运行方式

```python
from agents.writer_agent import WriterAgent

agent = WriterAgent()
result = await agent.execute(
    topic={
        "title": "EMBA报考指南：条件、流程与院校选择",
        "target_keywords": ["EMBA报考条件", "EMBA流程"],
        "content_type": "guide",
        "search_intent": "informational"
    },
    outline={
        "h1": "EMBA报考指南",
        "sections": [
            {"h2": "EMBA与MBA的区别", "points": [...]},
            {"h2": "报考基本条件", "points": [...]},
        ]
    },
    materials={
        "data": [...],
        "cases": [...],
        "quotes": [...]
    }
)
```

## 质量标准

| 维度 | 标准 |
|------|------|
| 字数 | 1500-3000字（根据内容类型调整） |
| 段落长度 | 每段不超过150字 |
| 关键词密度 | 主关键词1-2%，次关键词0.5-1% |
| 可读性 | 中文Flesch阅读难度适中 |
| 原创度 | >85%（通过内容检测） |

## 相关文档

- [[../../00-方案概述]]
- [[../../01-Agent架构图]]
- [[../../03-工作流编排]]
- [[../topic_agent/]] - 上游选题Agent
- [[../research_agent/]] - 上游调研Agent
- [[../editor_agent/]] - 下游编辑Agent
