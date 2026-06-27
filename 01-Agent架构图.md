# Agent角色设计

## 1.1 总体架构

````mermaid
flowchart TD
    subgraph ORCHESTRATOR["🎭 编排器 Orchestrator"]
        CHIEF["主编/运营负责人<br/>确定主题方向"]
        SCHEDULER["定时调度器"]
        WORKFLOW["工作流引擎"]
        HUB["消息中枢"]
    end

    subgraph ACTIVE["链路一：主动调研生产链"]
        SCORE["📊 文章评分Agent<br/>ScoringAgent"]
        RESEARCH["📚 调研Agent<br/>ResearchAgent"]
        WRITER["✍️ 写作Agent<br/>WriterAgent"]
        EDITOR["🔧 编辑Agent<br/>EditorAgent"]
        SEO["🔍 SEO Agent<br/>SEOAgent"]
        IMAGE["🎨 配图Agent<br/>ImageAgent"]
    end

    subgraph CRAWLER_FLOW["链路二：爬虫内容筛选分发链"]
        SPIDER["外部爬虫/采集任务"]
        CRAWLER_DB[("爬虫结果库<br/>status=pending")]
        CRAWLER["🕷️ 爬虫处理Agent<br/>CrawlerProcessor"]
        REVIEW["审查评分/去重<br/>质量/相关性/SEO"]
        QUALITY["⭐ 质量评分Agent<br/>QualityAgent"]
        DISCARD["❌ 放弃/归档"]
    end

    subgraph PUBLISH["📤 发布团队"]
        CMS["📋 CMS Agent<br/>CMSAgent"]
        SOCIAL["📢 社交分发Agent<br/>SocialAgent"]
    end

    subgraph ANALYTICS["📊 分析团队"]
        DATA["📈 数据Agent<br/>DataAgent"]
        COMPETE["🗡️ 竞品Agent<br/>CompetitorAgent"]
    end

    ORCHESTRATOR --> CONTENT
    ORCHESTRATOR --> OPTIMIZE
````

# Agent角色设计

## 1.1 总体架构

