# Multi-Agent Automatic Operation Website

面向自动化内容运营的多智能体系统。当前代码以 `main.py` 为统一入口，支持内容生产、内容质检、SEO 优化、图片规划、CMS 草稿发布、数据采集、竞品分析和爬虫内容入库等流程。

> 当前仓库仍包含部分演示/占位实现。真实外部调用依赖 `.env` 中的 API Key、CMS 配置和各 agent 的运行模式开关。默认配置优先保证本地可运行和 dry-run，不会直接发布到生产 CMS。

## 当前实现概览

### 当前重点：文章评分 Agent + QualityAgent

当前 crawler 侧重点是“文章价值评分 + 文章质量评分”，不是 topic 排名。

文章评分 Agent 负责判断素材值不值得做，评分维度包括标题、是否通知、内容重要性和时效性。`article_score >= 75` 进入 QualityAgent，**< 75 全部丢弃**。

QualityAgent 流程：

1. 原文先进入 QualityAgent。原文质量低于 70 才进入 ResearchAgent + WriterAgent。
2. WriterAgent 生成后再次进入 QualityAgent 复评。**最多重试 1 次**，选最高分版本，**不管分数多少都采用**。

详见：[06-文章评分Agent说明.md](06-文章评分Agent说明.md)
详见：[07-QualityAgent说明.md](07-QualityAgent说明.md)

### 入口

- `main.py`：命令行入口，支持选择不同执行引擎。
- 默认执行引擎：`hybrid`。
- 支持的引擎：
  - `hybrid`：推荐的内容生产主流程，结合 LangGraph 状态流转和本地 agent/tool 实现。
  - `langgraph`：基于 LangGraph 的状态机示例流程，部分节点仍是简化实现。
  - `crewai`：基于 CrewAI 的顺序式多 agent 流程。
  - `crawler`：爬虫内容清洗、路由和入库 dry-run 流程。

### 🤖 核心 Agent 团队

1. **📊 文章评分Agent (Article Scoring)**: 判断文章素材是否值得做，评分维度包括标题价值、通知属性、内容重要性和时效性。
2. **⭐ QualityAgent (质量评分)**: 评估文章写作质量（字数、流畅度、结构、吸引力、AI味）；不达标时触发 Research → Writer → QualityAgent 复评循环。
3. **📚 ResearchAgent (调研分析)**: 为低质量但高价值文章生成大纲和 WriterAgent prompt。
4. **✍️ WriterAgent (内容写作)**: 根据大纲生成高质量、SEO友好的原创文章。
5. **🔧 EditorAgent (审校编辑)**: 审校文章质量、润色、格式排版。
6. **🎨 ImageAgent (配图生成)**: 为文章生成或选择合适的配图及Alt文本。
7. **🔍 SEOAgent (SEO优化)**: 优化文章SEO要素（TDK、Schema、内链等）。
8. **📋 CMSAgent (内容发布)**: 将最终文章和多媒体资源发布到目标CMS系统。
9. **📈 DataAgent (数据分析)**: 采集网站运营数据，生成报告和优化建议。
10. **🗡️ CompetitorAgent (竞品监控)**: 监控竞品动态，提供竞争分析和策略。

## 🛠️ 技术栈

- **开发语言**: Python 3.11+
- **Agent 框架**: CrewAI + LangGraph
- **大语言模型**: GPT-4o / DeepSeek
- **调度系统**: APScheduler / Celery
- **数据存储**: MySQL (文章、爬虫结果、任务、评分、日志) + Redis (缓存/队列)
- **对象存储**: 华为云 OBS (图片、附件、生成资源)
- **语义检索**: 当前不接入独立向量数据库；如后续需要内链推荐/RAG，再作为可选扩展

## 📂 目录结构

```text
Multi-Agent-Automatic-Operation-Website/
├── agents/                 # 各类 agent、工具、prompt、配置
├── workflows/              # CrewAI、LangGraph、Hybrid、Crawler 工作流
├── scheduler/              # APScheduler 调度器与计划任务配置
├── config/                 # 品牌规范等全局配置
├── docs/                   # 架构、API、部署、使用文档
├── logs/                   # 运行日志目录
├── main.py                 # CLI 入口
├── requirements.txt        # Python 依赖
└── .env.example            # 环境变量模板
```

## 环境准备

### 推荐 Python 版本

- 推荐：Python 3.12（或 3.11）
- 不建议：Python 3.13（部分三方库可能尚未完整适配，容易出现“requirements 已声明但无法安装/未安装”的不一致）

### 1. 创建虚拟环境

