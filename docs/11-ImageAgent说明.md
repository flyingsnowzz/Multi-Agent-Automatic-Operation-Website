# ImageAgent 说明

## 当前目标

ImageAgent 与 SEOAgent 在 Editor 之后**并行执行**，输出汇入 CMSAgent，负责为已通过编辑和 SEO 优化的文章生成配图。它是内容生产流水线中的视觉创作环节——输入 SEO 优化后的文章内容，输出结构化的图片方案或实际生成的图片文件。

当前支持两条生图路径：

1. **在线生成**（OpenAI DALL-E / Coze Site）：通过 LLM 分析文章内容生成配图提示词，再调用图片 API 生成封面图和文中插图。
2. **批量 Pipeline**（DeepSeek + Coze Site）：从数据库读取已通过质量审核的文章，自动分析内容产出 5-6 种视觉风格的配图提示词，再批量调用 Coze Site 生图并下载到本地缓存。

## 工作位置

```mermaid
flowchart LR
    SEO["SEOAgent<br/>SEO 优化稿 + TDK"] --> IMAGE["ImageAgent<br/>生成/选择配图"]
    IMAGE --> CMS["CMSAgent<br/>发布到 CMS"]
```

在 `langgraph_workflow` 中，ImageAgent 对应 `_image_node`，支持两种模式：

- **`plan_only`**：仅输出配图方案（提示词 + Alt 文本），不调用生图 API。适合快速验证或成本控制。
- **`generate`**：实际调用 DALL-E 或 Coze API 生成图片，回填 URL 到输出结构。

## 核心职责

| 职责 | 说明 |
|------|------|
| 封面图生成 | 基于文章主题生成吸引人的封面图（OpenAI DALL-E / Coze Site） |
| 文中插图生成 | 为文章内各段落生成相关配图 |
| Alt 文本生成 | 为每张图片生成 SEO 友好的替代文本（中/英文，≤125 字符） |
| 配图提示词生成 | 通过 DeepSeek 分析文章内容，自动从 8 种视觉风格中选择最合适的 5-6 种 |
| 提示词→图片 Pipeline | 从数据库读取已生成的提示词，批量调用 Coze Site 生成实际图片并下载到本地 |

## 工具清单

| 工具 | 文件 | 功能 |
|------|------|------|
| `image_generator` | `tools/image_generator.py` | OpenAI DALL-E 图片生成，支持 gpt-image-1 / dall-e-3，8 种视觉风格 |
| `alt_text_generator` | `tools/alt_text_generator.py` | SEO 友好的 Alt 文本生成（中/英文），HTML img 标签格式化 |
| `coze_image_generator` | `tools/coze_image_provider.py` | Coze Site 图片生成，自动下载到本地缓存 |
| `image_prompt_generator` | `tools/image_prompt_generator.py` | DeepSeek 分析文章 → 多风格配图提示词 → 写入 `article_image_prompts` |
| `prompt_to_image_generator` | `tools/prompt_to_image.py` | 从 DB 读取提示词 → Coze 批量生图 → 写回 DB |

## 输入

ImageAgent 接收上游 SEOAgent 的输出：

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章标题 | 文章主标题 | WriterAgent / EditorAgent |
| 文章内容 | 已编辑/SEO 优化后的正文 | EditorAgent / SEOAgent |
| 主关键词 | SEO 目标关键词 | TopicAgent / SEOAgent |
| 内容类型 | guide / news / analysis 等 | TopicAgent |
| TDK 信息 | meta_title / meta_description | SEOAgent |

## 输出

标准化输出结构 `ImageResult`，与 `hybrid_workflow` / `langgraph_workflow` 对齐：

```json
{
  "featured_image_url": "https://example.com/cover.webp",
  "featured_alt": "EMBA 选择指南封面图，高管在现代商学院场景下讨论评估维度",
  "featured_prompt": "商务风格封面图，主题为 EMBA 项目选择...",
  "inline_images": [
    {
      "url": "https://example.com/inline1.webp",
      "alt": "EMBA 学费对比图表",
      "prompt": "清晰的信息图表展示 EMBA 学费对比...",
      "position": "第2节小标题后"
    }
  ],
  "license": {
    "source": "generated",
    "provider": "openai"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `featured_image_url` | string | 封面图 URL（plan_only 模式为空字符串） |
| `featured_alt` | string | 封面图 Alt 文本，自然包含主关键词 |
| `featured_prompt` | string | 封面图提示词（可复现） |
| `inline_images` | array | 文中插图数组，每项含 url/alt/prompt/position |
| `license` | object | 版权信息（source + provider） |

## 完整配图 pipeline（DeepSeek → Coze）

```
LangGraph image_node
    ↓
cover_decision 判断封面策略
    ↓
转载/直发文章：复用原文封面，不调用 DeepSeek，不调用生图
    ↓
重写文章：DeepSeek 根据标题和正文摘要生成最终封面 prompt
    ↓
Coze / OpenAI / Seedance 生图 → 下载到本地缓存 → 写入 pipeline_audit
    ↓
