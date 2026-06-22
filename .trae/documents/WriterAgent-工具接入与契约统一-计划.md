# WriterAgent 主要缺陷修复计划

## Summary

目标：把 `agents/writer_agent` 从“只有 prompt/配置/单个工具文件”的占位状态，升级为可在 **独立调用 / CrewAI 工作流 / LangGraph 工作流 / Hybrid 工作流**中稳定运行的 WriterAgent，实现统一输出契约、完整 prompt 渲染、可读性与质量规则落地、JSON 解析容错、引用约束落地，并补齐测试。

本计划采用用户确认的偏好：
- 输出契约：`article.content_md` 版（统一字段名）
- 引用约束：硬失败（无可回链引用则失败并重写）
- 质量策略：严格失败 + 重写（有最大重试次数）

## Current State Analysis

### 1) WriterAgent 类缺失
- `agents/writer_agent` 目录只有 `prompt.md / config.yaml / tools/readability_checker.py`，没有 `WriterAgent` 类实现。
- `agents/writer_agent/tools/__init__.py` 仅有注释，无法按 `from agents.writer_agent import WriterAgent` 使用。
- 影响：SKILL.md 的示例无法运行；LangGraph/CrewAI/Hybrid 无法复用统一的写作实现。

### 2) CrewAI WriterAgent 未挂载可读性工具
- `workflows/crewai_workflow.py` 创建 `writer_agent` 时未挂载 `get_readability_checker_tool()`。
- 影响：即便 `readability_checker.py` 存在，写作流程也无法调用/无法在后处理阶段做可读性校验。

### 3) LangGraph prompt 填充不完整 + JSON 解析脆弱
- `workflows/langgraph_workflow.py` 写作节点仅替换 `{title}/{primary_keyword}/{content_type}/{research_materials}`，`prompt.md` 中大量占位符残留。
- 写作节点直接 `json.loads(response.content)`，对代码块/解释文字/缺字段完全不容错。

### 4) 输出契约不一致
现状多版本并存：
- `agents/writer_agent/prompt.md`：`article.title/meta_description/content`
- `agents/writer_agent/config.yaml`：`article_markdown/meta_description/...`
- `workflows/hybrid_workflow.py`：建议 `article:{title, content_md}`
- `workflows/langgraph_workflow.py`：下游主要取 `write_result.article`
影响：正文可能丢失或下游拿不到字段；SEO/Editor 节点已在用 `content_md` 优先读取。

### 5) 配置质量规则未落地
`agents/writer_agent/config.yaml` 声明了字数、段落长度、标题长度、禁用词、关键词密度、min_quality_score 等，但写作后无校验器/无重写策略。

### 6) readability_checker 统计逻辑存在系统性误判
主要问题：
- 中文“难词”统计把非中文字符/空格当难词（未使用 `common_chinese_chars`）。
- `_clean_text` 把所有空白压成单空格，导致段落数统计几乎恒为 1。
- 英文句子分割不含 `.`，Flesch 失真。
- 英文复杂词判断 `difficult > len([]) * 0.3` 恒等于 `difficult > 0`。

### 7) 引用约束未落地
prompt 要求“事实/数据必须标明来源”，但没有任何校验：无法阻止模型编造数据，或引用未能回链到 `research_result.sources/citations`。

## Proposed Changes (Decision-Complete)

### A) 新增真正的 WriterAgent 类与包导出

#### 1) 新增文件
- `agents/writer_agent/writer_agent.py`
  - 提供 `class WriterAgent`，核心接口：
    - `async def execute(self, *, topic: Dict[str, Any], outline: Optional[Dict[str, Any]], materials: Dict[str, Any], brand_config: Optional[Dict[str, Any]] = None, dry_run: bool = False, mode: Optional[str] = None) -> Dict[str, Any]`
  - 内部职责：
    - 读取 `agents/writer_agent/config.yaml`（支持 `${ENV}` 解析）
    - 读取 `agents/writer_agent/prompt.md`
    - 构造渲染上下文（topic/research/brand/config 默认值齐全）
    - 一次性渲染所有占位符（保证无 `{xxx}` 残留）
    - 调用 LLM（通过项目现有 LLM 调用方式：与 TopicAgent/EditorAgent 类似，保持一致）
    - 解析 JSON（支持代码块/前后解释文字；缺字段补默认）
    - 运行质量校验（字数/段落长度/禁用词/关键词密度/可读性/标题层级）
    - 引用一致性校验（硬失败，失败会触发重写）
    - 重写策略（最多 2 次）：将质量失败原因作为 “修复指令” 追加到第二次 prompt

