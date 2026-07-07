# CMS Agent (CMS发布Agent)

## Agent概述

这套发布链路里有三个不同层次，它们不是同一个东西：

- `CMSAgent`：发布流程编排层
- `CMSClient / MediaUploader`：CMS 后端适配层
- `CMS 后端`：真正接收、存储、更新、发布文章和媒体的系统

`CMSAgent` 负责把已经进入发布链路的内容整理成统一发布语义 payload，并在显式发布开关开启时调用 `CMSClient / MediaUploader` 执行发布。它默认以 dry-run 方式运行，避免误发。
它的定位是“发布完整性检查 + 发布执行层”。

`CMSAgent` 不负责内容价值判断、写作质量判断、改写决策或正文生产，也不直接处理 custom CMS 的字段映射、响应路径解析或 provider 专属协议细节。
- `CMSAgent` 不直接处理 WordPress/Yoast/RankMath 专属 meta 字段；这类 provider 映射属于 `CMSClient / MediaUploader`。

## 分层职责

### CMSAgent

- 接收 `article`、`page_info`、`images`
- 组装统一发布语义 payload
- 执行业务校验，如标题、正文、分类、封面图、slug
- 控制 dry-run 与真实发布闸门
- 决定调用 create 还是 update
- 汇总 `checks`、`errors`、`warnings`、`missing_fields`、`repair_hints`、`blocking`、`payload`、`article_id`、`article_url`、`status`、`slug`、`provider`、`payload_summary`

### CMSClient / MediaUploader

- 认证
- slug 查询
- `create_post / update_post / get_post`
- 图片上传
- custom / wordpress / ghost / strapi 等 provider 差异处理
- request body 映射
- response 解析并归一化为统一结果

### CMS 后端

- 接收 HTTP 请求
- 保存文章和媒体
- 维护 slug 唯一性
- 返回文章 ID、URL、状态和错误信息

## 核心职责

1. **内容格式化** - 优先使用 `content_html` 写入 CMS；缺失时回退到 `content_md/content`，同时保留 Markdown 源文。
2. **元数据准备** - 设置分类、标签、Slug、摘要、topic_id 等字段。
3. **SEO字段承接** - 保留上游已经产出的 SEO 信息，作为统一发布语义的一部分。
4. **图片处理** - 根据配置上传封面图并关联文章；dry-run 时只保留图片 URL。
5. **发布前检查** - 检查标题、正文、Slug、发布状态、分类、封面图和 Slug 冲突。
6. **幂等发布** - Slug 冲突时按配置执行 `auto_rewrite`、`overwrite_update` 或 `fail`。
7. **发布审计** - 输出 checks、errors、payload，并按配置保存发布历史。

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| `article.title` | 文章标题；发布基础必需项 | SEOAgent / EditorAgent |
| `article.content_html` | HTML 正文，CMS 写入优先字段 | SEOAgent / 渲染步骤 |
| `article.content_md` | Markdown 源文，便于审计和重渲染；HTML 缺失时可作为正文回退 | WriterAgent / SEOAgent |
| `article.content` | 兼容字段；`content_md` 缺失时作为正文回退 | WriterAgent / 工作流适配层 |
| `article.meta` | SEO 标题、描述、Schema 等 | SEOAgent |
| `article.primary_keyword` | 主关键词 | SEOAgent / Redis payload |
| `page_info.category` | 分类，会按配置映射为 CMS 分类 | Redis payload |
| `page_info.tags` | 标签列表，可按配置自动加入主关键词 | SEOAgent / Redis payload |
| `page_info.slug` | URL 别名；为空时由标题生成 | worker_cms.py |
| `page_info.topic_id` | 选题或内容 ID，用于追踪 | Redis payload |
| `images.featured_image_url` | 封面图 URL；按配置可成为发布必需项 | ImageAgent |
| `images.featured_alt` | 封面图 Alt 文本 | ImageAgent |

输入口径说明：

- CMS 只接收已经被上游判定为“进入发布链路”的内容。
- 本次实现不新增 `publish_intent` 字段，但该前提必须作为工作流调用约束存在。
- 标题、正文、分类属于基础发布输入；封面图是否必需由配置控制。
- request body 的字段名如何映射到 CMS 后端，由 `CMSClient / MediaUploader` 负责，不由 `CMSAgent` 直接决定。
- slug 为空时，当前代码会根据 `topic_id / article.id / page_info.id / title / content` 生成稳定 slug。
- 封面图 URL 可以来自 `article.featured_image_url`，也可以来自 `images.featured_image_url / cover_url / cover_image_url`。

## 输出

| 输出项 | 说明 |
|--------|------|
| `article_id` | CMS 文章 ID；dry-run/失败时为 `null` |
| `article_url` | CMS 文章 URL；dry-run/失败时为 `null`，update 场景会尽量补齐 |
| `status` | `dry_run` / `published` / `draft` / `scheduled` / `publish_blocked` / `retry_pending` / `failed` |
| `published_at` | 真实发布完成时间；dry-run/失败时为 `null` |
| `slug` | 最终使用的 slug，包含自动生成或冲突改写后的结果 |
| `provider` | CMS provider，如 `custom` / `wordpress` |
| `checks` | 发布前检查结果；只暴露统一业务检查项 |
| `errors` | 错误码列表 |
| `warnings` | 非阻断问题列表，用于后续优化 |
| `missing_fields` | 当前缺失或阻断发布的字段列表 |
| `repair_hints` | 人工补齐或重试建议 |
| `blocking` | 当前是否为阻断型失败 |
| `source` / `candidate` | 从 `article` 或 `page_info` 透传的来源追踪字段 |
| `topic_id` | 当前内容的 topic/content 追踪 ID |
| `payload_summary` | 发布摘要，用于审计和列表展示 |
| `payload` | dry-run 或失败时返回的待发布 payload |

