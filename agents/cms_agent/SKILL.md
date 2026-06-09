# CMS Agent (CMS发布Agent)

## Agent概述

CMS Agent 负责把 SEO 优化后的文章转换为 CMS 后端可写入的结构化 payload，并在显式发布开关开启时创建或更新 CMS 文章。它是内容生产流水线的发布出口，默认以 dry-run 方式运行，避免误发。

## 核心职责

1. **内容格式化** - 优先使用 `content_html` 写入 CMS，同时保留 `content_md` 作为 Markdown 源文。
2. **元数据准备** - 设置分类、标签、Slug、摘要、topic_id 等字段。
3. **SEO字段填充** - 写入 SEO 标题、描述、主关键词和 Schema JSON-LD。
4. **图片处理** - 根据配置上传封面图并关联文章；dry-run 时只保留图片 URL。
5. **发布前检查** - 检查正文、分类、封面图、Slug 冲突。
6. **幂等发布** - Slug 冲突时按配置执行 `auto_rewrite`、`overwrite_update` 或 `fail`。
7. **发布审计** - 输出 checks、errors、payload，并按配置保存发布历史。

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| `article.title` | 文章标题 | SEOAgent |
| `article.content_html` | HTML 正文，CMS 写入主字段 | SEOAgent / 渲染步骤 |
| `article.content_md` | Markdown 源文，便于审计和重渲染 | WriterAgent / SEOAgent |
| `article.meta` | SEO 标题、描述、Schema 等 | SEOAgent |
| `article.primary_keyword` | 主关键词 | TopicAgent / SEOAgent |
| `page_info.category` | 分类，会按配置映射为 CMS 分类 | TopicAgent |
| `page_info.tags` | 标签列表，可按配置自动加入主关键词 | TopicAgent / SEOAgent |
| `page_info.slug` | URL 别名；为空时由标题生成 | TopicAgent / SEOAgent |
| `page_info.topic_id` | 选题或内容 ID，用于追踪 | TopicAgent |
| `images.featured_image_url` | 封面图 URL | ImageAgent |
| `images.featured_alt` | 封面图 Alt 文本 | ImageAgent |

## 输出

| 输出项 | 说明 |
|--------|------|
| `article_id` | CMS 文章 ID；dry-run 时为 `null` |
| `article_url` | CMS 文章 URL；dry-run 时为 `null` |
| `status` | `dry_run` / `draft` / `scheduled` / `publish` / `failed` |
| `published_at` | 真实发布完成时间；dry-run 时为 `null` |
| `checks` | 发布前检查结果 |
| `errors` | 错误码列表 |
| `payload` | dry-run 或失败时返回的待发布 payload |

## 发布控制

默认只 dry-run。真实发布必须同时满足：

1. `publishing.dry_run == false`
2. 环境变量 `CMS_ENABLE_REAL_PUBLISH=true`

只要任一条件不满足，Agent 返回 `status: "dry_run"`，不会上传图片、创建文章或更新文章。

## Custom CMS Contract

custom CMS 按 `config.yaml -> cms.custom.post_contract` 生成请求体。默认字段：

```json
{
  "title": "文章标题",
  "content_html": "<p>HTML正文</p>",
  "content_md": "# Markdown源文",
  "excerpt": "摘要",
  "slug": "article-slug",
  "category": "emba-guide",
  "tags": ["EMBA", "报考指南"],
  "featured_image": "https://cdn.example.com/cover.webp",
  "meta": {
    "seo_title": "SEO标题",
    "seo_description": "SEO描述",
    "focus_keyword": "主关键词",
    "schema_json": {}
  },
  "status": "draft",
  "publish_date": null,
  "topic_id": "uuid"
}
```

## Slug冲突策略

| 策略 | 行为 |
|------|------|
| `auto_rewrite` | 已存在时尝试 `slug-2`、`slug-3`，直到可用或达到上限 |
| `overwrite_update` | 查询同 slug 文章，找到后执行 `PATCH /posts/{id}` |
| `fail` | 检测到冲突即失败，返回 `slug_unique` 错误 |

## 配置文件

- [[config.yaml]] - Agent 参数、custom CMS contract、dry-run、slug 策略、图片和日志配置
- [[prompt.md]] - Agent 提示词模板和字段契约
- [[tools/]] - `cms_client`、`media_uploader` 等工具

## 运行方式

```python
from agents.cms_agent import CMSAgent

agent = CMSAgent()
result = await agent.execute(
    article={
        "title": "EMBA报考条件有哪些？2026最全解读",
        "content_html": "<h1>EMBA报考条件有哪些</h1><p>...</p>",
        "content_md": "# EMBA报考条件有哪些\n\n...",
        "meta": {
            "seo_title": "EMBA报考条件有哪些？2026最全解读 | 品牌名",
            "seo_description": "本文详细介绍EMBA报考条件。",
            "schema_json": {"@type": "Article"},
        },
        "primary_keyword": "EMBA报考条件",
    },
    page_info={
        "category": "emba",
        "tags": ["EMBA", "报考指南", "2026"],
        "slug": "emba-conditions-2026-guide",
        "topic_id": "topic-001",
    },
    images={
        "featured_image_url": "https://cdn.example.com/images/emba-guide-featured.jpg",
        "featured_alt": "EMBA报考条件指南封面图",
    },
)
```

dry-run 示例输出：

```json
{
  "article_id": null,
  "article_url": null,
  "status": "dry_run",
  "published_at": null,
  "checks": {
    "content_not_empty": true,
    "category_assigned": true,
    "featured_image_set": true,
    "slug_unique": true,
    "slug_checked": false,
    "slug_resolution": {}
  },
  "errors": [],
  "payload": {}
}
```