```powershell
cd D:\多智能体自动操作网站\Multi-Agent-Automatic-Operation-Website
py -3.12 -m venv venv
.\venv\Scripts\activate
```

### 2. 安装依赖

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. 依赖一致性与测试

```powershell
.\venv\Scripts\python.exe -m pip check
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m pytest -q tests
```

### 3. 创建环境变量文件

```powershell
Copy-Item .env.example .env
```

至少需要按使用场景配置以下变量：

- `OPENAI_API_KEY`：写作、编辑、图片生成等 LLM 能力。
- `SERPAPI_API_KEY`：选题 live 模式和搜索趋势能力。
- `CMS_API_URL`、`CMS_API_KEY`：CMS 发布能力。
- `CMS_ENABLE_REAL_PUBLISH=true`：允许真实发布。默认关闭。
- `CRAWLER_ENABLE_LLM_DECISION=true`：爬虫路由使用 LLM 决策。默认关闭。
- `EDITOR_ENABLE_LLM=true`：编辑 agent 使用 LLM。默认关闭。

数据库、Redis、MongoDB 等变量保留在模板中，供后续数据持久化或外部集成使用。当前并非所有流程都会强制使用这些服务。

## 运行方式

### 推荐：Hybrid 内容生产流程

```powershell
python main.py --engine hybrid --topic "AI驱动的内容营销" --keyword "AI内容营销"
```

### LangGraph 示例流程

```powershell
python main.py --engine langgraph --topic "AI驱动的内容营销" --keyword "AI内容营销"
```

### CrewAI 示例流程

```powershell
python main.py --engine crewai --topic "AI驱动的内容营销" --keyword "AI内容营销"
```

### 爬虫内容处理流程

```powershell
python main.py --engine crawler --keyword "多Agent系统"
```

该命令会使用 `main.py` 中的示例爬虫数据运行 dry-run 入库流程。

## 代码健康检查

在 Windows 环境下建议优先使用项目虚拟环境中的 Python：

```powershell
.\venv\Scripts\python.exe -m compileall -q agents workflows scheduler config main.py
```

该检查只能证明代码可解析，不能证明外部 API、CMS、数据库或调度器生产行为已经可用。

## 配置说明

### 品牌规范

`config/brand_guidelines.yaml` 定义品牌语气、禁用词、SEO、质量阈值和内容结构等规则。`config/__init__.py` 会加载该配置，供 agent 和 workflow 使用。

### Agent 配置

各 agent 目录下通常包含：

- `config.yaml`：该 agent 的运行参数。
- `prompts/`：提示词模板。
- `tools/`：可复用工具实现。
- `*_agent.py`：存在独立 agent 类时的主实现。

不同 agent 的完整度不完全一致。已有独立类的 agent 更适合直接实例化测试；只有工具和配置的 agent 主要通过工作流间接使用。

## 当前限制与完善方向

- **外部服务接线**：OpenAI、SerpAPI、CMS、分析平台、数据库等依赖环境变量和真实服务，默认配置不会假定这些服务已经可用。
- **真实发布保护**：CMS 默认 dry-run；生产发布前需要明确打开 `CMS_ENABLE_REAL_PUBLISH` 并校验 CMS agent 配置。
- **调度器生产化**：需要继续完善任务开关、失败重试、通知、事件循环启动方式和 `cron_tasks.yaml` 与代码注册任务的一致性。
- **工作流一致性**：`hybrid` 是当前最完整的主流程；`langgraph` 和 `crewai` 更偏参考/演示，需要继续补齐真实 agent 输出、错误处理和测试。
- **质量规则**：写作、编辑和品牌配置中已有字数、禁用词、关键词、可读性等规则基础，建议统一为“严格失败 + 有限重写 + 最终错误/警告输出”的策略，避免不合格内容静默进入 CMS。
- **测试覆盖**：建议为各 agent 的 `execute`、workflow 状态流转、CMS dry-run/真实发布保护、调度器任务注册和爬虫路由增加单元测试与集成测试。

## 参考文档

- `00-方案概述.md`
- `01-Agent架构图.md`
- `02-技术实现规范.md`
- `03-工作流编排.md`
- `04-定时任务方案.md`
- `05-部署方案.md`
- `06-文章评分Agent说明.md`
- `07-QualityAgent说明.md`
- `08-ResearchAgent说明.md`
- `09-WriterAgent说明.md`
- `今日工作总结-2026-06-24.md`

如文档与代码不一致，以当前代码、`main.py`、`workflows/`、`agents/` 和 `scheduler/` 中的实现为准。
