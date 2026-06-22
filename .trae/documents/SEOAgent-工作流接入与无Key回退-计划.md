# SEOAgent 工作流接入与无 Key 回退（Plan）

## Summary
为项目新增可独立测试的 `SEOAgent` 主类，并将 `hybrid_workflow.py` 与 `langgraph_workflow.py` 的 SEO 节点改为优先调用该 Agent。即使没有可用 OpenAI API Key，也能稳定通过规则工具（KeywordAnalyzer / MetaGenerator / SchemaGenerator）生成标准 `seo_result`，保证后续 Image/CMS 节点可消费。

## Current State Analysis
### 代码现状
- `agents/seo_agent/` 目前只有配置与工具，无 `SEOAgent` 主类：
  - [agents/seo_agent/config.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/config.yaml)
  - [agents/seo_agent/prompt.md](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/prompt.md)
  - 规则工具已存在且可单测：
    - [KeywordAnalyzer](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/keyword_analyzer.py)
    - [MetaGenerator](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/meta_generator.py)
    - [SchemaGenerator](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/schema_generator.py)
- `HybridWorkflow._seo_node` 目前走 `_run_crewai_step` 调 LLM 产出 JSON，属于“临时拼 prompt”路径，缺少无 Key 回退与统一契约落地（见 [hybrid_workflow.py:_seo_node](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L380-L428)）。
- `LangGraphWorkflow._seo_node` 目前直接读 `prompt.md` → `self.llm.invoke()` → `json.loads()`，需要可用 LLM 才稳定（见 [langgraph_workflow.py:_seo_node](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L420-L509)）。
- 现有测试对工作流 SEO 节点的要求主要是“seo_result 字段存在且齐全”，例如：
  - [test_workflow_seo_result_presence.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/tests/test_workflow_seo_result_presence.py)
  - SEO 工具层已有单测（keyword/meta/schema），但没有 “SEOAgent.execute” 契约测试。

### 约束/目标
- 不做无关重构：仅涉及 SEOAgent、SEO workflow 节点与相关测试。
- 不依赖真实 API Key：无 OPENAI_API_KEY 时不得报错，应回退到规则工具生成基础 `seo_result`。
- 工作流稳定：hybrid/langgraph 均可稳定产出可消费 `seo_result`。
- 保留 LLM 能力路径：允许未来在显式开关 + 有 Key 时再走 LLM 增强，但默认不打开。

## Proposed Changes

### 1) 新增 SEOAgent 主类
**新增文件**
- `agents/seo_agent/seo_agent.py`
- `agents/seo_agent/__init__.py`（导出 `SEOAgent`，与 Writer/Editor/Research 一致）

**SEOAgent.execute 接口**
```python
async def execute(
    self,
    *,
    article: dict,
    topic: dict | None = None,
    page_info: dict | None = None,
    dry_run: bool = True,
) -> dict:
    ...
```

**输入解析（最小且健壮）**
- 从 `article` 中提取：
  - `title`（优先 `article["title"]`，否则 topic.title）
  - `content`（优先 `content_md`，否则 `content`/`content_html`）
  - `meta_description`（可能来自 `article["meta_description"]` 或 `article["meta"]["meta_description"]`）
- 从 `topic` 中提取：
  - `primary_keyword`
  - `secondary_keywords`
  - `content_type`/`category`
- `page_info` 若提供，用于补齐 category/tags/slug 等（不强制）。

**输出 seo_result 统一契约（与现有 workflow 对齐）**
SEOAgent SHALL 输出 dict 且至少包含：
- `optimized_article`: `{ "title": str, "content": str }`
- `meta_title`: str
- `meta_description`: str
- `og_tags`: dict
- `twitter_tags`: dict
- `schema_json`: dict
- `internal_links`: list
- `seo_report`: dict
- `improvement_suggestions`: list
- `warnings`: list

**规则工具 fallback（默认路径）**
- `KeywordAnalyzer.analyze(...)` → 写入 `seo_report["keyword_analysis"]`
- `MetaGenerator.generate(...)` → 写入 `meta_title/meta_description/og_tags/twitter_tags`，并把工具 warnings 合并到总 warnings
- `SchemaGenerator.generate(...)` → 写入 `schema_json`
- `optimized_article` 默认返回原 `title` + 原 `content`（不做 LLM 改写；只保证 meta/schema/报告齐全）
- `internal_links` 默认 `[]`（后续可扩展；本次不做内链生成重构）
- `improvement_suggestions` 给出少量规则化建议（根据 keyword_analysis/meta_generator 的 warnings 补齐），保证非空但不做过度生成。