- `agents/writer_agent/__init__.py`
  - `from .writer_agent import WriterAgent`
  - `__all__ = ["WriterAgent"]`

#### 2) 调整文件
- `agents/writer_agent/tools/__init__.py`
  - 导出 `ReadabilityChecker` 与 `get_readability_checker_tool`，并作为 WriterAgent 内部可直接复用的工具入口。

### B) 统一输出契约（content_md 版）

统一为：

```json
{
  "article": {
    "title": "...",
    "content_md": "...",
    "meta_description": "..."
  },
  "seo_analysis": {},
  "internal_links": [],
  "image_alt_texts": [],
  "statistics": {
    "word_count": 0,
    "reading_time_minutes": 0
  },
  "quality_checks": {},
  "warnings": []
}
```

落地调整点：
- `agents/writer_agent/prompt.md`：JSON 模板中 `article.content` 改为 `article.content_md`；补充“必须输出纯 JSON，不要代码块”的硬约束；在 prompt 中明确要求追加 `## 参考来源` 小节（用于引用回链）。
- `agents/writer_agent/config.yaml`：`output.output_fields` 改为与契约一致（移除 `article_markdown`，改为 `article`/或保持但映射到 `article.content_md`，最终以统一契约输出）。
- `workflows/hybrid_workflow.py`：写作阶段的字段建议文字改为统一契约（避免引导模型输出旧字段）。
- `workflows/langgraph_workflow.py`：写作节点输出统一结构到 `state["write_result"]`，保证下游 Editor/SEO/CMS 可消费。
- 若存在其他消费方（例如 CMS 发布节点）读取旧字段，在实现阶段统一做兼容映射（优先 `content_md`，兼容 `content`）。

### C) Prompt 渲染：结构化 context 一次性填充

在 `WriterAgent` 内实现 `_render_prompt(template: str, context: Dict[str, Any]) -> str`：
- 通过正则扫描模板中所有 `{var}` 占位符，逐一从 context 取值（缺失则填默认值），避免零散 `.replace()` 漏替换。
- 默认值策略（确保“占位符不残留”）：
  - `secondary_keywords`：缺省 `[]`，注入为 JSON 字符串
  - `search_intent`：缺省 `"informational"`
  - `hierarchy_outline`：优先取 `materials.get("outline")` 或 `materials.get("detailed_outline")`，最终转为 Markdown/JSON 字符串
  - `brand_tone/must_include/prohibited_words/recommended_words`：优先 `brand_config`，否则取 `config.yaml.brand` 默认
  - `target_word_count/min_word_count/reading_time`：由 config + content_type 推导
  - `meta_description_requirements`：由 config.yaml.seo.meta_description 生成自然语言要求
- 渲染后再次扫描 `{...}` 残留：若仍存在则视为实现缺陷（单测覆盖）。

### D) LangGraph 写作节点：改为调用 WriterAgent + JSON 解析容错

在 `workflows/langgraph_workflow.py::_write_node`：
- 替换当前“读模板 + replace + llm.invoke + json.loads”的实现。
- 改为：
  - 组装 `topic/materials/brand_config`
  - `agent = WriterAgent(config_path=..., prompt_path=...)`
  - `write_result = asyncio.run(agent.execute(..., dry_run=True))`
- 这样 LangGraph 写作节点直接复用统一逻辑：
  - prompt 无占位符残留
  - JSON 容错解析
  - 质量校验 + 重写
  - 引用约束

### E) CrewAI：挂载可读性工具 + 强化写作任务约束

在 `workflows/crewai_workflow.py`：
- 创建 `WriterAgent`（CrewAI Agent）时增加 `tools=[get_readability_checker_tool()]`（从 `agents.writer_agent.tools.readability_checker` 导入）。
- `write_task` 描述中补充强约束：
  - 必须输出统一契约（`content_md`）
  - 必须在文末生成 `## 参考来源` 并附可回链 URL（来自 research 的 sources/citations）
  - 必须调用 `readability_checker` 检查正文，并把结果摘要写入 `quality_checks.readability`

