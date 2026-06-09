# 图片Agent (ImageAgent)

## Agent概述

图片Agent负责为文章生成或选择合适的配图，包括封面图和文中插图。

## 核心职责

1. **封面图生成** - 为文章生成吸引人的封面图
2. **插图生成** - 为文章内容生成相关插图
3. **图片优化** - 压缩和调整图片尺寸
4. **Alt文本生成** - 为图片生成SEO友好的描述

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章信息 | 标题、关键词、内容摘要 | 写作Agent |
| 图片需求 | 需要几张、什么类型 | 选题Agent |

## 输出

| 输出项 | 说明 |
|--------|------|
| featured_image_url | 封面图 URL |
| featured_alt | 封面图 Alt 文本 |
| featured_prompt | 封面图提示词（可复现） |
| inline_images | 文中插图数组（含 url/alt/prompt/position） |
| license | 版权/来源信息（例如 source=generated, provider=openai） |

## 相关文档

- [[../writer_agent/]] - 上游写作Agent
- [[../cms_agent/]] - 下游CMS Agent
