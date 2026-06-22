# 多Agent自动运营网站 (Multi-Agent Automated Operation Website)

基于 **CrewAI** 和 **LangGraph** 的多Agent内容生产与自动化运营系统。本项目旨在通过大模型技术，让多个AI Agent各司其职，组成"虚拟运营团队"，实现网站从内容策划、撰写、排版、SEO优化到CMS发布、数据分析的全链路自动化。

## 🎯 系统架构

系统由两层架构组成：
- **LangGraph 编排层**：负责整体工作流的状态管理、条件分支控制和自演化数据反馈。
- **CrewAI Agent 层**：由多个角色分明的Agent组成，执行具体的专业任务。

### 🤖 核心 Agent 团队

1. **🔍 TopicAgent (选题策划)**: 发现热点、挖掘长尾关键词、生成选题列表。
2. **📚 ResearchAgent (调研分析)**: 围绕选题进行深度调研，收集素材、生成大纲。
3. **✍️ WriterAgent (内容写作)**: 根据大纲生成高质量、SEO友好的原创文章。
4. **🔧 EditorAgent (审校编辑)**: 审校文章质量、润色、格式排版。
5. **🎨 ImageAgent (配图生成)**: 为文章生成或选择合适的配图及Alt文本。
6. **🔍 SEOAgent (SEO优化)**: 优化文章SEO要素（TDK、Schema、内链等）。
7. **📋 CMSAgent (内容发布)**: 将最终文章和多媒体资源发布到目标CMS系统。
8. **📈 DataAgent (数据分析)**: 采集网站运营数据，生成报告和优化建议。
9. **🗡️ CompetitorAgent (竞品监控)**: 监控竞品动态，提供竞争分析和策略。

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
.
├── agents/                 # Agent 配置与工具实现目录
│   ├── cms_agent/
│   ├── competitor_agent/
│   ├── data_agent/
│   ├── editor_agent/
│   ├── image_agent/
│   ├── research_agent/
│   ├── seo_agent/
│   ├── topic_agent/
│   └── writer_agent/
├── workflows/              # 工作流定义目录 (CrewAI & LangGraph)
├── scheduler/              # 定时任务调度器配置
├── config/                 # 全局配置及品牌指南 (需自行配置)
├── main.py                 # 项目主入口文件
├── requirements.txt        # Python 依赖包
├── .env.example            # 环境变量示例
└── README.md               # 项目说明文档
```

## 🚀 快速开始

### 1. 环境准备

确保已安装 Python 3.11+，并使用 venv 与系统 Python 库隔离（强烈建议使用项目级虚拟环境，不要在全局环境安装依赖）：

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. 安装依赖

如果已激活虚拟环境，可直接执行：

```bash
python -m pip install -r requirements.txt
```

### 3. 配置环境变量

复制环境变量示例文件并配置您的 API Key：

```bash
cp .env.example .env
# 编辑 .env 文件，填入 OPENAI_API_KEY 等配置
```

### 4. 运行示例工作流

```bash
python main.py
```

### 5. 使用 Makefile（可选）

```bash
make install
make run
```

### 6. 选择运行引擎

本项目同时保留了三种运行模式，便于对照与逐步演进：
- hybrid（默认）：LangGraph 负责编排，CrewAI 负责各阶段 Agent 执行
- langgraph：纯 LangGraph 版本（节点内直接调用 LLM，便于理解状态机结构）
- crewai：纯 CrewAI 版本（顺序流水线，便于理解多 Agent 的任务拆分）

```bash
python main.py --engine hybrid
python main.py --engine langgraph
python main.py --engine crewai
```

## 📖 相关文档

- [00-方案概述](00-方案概述.md)
- [01-Agent架构图](01-Agent架构图.md)
- [02-技术实现规范](02-技术实现规范.md)
- [03-工作流编排](03-工作流编排.md)
- [04-定时任务方案](04-定时任务方案.md)
- [05-部署方案](05-部署方案.md)

## 📄 许可证

MIT License
