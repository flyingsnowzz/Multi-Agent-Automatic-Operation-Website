# Agent角色设计

## 1.1 总体架构

```mermaid
flowchart TD
    subgraph ORCHESTRATOR["🎭 编排器 Orchestrator"]
        SCHEDULER["定时调度器"]
        WORKFLOW["工作流引擎"]
        HUB["消息中枢"]
    end

    subgraph CONTENT["📝 内容生产团队"]
        TOPIC["🔍 选题Agent<br/>TopicAgent"]
        RESEARCH["📚 调研Agent<br/>ResearchAgent"]
        WRITER["✍️ 写作Agent<br/>WriterAgent"]
        EDITOR["🔧 编辑Agent<br/>EditorAgent"]
        IMAGE["🎨 配图Agent<br/>ImageAgent"]
        CRAWLER["🕷️ 爬虫处理Agent<br/>CrawlerProcessor"]
    end

    subgraph OPTIMIZE["⚙️ 优化团队"]
        SEO["🔍 SEO Agent<br/>SEOAgent"]
        TECH["🏗️ 技术SEO Agent<br/>TechSEOAgent"]
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
# Agent角色设计

## 1.1 总体架构

```mermaid
flowchart TD
    subgraph ORCHESTRATOR["🎭 编排器 Orchestrator"]
        SCHEDULER["定时调度器"]
        WORKFLOW["工作流引擎"]
        HUB["消息中枢"]
    end

    subgraph CONTENT["📝 内容生产团队"]
        TOPIC["🔍 选题Agent<br/>TopicAgent"]
        RESEARCH["📚 调研Agent<br/>ResearchAgent"]
        WRITER["✍️ 写作Agent<br/>WriterAgent"]
        EDITOR["🔧 编辑Agent<br/>EditorAgent"]
        IMAGE["🎨 配图Agent<br/>ImageAgent"]
        CRAWLER["🕷️ 爬虫处理Agent<br/>CrawlerProcessor"]
    end

    subgraph OPTIMIZE["⚙️ 优化团队"]
        SEO["🔍 SEO Agent<br/>SEOAgent"]
        TECH["🏗️ 技术SEO Agent<br/>TechSEOAgent"]
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
    ORCHESTRATOR --> PUBLISH
    ORCHESTRATOR --> ANALYTICS
    CONTENT --> OPTIMIZE
    OPTIMIZE --> PUBLISH
    PUBLISH --> ANALYTICS
    ANALYTICS -. "📈 数据反馈" .-> ORCHESTRATOR
    CRAWLER -->|"丢弃"| DISCARD["❌ 丢弃"]
    CRAWLER -->|"pass_to_topic"| TOPIC
```

## 1.2 各Agent职责详述

### 🔍 选题Agent（TopicAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 发现热点、挖掘长尾关键词、生成选题列表 |
| **输入** | 行业关键词、历史文章数据、竞品动态、热点趋势 |
| **输出** | 选题建议列表（含关键词、预估搜索量、竞争度、推荐理由） |
| **运行频率** | 每日1次，或被事件触发 |
| **实现文件** | [[agents/topic_agent/]] |

### 📚 调研Agent（ResearchAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 围绕选题进行深度调研，收集素材、生成大纲 |
| **输入** | 选题列表（来自TopicAgent） |
| **输出** | 调研素材包 + 文章大纲 |
| **运行频率** | 按需，每个选题触发一次 |
| **实现文件** | [[agents/research_agent/]] |

### ✍️ 写作Agent（WriterAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 根据大纲生成高质量原创文章 |
| **输入** | 大纲 + 调研素材 + 品牌风格指南 |
| **输出** | Markdown格式文章 |
| **运行频率** | 按需，每个大纲触发一次 |
| **实现文件** | [[agents/writer_agent/]] |

### 🔧 编辑Agent（EditorAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 审校文章质量、润色、排版 |
| **输入** | WriterAgent产出的Markdown文章 |
| **输出** | 审校后的最终版文章 + 质量评分 |
| **运行频率** | 每篇文章触发一次 |
| **实现文件** | [[agents/editor_agent/]] |

### 🎨 配图Agent（ImageAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 为文章生成/选择配图 |
| **输入** | 文章内容 + 配图需求 |
| **输出** | 文章配图（头图+文中插图） |
| **运行频率** | 每篇文章触发一次 |
| **实现文件** | [[agents/image_agent/]] |

### 🔍 SEO Agent（SEOAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 优化文章的SEO要素，确保搜索引擎友好 |
| **输入** | 审校后的文章 + 目标关键词 |
| **输出** | SEO优化后的文章 + TDK元数据 + 内链建议 |
| **运行频率** | 每篇文章触发一次 |
| **实现文件** | [[agents/seo_agent/]] |

### 📋 CMS Agent（CMSAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 将文章发布到CMS系统 |
| **输入** | SEO优化后的文章 + TDK + 配图 |
| **输出** | 发布确认 + 文章URL |
| **运行频率** | 每篇文章触发一次 |
| **实现文件** | [[agents/cms_agent/]] |

### 📈 数据Agent（DataAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 采集网站运营数据，生成分析报告和优化建议 |
| **输入** | 网站访问数据 + 搜索排名数据 |
| **输出** | 运营周报 + 优化建议 |
| **运行频率** | 每日采集，每周出报告 |
| **实现文件** | [[agents/data_agent/]] |

### 🗡️ 竞品Agent（CompetitorAgent）

| 项目 | 说明 |
|------|------|
| **职责** | 监控竞品网站动态，提供竞争分析 |
| **输入** | 竞品网站列表 + 监控关键词 |
| **输出** | 竞品动态报告 + 差异化建议 |
| **运行频率** | 每周1次 |
| **实现文件** | [[agents/competitor_agent/]] |

### 🕷️ 爬虫处理Agent（CrawlerProcessor）

| 项目 | 说明 |
|------|------|
| **职责** | 从爬虫数据库读取待处理内容，进行去重和质量自检评估后进行双路自动分流（丢弃/转选题线索） |
| **输入** | 爬虫数据库中 status=pending 的内容 |
| **输出** | 极简自动双路分流结果（丢弃 / pass_to_topic 选题线索 Payload） |
| **运行频率** | 每日多次（由定时调度器触发） |
| **决策规则** | 丢弃（质量<40/重复/字数超限/版权风险）、转选题线索（pass_to_topic→TopicAgent，进入选题生命周期） |
| **工具** | crawler_db_reader（数据库读取）、content_evaluator（质量评估）、dedup_checker（去重检测） |
| **实现文件** | [[agents/crawler_processor_agent/]] |

---

*相关文档：[[00-方案概述]] | [[03-工作流编排]]*