```mermaid
flowchart TD
    subgraph ORCHESTRATOR["🎭 编排器 Orchestrator"]
        CHIEF["主编/运营负责人<br/>确定主题方向"]
        SCHEDULER["定时调度器"]
        WORKFLOW["工作流引擎"]
        HUB["消息中枢"]
    end

    subgraph ACTIVE["链路一：主动调研生产链"]
        SCORE["📊 文章评分Agent<br/>ScoringAgent"]
        RESEARCH["📚 调研Agent<br/>ResearchAgent"]
        WRITER["✍️ 写作Agent<br/>WriterAgent"]
        EDITOR["🔧 编辑Agent<br/>EditorAgent"]
        SEO["🔍 SEO Agent<br/>SEOAgent"]
        IMAGE["🎨 配图Agent<br/>ImageAgent"]
    end

    subgraph CRAWLER_FLOW["链路二：爬虫内容筛选分发链"]
        SPIDER["外部爬虫/采集任务"]
        CRAWLER_DB[("爬虫结果库<br/>status=pending")]
        CRAWLER["🕷️ 爬虫处理Agent<br/>CrawlerProcessor"]
        REVIEW["审查评分/去重<br/>质量/相关性/SEO"]
        QUALITY["⭐ 质量评分Agent<br/>QualityAgent"]
        DISCARD["❌ 放弃/归档"]
    end

    subgraph PUBLISH["📤 发布团队"]
        CMS["📋 CMS Agent<br/>CMSAgent"]
        SOCIAL["📢 社交分发Agent<br/>SocialAgent"]
    end

    subgraph ANALYTICS["📊 分析团队"]
        DATA["📈 数据Agent<br/>DataAgent"]
        COMPETE["🗡️ 竞品Agent<br/>CompetitorAgent"]
    end

    CHIEF --> SCORE
    SCHEDULER --> WORKFLOW
    WORKFLOW --> SCORE
    WORKFLOW --> CRAWLER
    SCORE --> RESEARCH --> WRITER
    WRITER -->|"复评"| QUALITY_REV["⭐ QualityAgent 复评"]
    QUALITY_REV -->|">=85 通过"| EDITOR
    QUALITY_REV -.->|"<85 重写重写"| RESEARCH
    EDITOR --> SEO --> IMAGE --> CMS

    SPIDER --> CRAWLER_DB
    SCHEDULER -->|"定时读取 pending"| CRAWLER_DB
    CRAWLER_DB --> CRAWLER --> REVIEW
    REVIEW -->|"发表<br/>ready_to_publish"| CMS
    REVIEW -->|"AI改写再创作<br/>ready_to_rewrite"| QUALITY
    QUALITY -->|"quality<70<br/>重写"| RESEARCH
    QUALITY -->|"quality>=70<br/>轻改/审核"| EDITOR
    REVIEW -->|"废弃<br/>discarded"| DISCARD

    CMS --> SOCIAL
    CMS --> DATA
    COMPETE --> SCORE
    DATA -->|"再审查优化"| EDITOR
    DATA -->|"SEO修正"| SEO
    DATA -. "主题/策略反馈" .-> CHIEF
````

## 1.2 各Agent职责详述

### 📊 文章评分Agent（ScoringAgent）

| 项目         | 说明                                                         |
| ------------ | ------------------------------------------------------------ |
| **职责**     | 判断文章素材是否值得做，评分维度包括标题价值、通知属性、内容重要性和时效性 |
| **输入**     | crawler 文章标题、正文、发布时间                              |
| **输出**     | article_score（综合分）+ 各维度评分明细                       |
| **运行频率** | 每篇 crawler 文章触发一次                                     |
| **实现文件** | [[agents/scoring_agent/]]（ArticleScoreWriter）                 |

### 📚 调研Agent（ResearchAgent）

| 项目         | 说明                                     |
| ------------ | ---------------------------------------- |
| **职责**     | 围绕选题进行深度调研，收集素材、生成大纲 |
| **输入**     | 文章评分结果（来自文章评分Agent）        |
| **输出**     | 调研素材包 + 文章大纲                    |
| **运行频率** | 按需，每个选题触发一次                   |
| **实现文件** | [[agents/research_agent/]]               |

### ✍️ 写作Agent（WriterAgent）

| 项目         | 说明                           |
| ------------ | ------------------------------ |
| **职责**     | 根据大纲生成高质量原创文章     |
| **输入**     | 大纲 + 调研素材 + 品牌风格指南 |
| **输出**     | Markdown格式文章               |
| **运行频率** | 按需，每个大纲触发一次         |
| **实现文件** | [[agents/writer_agent/]]       |

### 🔧 编辑Agent（EditorAgent）

| 项目         | 说明                          |
| ------------ | ----------------------------- |
| **职责**     | 审校文章质量、润色、排版      |
| **输入**     | WriterAgent产出的Markdown文章 |
| **输出**     | 审校后的最终版文章 + 质量评分 |
| **运行频率** | 每篇文章触发一次              |
| **实现文件** | [[agents/editor_agent/]]      |

### 🎨 配图Agent（ImageAgent）

| 项目         | 说明                      |
| ------------ | ------------------------- |
| **职责**     | 封面图/插图生成、Alt文本撰写、配图提示词自动生成 |
| **输入**     | SEO 优化后文章 + 主关键词 + 内容类型 |
| **输出**     | featured_image_url/alt/prompt + inline_images + license |
| **运行频率** | 每篇文章触发一次          |
| **生图Provider** | OpenAI DALL-E (gpt-image-1) / Coze Site |
| **提示词生成** | DeepSeek 自动分析内容，生成 5-6 种风格选项 |
| **核心技术栈** | openai / httpx / aiomysql / crewai / DeepSeek |
| **实现文件** | [image_agent.py](agents/image_agent/image_agent.py) / [tools/](agents/image_agent/tools/) |

**核心工具：**
- `image_generator` — OpenAI DALL-E 图片生成（支持多种视觉风格）
- `alt_text_generator` — SEO 友好 Alt 文本（中/英文，≤125字符）
- `coze_image_generator` — Coze Site 生图 + 本地缓存下载
- `image_prompt_generator` — DeepSeek 分析文章 → 多风格提示词 → DB
- `prompt_to_image_generator` — DB 提示词 → Coze 批量生图 → 写回 DB

**完整流水线：** 审核文章(DB) → DeepSeek分析 → 提示词入库 → Coze生图 → 图片下载 → CMS发布

### 🔍 SEO Agent（SEOAgent）

| 项目         | 说明                                   |
| ------------ | -------------------------------------- |
| **职责**     | 优化文章的SEO要素，确保搜索引擎友好    |
| **输入**     | 审校后的文章 + 目标关键词              |
| **输出**     | SEO优化后的文章 + TDK元数据 + 内链建议 |
| **运行频率** | 每篇文章触发一次                       |
| **实现文件** | [[agents/seo_agent/]]                  |

### 📋 CMS Agent（CMSAgent）

| 项目         | 说明                         |
| ------------ | ---------------------------- |
| **职责**     | 将文章发布到CMS系统          |
| **输入**     | SEO优化后的文章 + TDK + 配图 |
| **输出**     | 发布确认 + 文章URL           |
| **运行频率** | 每篇文章触发一次             |
| **实现文件** | [[agents/cms_agent/]]        |

### 📈 数据Agent（DataAgent）

| 项目         | 说明                                     |
| ------------ | ---------------------------------------- |
| **职责**     | 采集网站运营数据，生成分析报告和优化建议 |
| **输入**     | 网站访问数据 + 搜索排名数据              |
| **输出**     | 运营周报 + 优化建议                      |
| **运行频率** | 每日采集，每周出报告                     |
| **实现文件** | [[agents/data_agent/]]                   |

### 🗡️ 竞品Agent（CompetitorAgent）

| 项目         | 说明                           |
| ------------ | ------------------------------ |
| **职责**     | 监控竞品网站动态，提供竞争分析 |
| **输入**     | 竞品网站列表 + 监控关键词      |
| **输出**     | 竞品动态报告 + 差异化建议      |
| **运行频率** | 每周1次                        |
| **实现文件** | [[agents/competitor_agent/]]   |

### 🕷️ 爬虫处理Agent（CrawlerProcessor）

| 项目         | 说明                                                                                                          |
| ------------ | ------------------------------------------------------------------------------------------------------------- |
| **职责**     | 从爬虫结果库读取待处理内容，完成入口处理后交给后续独立审查链路                                                |
| **输入**     | 爬虫数据库中 status=pending 的内容                                                                            |
| **输出**     | 标准化后的素材 + 交接信息                                                                                     |
| **运行频率** | 每日多次（由定时调度器触发）                                                                                  |
| **决策规则** | 不在本层承担 `publish / rewrite / discard` 分流，后续由独立 `REVIEW` 节点负责                                 |
| **工具**     | crawler_db_reader（数据库读取）                                                                               |
| **实现文件** | [[agents/crawler_processor_agent/]]                                                                           |

### ⭐ 质量评分Agent（QualityAgent）

| 项目         | 说明                                                                                         |
| ------------ | -------------------------------------------------------------------------------------------- |
| **职责**     | 评价文章写作质量（字数、流畅度、结构、吸引力、AI味），决定是否需要重写                       |
| **输入**     | 文章内容 + article_score（来自文章评分Agent）                                                |
| **输出**     | quality_score + 各维度分 + rewrite_feedback_prompt + 路由决策                                |
| **运行频率** | 按需，每篇通过文章评分Agent的原文触发一次；每次WriterAgent输出后再次触发                     |
| **运行位置** | ① 原文 `article_score > 75` 后，判断原文质量是否需重写                                       |
|             | ② WriterAgent 生成稿写出后，按 `if_ai_generated=true` 权重复评，决定是否通过                 |
| **路由规则** | quality<70→Research+Writer重写；70-84→人工审核；>=85→进入发布候选                            |
| **维度**     | word_count_score（代码）、fluency_score/structure_score/attractiveness_score（LLM）、ai_feel_score（衍生） |
| **实现文件** | [[agents/quality_agent/]]                                                                    |

---

_相关文档：[[00-方案概述]] | [[03-工作流编排]]_