CMSAgent 发布
```

当前正式链路不再使用旧的 `article_image_prompts` 表，也不再先生成 5-6 个候选风格入库。它只在真正需要生成新封面的重写文章上调用一次轻量 DeepSeek prompt 生成，输出一个最终封面 prompt，然后交给图片 Provider。

### 旧版支持的视觉风格（历史方案）

| slug | 中文名 | 风格描述 |
|------|--------|----------|
| `realistic` | 写实摄影风 | 逼真的摄影质感，自然光线，真实场景感 |
| `cartoon` | 卡通插画风 | 活泼可爱的卡通插画，色彩明亮，线条圆润 |
| `illustration` | 扁平矢量风 | 现代扁平矢量插画，简洁图形，高饱和度配色 |
| `cinematic` | 电影级写实风 | 电影镜头感，景深明显，光影戏剧化 |
| `minimalist` | 极简抽象风 | 极简构图，抽象几何元素，大量留白 |
| `watercolor` | 水彩手绘风 | 清新水彩画，柔和过渡，文艺感 |
| `3d_render` | 3D 渲染风 | 3D 建模质感，材质细腻，现代科技感 |
| `chinese_ink` | 中国水墨风 | 中国传统水墨画风格，意境悠远 |

## 环境变量

| 变量 | 用途 | 是否必需 |
|------|------|----------|
| `OPENAI_API_KEY` | OpenAI DALL-E / gpt-image-1 API 密钥 | 使用 DALL-E 时必需 |
| `DEEPSEEK_API_KEY` | DeepSeek 文章分析与提示词生成 | 使用 DeepSeek 分析时必需 |
| `COZE_JWT_TOKEN` | Coze Site 生图 JWT Token | 使用 Coze 生图时必需 |
| `IMAGE_PROMPT_LLM_ENABLED` | 是否启用生图前的 DeepSeek prompt 生成 | 可选，默认 true |
| `IMAGE_PROMPT_API_KEY` | 配图提示词 LLM API 密钥（可选，优先于 DEEPSEEK_API_KEY） | 可选 |
| `IMAGE_PROMPT_BASE_URL` | 配图 prompt 生成模型 API 地址 | 可选，默认 https://api.deepseek.com |
| `IMAGE_PROMPT_MODEL` | 配图 prompt 生成模型 | 可选，默认 deepseek-v4-flash |
| `IMAGE_PROMPT_CONTEXT_CHARS` | 送入 DeepSeek 的正文摘要长度 | 可选，默认 1200 |
| `IMAGE_PROMPT_MAX_TOKENS` | DeepSeek 输出 token 上限 | 可选，默认 220 |

## 配置文件

配置文件位于 `agents/image_agent/config.yaml`，主要配置项：

- **`image_generation`**：生图 Provider 选择（openai / coze / midjourney）、模型、尺寸、质量
- **`image_requirements`**：封面图尺寸（1200×630，OG 推荐 1.91:1）、文中插图数量（1-4 张）
- **`alt_text`**：Alt 文本最大长度（125 字符）、关键词策略
- **`optimization`**：图片压缩、格式转换（webp）、尺寸调整
- **`image_prompt`**：DeepSeek 轻量生成封面 prompt 的模型、API Key、上下文长度、输出上限

## 集成方式

### LangGraph Workflow

在 `langgraph_workflow.py` 中，ImageAgent 作为独立节点 `_image_node` 运行：

```python
wf = MultiAgentWorkflow(config_dir="agents", image_mode="plan_only")
# 或
wf = MultiAgentWorkflow(config_dir="agents", image_mode="generate")
```

### 独立使用

```python
from agents.image_agent import ImageAgent

agent = ImageAgent()
# plan_only 模式（默认）
result = agent.generate_alt_text(
    image_description="商务人士在办公室工作",
    context="EMBA 选择指南",
    keywords=["EMBA"],
)
# generate 模式 - 实际生图
gen_result = await agent.generate_featured_image(
    prompt="A modern business school campus at sunset",
    visual_style="professional",
)
```

### 数据库 Pipeline

```python
# 步骤 1：从 DB 读取已审核文章，生成配图提示词
prompt_result = await agent.generate_prompts_from_db(limit=5, min_quality=85.0)

# 步骤 2：从 DB 读取提示词，调用 Coze 生图
image_result = await agent.generate_images_from_prompts(limit=5, only_primary=True)

# 一步到位
full_result = await agent.full_pipeline(prompt_limit=5, image_limit=5)
```

## 入库表结构

### `article_image_prompts`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT AUTO_INCREMENT | 主键 |
| `candidate_id` | BIGINT | 关联文章候选 ID |
| `output_id` | BIGINT | 关联 writer_article_outputs ID |
| `source_title` | VARCHAR(500) | 原标题 |
| `generated_title` | VARCHAR(500) | 生成标题 |
| `content_md` | LONGTEXT | 文章内容 |
| `image_prompts_json` | JSON | DeepSeek 生成的配图提示词（含 content_analysis / styles / primary_recommendation） |
| `generated_images_json` | JSON | Coze 生成的图片结果（含 url / local_path / run_id） |
| `generation_status` | VARCHAR(30) | `pending` / `generated` / `failed` |

## 依赖

| 依赖 | 用途 |
|------|------|
| `openai` | OpenAI DALL-E API 调用 |
| `httpx` | Coze Site HTTP 异步请求 |
| `aiomysql` | MySQL 异步数据库读写 |
| `crewai` | CrewAI Tool 封装（可选） |
| `yaml` | 配置文件解析 |
