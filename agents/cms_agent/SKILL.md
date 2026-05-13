# CMS Agent (CMS发布Agent)

## Agent概述

CMS Agent负责将优化后的文章发布到网站CMS系统，是内容生产流水线的最后一环。

## 核心职责

1. **内容格式化** - 将文章转换为CMS所需格式
2. **元数据准备** - 设置分类、标签、别名等
3. **图片上传** - 上传并关联文章图片
4. **草稿创建** - 在CMS中创建文章草稿
5. **发布审核** - 提交发布或直接发布

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章内容 | SEO优化后的文章 | SEOAgent |
| 分类信息 | 分类、标签、别名 | 选题Agent |
| 图片信息 | 图片URL和alt文本 | ImageAgent |

## 输出

| 输出项 | 说明 |
|--------|------|
| 文章URL | 发布的文章URL |
| 文章ID | CMS中的文章ID |
| 状态 | 发布状态 |

## 配置文件

- [[config.yaml]] - Agent参数配置
- [[prompt.md]] - Agent提示词模板
- [[tools/]] - Agent使用的工具

## 运行方式

```python
from agents.cms_agent import CMSAgent

agent = CMSAgent()
result = await agent.execute(
    article={"title": "...", "content": "...", "meta": {...}},
    page_info={"category": "emba", "tags": ["emba指南"], "slug": "emba-guide"}
)
```
