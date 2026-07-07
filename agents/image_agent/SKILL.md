# 配图Agent (ImageAgent)

## 概述

ImageAgent 负责为文章生成或选择合适的配图，是内容生产流水线中的视觉创作环节。它接收上游 SEO 优化后的文章内容，输出结构化的图片方案（或实际生成的图片），供下游 CMS Agent 发布使用。

## 核心职责

| 职责 | 说明 |
|------|------|
| 封面图生成 | 基于文章主题生成吸引人的封面图（DALL-E / Coze Site） |
| 插图生成 | 为文章内各段落生成相关配图 |
| Alt 文本生成 | 为每张图片生成 SEO 友好的替代文本描述（中/英文） |
| 配图决策 | Redis 图片 worker 判断复用原封面还是生成新封面 |
| 生图 Pipeline | 重写文章需要新封面时，调用配置的图片 Provider 生成实际图片 |

## 工具清单

| 工具 | 文件 | 功能 |
|------|------|------|
| `image_generator` | `tools/image_generator.py` | OpenAI DALL-E 图片生成（支持 gpt-image-1 / dall-e-3） |
| `alt_text_generator` | `tools/alt_text_generator.py` | SEO 友好的 Alt 文本生成（中/英文，≤125 字符） |
| `coze_image_generator` | `tools/coze_image_provider.py` | 通过 Coze Site API 生成图片并下载到本地 |

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章标题 | 文章主标题 | WriterAgent / EditorAgent |
| 文章内容 | 已审核/编辑后的正文 | EditorAgent / SEOAgent |
| 主关键词 | SEO 目标关键词 | SEOAgent |
| 内容类型 | guide / news / analysis 等 | Redis payload |

## 输出

标准化输出结构（`ImageResult`），与 `hybrid_workflow` / `langgraph_workflow` 对齐：

| 字段 | 类型 | 说明 |
|------|------|------|
| `featured_image_url` | string | 封面图 URL（plan_only 模式为空字符串） |
| `featured_alt` | string | 封面图 Alt 文本（含主关键词） |
| `featured_prompt` | string | 封面图提示词（可复现） |
| `inline_images` | array | 文中插图数组，每项含 url/alt/prompt/position |
| `license` | object | 版权信息（source + provider） |

## 工作模式

| 模式 | 说明 |
|------|------|
| `plan_only` | 仅输出配图方案（提示词和 Alt 文本），不调用生图 API |
| `generate` | 调用 DALL-E / Coze API 实际生成图片并回填 URL |

## 生图 Provider 对比

| Provider | API | 用途 | 配置 |
|----------|-----|------|------|
| OpenAI DALL-E | `images/generations` | 高质量封面图/插图 | `OPENAI_API_KEY` |
| Coze Site | 自建生图站点 | 批量生图 Pipeline | `COZE_JWT_TOKEN` |

## 配图流水线

```
pipeline:image
    ↓
worker_image.py 判断封面策略
    ↓
转发/直发文章：复用 source_image
重写文章：调用 IMAGE_PROVIDER 生成新封面
    ↓
写入 pipeline_audit.image_url / image_local_path
    ↓
pipeline:cms
```

## 依赖

- `openai` — DALL-E API 调用
- `httpx` — Coze Site HTTP 请求
- `aiomysql` — Redis worker 写入 pipeline_audit 图片字段
- `crewai` — CrewAI Tool 封装
- `yaml` — 配置文件解析

## 环境变量

| 变量 | 用途 |
|------|------|
| `OPENAI_API_KEY` | DALL-E / gpt-image-1 API 密钥 |
| `DEEPSEEK_API_KEY` | DeepSeek 文章分析 API 密钥 |
| `COZE_JWT_TOKEN` | Coze Site 生图 JWT Token |
| `IMAGE_PROMPT_API_KEY` | 配图提示词 LLM API 密钥（可选，优先于 DEEPSEEK_API_KEY） |

## 相关文档

- `config.yaml` — Agent 配置（模型、风格、尺寸、Coze/DeepSeek 参数）
- `prompt.md` — LLM 提示词模板（系统提示词、需求分析、Alt 文本指南、Few-shot 示例）
- `tools/` — 所有工具实现
