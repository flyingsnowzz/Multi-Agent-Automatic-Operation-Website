# 编辑Agent (EditorAgent)

## Agent概述

编辑Agent是多Agent内容生产流水线中的审校环节，负责对初稿进行质量审核、润色优化和最终把关。

## 核心职责

1. **内容审校** - 检查事实准确性、逻辑清晰度、论证完整性
2. **语言润色** - 优化表达、提升可读性、统一语言风格
3. **品牌一致性** - 确保文章符合品牌调性要求
4. **质量评估** - 对文章给出质量评分和改进建议
5. **格式规范** - 检查Markdown格式、图片alt、内外链等

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 初稿文章 | 写作Agent产出的文章 | 写作Agent |
| 选题信息 | 原始选题和关键词要求 | 选题Agent |
| 品牌指南 | 品牌调性、禁用词等 | config/brand_guidelines.yaml |
| 审校标准 | 质量检查清单 | config/editorial_standards.yaml |

## 输出

| 输出项 | 说明 |
|--------|------|
| 审校后文章 | 修改后的最终文章 |
| 质量评分 | 综合评分（1-100） |
| 问题清单 | 发现的问题及修改建议 |
| 润色说明 | 主要润色点的说明 |

## 工作流程

```
初稿文章
   ↓
内容审校（事实/逻辑/完整度）
   ↓
语言润色（表达/风格/可读性）
   ↓
格式检查（Markdown/图片/链接）
   ↓
质量评分
   ↓
审校后文章 + 问题清单
```

## 配置文件

- [[config.yaml]] - Agent参数配置
- [[prompt.md]] - Agent提示词模板
- [[tools/]] - Agent使用的工具

## 运行方式

```python
from agents.editor_agent import EditorAgent

agent = EditorAgent()
result = await agent.execute(
    article={
        "title": "...",
        "content": "...",
        "meta_description": "..."
    },
    topic={
        "primary_keyword": "EMBA报考条件",
        "content_type": "guide"
    }
)
```

## 质量评分标准

| 维度 | 权重 | 评分说明 |
|------|------|---------|
| 内容质量 | 30% | 事实准确、论证完整、有实用价值 |
| 逻辑清晰 | 20% | 结构合理、过渡自然、层次分明 |
| 语言表达 | 20% | 简洁准确、通俗易懂、无语法错误 |
| SEO优化 | 15% | 关键词布局合理、结构SEO友好 |
| 品牌一致 | 15% | 符合品牌调性、无禁用词 |

## 相关文档

- [[../../00-方案概述]]
- [[../../01-Agent架构图]]
- [[../writer_agent/]] - 上游写作Agent
- [[../seo_agent/]] - 下游SEO Agent
