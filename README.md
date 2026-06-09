# Multi-Agent Automatic Operation Website

面向自动化内容运营的多智能体系统。当前代码以 `main.py` 为统一入口，支持内容生产、内容质检、SEO 优化、图片规划、CMS 草稿发布、数据采集、竞品分析和爬虫内容入库等流程。

> 当前仓库仍包含部分演示/占位实现。真实外部调用依赖 `.env` 中的 API Key、CMS 配置和各 agent 的运行模式开关。默认配置优先保证本地可运行和 dry-run，不会直接发布到生产 CMS。

## 当前实现概览

### 入口

- `main.py`：命令行入口，支持选择不同执行引擎。
- 默认执行引擎：`hybrid`。
- 支持的引擎：
  - `hybrid`：推荐的内容生产主流程，结合 LangGraph 状态流转和本地 agent/tool 实现。
  - `langgraph`：基于 LangGraph 的状态机示例流程，部分节点仍是简化实现。
  - `crewai`：基于 CrewAI 的顺序式多 agent 流程。
  - `crawler`：爬虫内容清洗、路由和入库 dry-run 流程。

### 主流程

`hybrid` 工作流位于 `workflows/hybrid_workflow.py`，当前节点顺序为：

```text
research -> write -> edit -> seo -> image -> cms -> evolve
```

主要行为：

- `research`：通过研究/趋势/关键词相关工具生成研究结果。
- `write`：生成文章正文。
- `edit`：执行编辑和质量评分；分数低于阈值时会回到写作节点重写，带最大重试次数。
- `seo`：生成 SEO 标题、Meta 描述、Schema 和关键词建议。
- `image`：默认使用 `plan_only` 模式生成图片规划；如切换为 `generate`，需要可用的 OpenAI 图片生成配置。
- `cms`：默认 dry-run，只返回草稿发布结果；真实发布需要显式开启 CMS 配置。
- `evolve`：输出流程复盘和改进建议。

### 调度器

调度器代码位于 `scheduler/`：

- `scheduler/scheduler.py`：基于 APScheduler 的异步任务调度器。
- `scheduler/cron_tasks.yaml`：计划任务配置和说明。

当前调度器注册了主题发现、数据采集、竞品分析、周报、技术 SEO、月度复盘和爬虫入库等任务。部分任务函数仍是占位或开发阶段实现，生产运行前需要重点验证事件循环、任务启停、失败重试、通知和真实外部服务配置。

## Agent 与模块职责

```text
agents/
├── topic_agent/              # 选题与趋势发现，支持 mock/live 模式
├── writer_agent/             # 文章生成、提示词渲染、质量检查
├── editor_agent/             # 编辑、语法检查、质量评分
├── cms_agent/                # CMS 发布客户端，默认 dry-run
├── data_agent/               # 数据采集与报告
├── competitor_agent/         # 竞品监控与差距分析
├── crawler_processor_agent/  # 爬虫内容清洗、分类、路由
├── research_agent/           # 研究工具、引用格式化和配置
├── seo_agent/                # SEO 关键词、Meta、Schema 工具
└── image_agent/              # 图片规划、生成和 Alt 文本工具
```

说明：

- `TopicAgent` 默认可在 mock 模式下运行；live 模式依赖 `SERPAPI_API_KEY` 等外部配置。
- `WriterAgent`、`EditorAgent`、`CMSAgent`、`DataAgent`、`CompetitorAgent`、`CrawlerProcessorAgent` 提供独立类实现。
- `research_agent`、`seo_agent`、`image_agent` 主要通过工具、prompt 和配置参与工作流。
- `CMSAgent` 默认不会真实发布；真实发布需要同时满足 `.env` 和 agent 配置中的开关。

## 项目结构

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

- `docs/00_PROJECT_STATUS_AND_ROADMAP.md`
- `docs/01_SYSTEM_ARCHITECTURE.md`
- `docs/02_API_DOCUMENTATION.md`
- `docs/03_DEPLOYMENT_GUIDE.md`
- `docs/04_USER_GUIDE.md`
- `docs/05_DEVELOPER_GUIDE.md`

这些文档可能仍包含早期设计内容。以当前代码、`main.py`、`workflows/`、`agents/` 和 `scheduler/` 中的实现为准。
