# SEOAgent (SEO优化Agent)

## Agent概述

SEOAgent是多Agent内容生产流水线中的搜索引擎优化环节，负责对文章进行全面的SEO优化，包括关键词布局、技术SEO建议、Schema标记等。

## 核心职责

1. **关键词优化** - 优化关键词密度、分布、语义相关性
2. **标题优化** - 优化H1标题，提高点击率和搜索匹配度
3. **Meta优化** - 优化Meta Title和Description，提高展示效果
4. **结构优化** - 建议标题层级、内容结构优化
5. **技术SEO** - 生成Schema标记、提供内链建议、技术SEO检查

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章内容 | 待优化的文章 | 写作Agent / 编辑Agent |
| 关键词策略 | 目标关键词、相关词 | 选题Agent |
| 页面信息 | URL、分类、标签 | CMSAgent |

## 输出

| 输出项 | 说明 |
|--------|------|
| 优化后文章 | 已包含SEO优化的文章 |
| SEO报告 | 包含各项优化指标评分 |
| Schema标记 | JSON-LD格式的Schema代码 |
| Meta信息 | Title、Description |
| 内链建议 | 推荐添加的内链 |

## 配置文件

- [[config.yaml]] - Agent参数配置
- [[prompt.md]] - Agent提示词模板
- [[tools/]] - Agent使用的工具

## 运行方式

```python
from agents.seo_agent import SEOAgent

agent = SEOAgent()
result = await agent.execute(
    article={"title": "...", "content": "..."},
    keywords={
        "primary": "EMBA报考条件",
        "secondary": ["EMBA学费", "EMBA院校"]
    },
    page_info={"url": "/emba/guide", "category": "EMBA"}
)
```

## 相关文档

- [[../../00-方案概述]]
- [[../writer_agent/]] - 上游写作Agent
- [[../editor_agent/]] - 上游编辑Agent
- [[../cms_agent/]] - 下游CMS Agent