**LLM 增强路径（保留但默认关闭）**
- 仅当同时满足才尝试：
  - `dry_run is False`
  - 环境变量 `SEO_ENABLE_LLM=true`（新约定，不修改现有逻辑）
  - `OPENAI_API_KEY` 非空
- 若满足，读取 `agents/seo_agent/prompt.md`，把规则产物作为上下文，尝试调用 LLM 输出更完整结果并与规则结果 merge；任何异常都只写 warnings 并回退规则结果，不允许抛出导致工作流失败。

### 2) 改造 HybridWorkflow SEO 节点接入
**修改文件**
- `workflows/hybrid_workflow.py`

**改造点**
- `_seo_node` 不再走 `_run_crewai_step`，改为：
  - 显式初始化 `SEOAgent(config_path=..., prompt_path=...)`（与 EditorAgent 一致，避免依赖 cwd）
  - 使用项目已有 `_run_async_sync` 执行 `SEOAgent.execute(...)`，避免在已有 event loop 内 `asyncio.run`
  - `state["seo_result"]` 必须是 dict（如异常则走 `_workflow_error` 并进入 ERROR）
- 输入 article 选择顺序：优先 `edit_result.article`，否则回退 `write_result.article`（与 langgraph_workflow 当前逻辑一致）。

### 3) 改造 LangGraphWorkflow SEO 节点接入
**修改文件**
- `workflows/langgraph_workflow.py`

**改造点**
- `_seo_node` 改为调用 `SEOAgent.execute(...)`（同样传入 `config_dir` 下的显式路径）
- 保留现有 JSON “补字段/归一化”逻辑的核心意图，但落到 SEOAgent 内部（避免重复），节点内只做最少的异常包装与 state 写入。

### 4) 测试补齐与调整
**新增测试**
- `tests/test_seo_agent_contract.py`
  - 验证 `SEOAgent` 可导入
  - 无 OPENAI_API_KEY 时 `execute(dry_run=True)` 仍返回完整 seo_result 字段与类型
  - `schema_json`/`og_tags`/`twitter_tags` 为 dict，`internal_links`/`improvement_suggestions`/`warnings` 为 list

**更新/补充工作流节点测试**
- 调整 [test_workflow_seo_result_presence.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/tests/test_workflow_seo_result_presence.py)
  - 从依赖 DummyLLM 改为：在 `wf.llm` 不可用的情况下仍能产出 `seo_result`（验证 fallback）
- 新增 `tests/test_hybrid_seo_node_contract.py`
  - patch `agents.seo_agent.SEOAgent.execute` 或直接跑规则路径
  - 验证 `_seo_node` 写入 `state["seo_result"]` 为 dict 且字段齐全
- 如现有测试依赖 LLM 文本 JSON，可保留一条“LLM 输出解析”单测，但需改为“可选”，不影响无 Key 环境（本次优先保证无 Key 可跑）。

## Assumptions & Decisions
- 默认不启用 LLM：通过新增环境变量 `SEO_ENABLE_LLM` 控制（默认 false），且必须有 `OPENAI_API_KEY` 才尝试。
- 不重写文章正文：`optimized_article.content` 直接沿用输入正文（content_md/content_html 任一），本次只保证 meta/schema/report 输出稳定。
- internal_links 本次保持空列表：不引入内链生成器/站点图数据源，避免无关扩展。
- 若 topic/page_info 缺失，仍输出基础可用结果，并写入 warnings（不抛异常）。

## Verification
### 单测
- 运行 SEOAgent 新增/改动测试：
  - `.\venv\Scripts\python.exe -m pytest -q tests\test_seo_agent_contract.py tests\test_workflow_seo_result_presence.py`
  - 如项目虚拟环境为 `.venv`，替换为 `.\.venv\Scripts\python.exe`
- 回归：
  - `.\venv\Scripts\python.exe -m pytest -q tests`

### 语法检查
- `.\venv\Scripts\python.exe -m compileall -q agents\seo_agent workflows\hybrid_workflow.py workflows\langgraph_workflow.py`