## 发布控制

默认只 dry-run。真实发布必须同时满足：

1. `publishing.dry_run == false`
2. 环境变量 `CMS_ENABLE_REAL_PUBLISH=true`

只要任一条件不满足，Agent 返回 `status: "dry_run"`，不会上传图片、创建文章或更新文章。

dry-run 的默认语义是“只做本地预检，不做写入型操作”：

- 不认证
- 不上传图片
- 不创建文章
- 不更新文章
- 默认不做远程 slug 检查

如果 `publishing.slug_check_in_dry_run=true`，dry-run 才允许读取 CMS 做远程 slug 冲突预检。即使开启该项，dry-run 也不会执行任何写入型发布动作。

## Custom CMS Contract

`config.yaml -> cms.custom.post_contract` 仍然存在，但这层 contract 由 `CMSClient / MediaUploader` 解释，不应散落在 `CMSAgent` 主流程中。

custom CMS 默认请求体示意：

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

## 本地 Contract 校验

`config.yaml -> cms.custom.post_contract.required_fields` 定义的是后端 contract 必填字段。
这些字段会先由 `CMSClient` 转换成统一业务检查项，再由 `CMSAgent` 执行本地预检，避免把缺字段问题推迟到后端接口报错时才暴露。

当前默认映射为：

- `title` -> `title_not_empty`
- `content_html/content` -> `content_not_empty`
- `slug` -> `slug_not_empty`
- `status` -> `status_valid`

其中 `title_not_empty` 为基础必校验；其余 contract 校验也只通过统一业务检查项暴露，不直接把后端字段名泄漏到 `CMSAgent` 主流程。

## 发布模式校验

`publishing.mode` 只允许以下值：

- `draft` -> `draft`
- `scheduled` -> `scheduled`
- `immediate` -> `publish`
- `publish` -> `publish`

其他值会被 `CMSAgent` 在本地预检阶段直接拦截为 `publish_blocked`，并返回：

- `errors: ["publish_mode_valid"]`
- `checks["publish_mode_valid"] = false`
- `missing_fields` 包含 `publishing.mode`

## 发布状态分层

- `dry_run`：试运行通过，但未真实发布
- `published / draft / scheduled`：真实发布或保存成功
- `publish_blocked`：字段或物料缺失，不能发给 CMS 后端
- `retry_pending`：认证、网络、后端、图片上传等系统问题，可重试
- `failed`：不可恢复或未分类失败

字段或物料问题不应当返回普通 `failed`，优先归为 `publish_blocked`。
系统/API 问题优先归为 `retry_pending`。

## Warnings

以下问题默认进入 `warnings`，不阻断发布：

- `meta_title_not_empty`
- `meta_description_not_empty`
- `primary_keyword_not_empty`
- `tags_not_empty`
- `schema_valid`
- `featured_alt_not_empty`

## 图片上传失败策略

- `images.upload_failure_strategy = "fail"`：上传失败返回 `publish_blocked` 或 `retry_pending`
- `images.upload_failure_strategy = "use_original_url"`：仅在封面图非必需时回退为原始 URL 继续发布

当 `featured_image.required=true` 时，上传失败会返回 `publish_blocked` 或 `retry_pending`，并携带 `featured_image_upload_failed` 或实际上传错误。

## 不属于 CMSAgent 的职责

- 不判断这篇内容值不值得发。
- 不判断文章写作质量是否达标。
- 不决定是否改写或如何改写。
- 不生产正文、不补写结构、不做选题判断。
- 不替代上游的 QualityAgent、EditorAgent、SEOAgent 或路由层。
- 不拼 CMS 后端专属 request body。
- 不解析 `response_paths`。
- 不直接理解 `content_field`、`meta_field`、`status_mapping` 等 contract 细节。
- 不绕过 `CMSClient / MediaUploader` 直接请求 CMS 后端。

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
  "slug": "emba-conditions-2026-guide",
  "provider": "custom",
  "checks": {
    "title_not_empty": true,
    "content_not_empty": true,
    "slug_not_empty": true,
    "status_valid": true,
    "publish_mode_valid": true,
    "category_assigned": true,
    "featured_image_set": true,
    "slug_unique": true,
    "slug_checked": false,
    "slug_resolution": {}
  },
  "errors": [],
  "warnings": [],
  "missing_fields": [],
  "blocking": false,
  "payload_summary": {
    "title": "EMBA报考条件有哪些？2026最全解读",
    "slug": "emba-conditions-2026-guide",
    "category": "emba-guide",
    "topic_id": "topic-001",
    "action": "create",
    "tags_count": 3
  },
  "payload": {
    "title": "EMBA报考条件有哪些？2026最全解读",
    "content": "<h1>EMBA报考条件有哪些</h1><p>...</p>",
    "content_html": "<h1>EMBA报考条件有哪些</h1><p>...</p>",
    "content_md": "# EMBA报考条件有哪些\n\n...",
    "slug": "emba-conditions-2026-guide",
    "category": "emba-guide",
    "tags": ["EMBA报考条件", "EMBA", "报考指南"],
    "featured_image_url": "https://cdn.example.com/images/emba-guide-featured.jpg",
    "meta_title": "EMBA报考条件有哪些？2026最全解读 | 品牌名",
    "meta_description": "本文详细介绍EMBA报考条件。",
    "primary_keyword": "EMBA报考条件",
    "topic_id": "topic-001",
    "status": "draft",
    "publish_date": null
  }
}
```