说明：CrewAI 侧是否能强制调用工具不可完全保证，因此实现阶段仍以 WriterAgent（Python 后处理）校验为主；CrewAI 工具调用作为增强。

### F) 可读性工具重写（readability_checker.py）

在 `agents/writer_agent/tools/readability_checker.py`：
- 中文：
  - 段落统计：在清洗前保留 `\n\n`（或先基于原文 split 段落计数）
  - 难字统计：对中文字符集合，统计 `char not in common_chinese_chars` 的数量（可按比例/每百字惩罚），不再用 “总字符 - 中文 - 标点” 的错误公式
  - 句子分割：沿用中文标点，但不吞掉换行导致段落消失
  - 分数：对难字惩罚设置合理上限，避免正常短文被打到 40 分
- 英文：
  - 句子分割：加入 `[.!?]`，并处理缩写/小数点的最小容错（先做基础规则，单测覆盖）
  - 复杂词阈值：修复 `len([])`，改为 `difficult_words > word_count * ratio`（ratio 取 0.12 作为默认，可配置）
- 保持 `get_readability_checker_tool()` 接口不变，确保工作流可直接挂载。

### G) 引用约束（硬失败）落地

在 `WriterAgent` 内新增引用校验与输出字段：
- 从 `materials`（通常是 research_result）读取 `sources/citations`：
  - 归一化出 `citation_urls`（url 字段或 text 中提取 http(s)）
  - 归一化出 `citation_titles`（title/name 字段）
- 要求正文满足：
  - 末尾存在 `## 参考来源` 小节
  - 小节中至少出现 1 条来自 research 的可回链 URL
- 产物里输出：
  - `quality_checks.citations = { "used": [...], "unused": [...], "passed": bool }`
- 若不满足：视为失败 → 触发重写（把缺失原因写入反馈 prompt）。

### H) 测试补齐

新增测试文件（unittest）：
- `tests/test_writer_agent_prompt_render.py`
  - 给定最小 topic/materials，渲染后不应残留 `{xxx}`
- `tests/test_writer_agent_contract.py`
  - `WriterAgent` 输出必须包含 `article.content_md/statistics/quality_checks/warnings`
- `tests/test_writer_agent_json_extract.py`
  - LLM 输出包在 ```json 代码块中仍能解析
- `tests/test_readability_checker.py`
  - 中文两段文本 `paragraph_count >= 2`
  - 英文包含 `.` 可正确分句（sentence_count>1）且复杂词阈值不再 “有1个就报错”
- `tests/test_writer_agent_quality_gate.py`
  - 包含禁用词时触发失败/重写（用 stub 输出或直接测校验函数）
- `tests/test_writer_agent_citation_gate.py`
  - research 有 sources/citations，但正文无 “参考来源/URL” 时必须失败

## Assumptions & Decisions

- WriterAgent 的 LLM 调用方式将复用项目现有模式（与 `EditorAgent/TopicAgent` 一致），不引入新依赖（例如 jinja2）。
- 引用约束以“参考来源小节 + URL 回链”为最小可用标准；不尝试做复杂的“句子级数据对齐引用”。
- 质量重写最多 2 次，避免无限循环；失败会返回包含原因的结构化 error/warnings。
- 可读性分数仍输出 0-100，但修复统计逻辑后保证不会对正常短文系统性误判。

## Verification Steps

实现完成后执行：
1) 语法检查
```bash
.\venv\Scripts\python.exe -m py_compile agents\writer_agent\writer_agent.py agents\writer_agent\tools\readability_checker.py workflows\crewai_workflow.py workflows\langgraph_workflow.py workflows\hybrid_workflow.py
```

2) 单测
```bash
.\venv\Scripts\python.exe -m unittest -q tests/test_writer_agent_prompt_render.py tests/test_writer_agent_contract.py tests/test_writer_agent_json_extract.py tests/test_readability_checker.py tests/test_writer_agent_quality_gate.py tests/test_writer_agent_citation_gate.py
```

3) 冒烟验证（人工/日志）
- LangGraph 主流程能跑到写作节点并生成 `state.write_result.article.content_md`
- CrewAI 写作任务输出结构字段齐全，且 WriterAgent 后处理的 `quality_checks.readability/citations` 可用
- EditorAgent/SEO 节点能从 write_result 读取正文（优先 `content_md`）且不丢字段

