## Summary

将 `agents/editor_agent` 从“提示词/配置/工具/工作流不对齐”的半成品，补齐为可稳定运行、可解释、与下游链路一致的审校 Agent：

- 修复 `prompt.md` 缺少正文占位符 `{content}` 的根因问题，使 EditorAgent 能真正看到文章正文
- 新增可导入的 `EditorAgent` 主类与 `execute()`，统一输入输出协议（权威字段为 `article.content_md`），并保留旧字段别名避免断链
- 让 `quality_scorer.py` 读取 `config.yaml`（权重/通过阈值/禁用词/auto_fix 等），消除“配置与代码两套标准”
- 改造 `grammar_checker.py`：默认只产出 issues + patches（不直接全量改写正文），由 EditorAgent 决定是否应用
- 把工具接入工作流：LangGraph/Hybrid 使用 EditorAgent.execute；CrewAI EditorAgent 挂载工具并更新任务约束
- 补齐 editor_agent 相关测试，覆盖 prompt 占位符、配置生效、输出协议、LLM 回退与工作流字段提取

---

## Current State Analysis (Grounded)

### 1) prompt.md 没有 `{content}`，导致工作流注入正文失效

`langgraph_workflow.py` 的 edit 节点会替换 `{content}`，但 [prompt.md](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/editor_agent/prompt.md) 的“文章信息”里没有正文占位符，LLM 可能拿不到正文。

- [langgraph_workflow.py:L276-L309](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L276-L309)
- [prompt.md:L55-L66](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/editor_agent/prompt.md#L55-L66)

### 2) SKILL.md 写了不存在的 EditorAgent 类

文档写 `from agents.editor_agent import EditorAgent`，但目录无主类与统一执行入口。

- [SKILL.md:L57-L72](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/editor_agent/SKILL.md#L57-L72)

### 3) config.yaml 很完整，但 quality_scorer/grammar_checker 未消费

- 配置存在质量权重/通过阈值/禁用词/auto_fix 等（[config.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/editor_agent/config.yaml)）
- 但 [quality_scorer.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/editor_agent/tools/quality_scorer.py) 权重、pass>=75 都硬编码
- [grammar_checker.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/editor_agent/tools/grammar_checker.py) 的自动修正是直接 `re.sub`，风险不可控

### 4) 工作流没有稳定使用工具 & 输出字段不统一

- `crewai_workflow.py` 创建 EditorAgent 时未挂载 tools（目前 editor_agent 只有 llm）：
  - [crewai_workflow.py:L130-L142](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py#L130-L142)
- `hybrid_workflow.py` 的编辑节点字段建议是 `revised_article:{content_md}`，而 `prompt.md/config.yaml` 又以 `reviewed_article` 为主，导致下游容易断链：
  - [hybrid_workflow.py:L291-L316](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L291-L316)
- `langgraph_workflow.py` 的 CMS 节点优先读取 `edit_result["article"]`，但 Editor 输出并不保证有 `article` 字段，导致 CMS 实际可能用写作草稿而不是审校稿：
  - [langgraph_workflow.py:L373-L389](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L373-L389)

---

## Decisions (Locked)

- LLM 审校默认策略：**默认规则审校 + LLM 双闸门**（config 开启 + 环境变量 `EDITOR_ENABLE_LLM=true` 才会调用；失败自动回退规则）
- 输出协议：**权威输出为 `article{title, content_md, meta_description}`**，并同时输出旧字段别名（reviewed_article / revised_article）避免下游断链
- grammar auto-fix：**默认不改正文，只产 patches**；由 EditorAgent 在 auto_fix 开启时选择性应用（高置信、局部替换）

---

## Proposed Changes (Decision-Complete)

### A) 统一输入输出协议 + 修复 prompt.md

**修改文件**
- `agents/editor_agent/prompt.md`

1) 在“文章信息”中补充正文占位符：
- 增加 `- **正文**: {content}`

2) 统一输出 JSON 协议（权威字段）：

```json
{
  "article": {"title": "...", "content_md": "...", "meta_description": "..."},
  "quality_score": {"overall": 85, "dimensions": {}},
  "issues_found": [],
  "polishing_notes": [],
  "approval_status": "approved"
}
```

3) 为兼容历史输出，允许 LLM 同时输出（或由 EditorAgent 归一化输出）：
- `reviewed_article`（旧字段）
- `revised_article`（旧字段）

### B) 新增 EditorAgent 主类与 execute()

**新增文件**
- `agents/editor_agent/__init__.py`
- `agents/editor_agent/editor_agent.py`

**核心能力**

- `EditorAgent(config_path="agents/editor_agent/config.yaml", prompt_path="agents/editor_agent/prompt.md")`
- `async def execute(self, article: Dict[str, Any], topic: Optional[Dict[str, Any]] = None, dry_run: bool = True) -> Dict[str, Any]`
  - 输入 article 允许来源多样：`content` / `content_md` / `content_html` 皆可；统一归一为 `content_md`
  - 规则阶段（默认执行）：
    - `GrammarChecker.check(content_md)` → issues + patches（不直接改正文）
    - `QualityScorer(config).score(article=..., topic=..., grammar_result=...)` → 维度分 + overall + pass
    - 根据 `config.quality.pass_threshold` 与 `critical_issues` 生成 `approval_status`
  - LLM 阶段（双闸门 + 回退）：
    - 新增 config：`execution.llm_review_enabled: false`（默认 false）
    - 新增 env：`EDITOR_ENABLE_LLM=false`（需 true 才允许）
    - 满足双闸门后：用 prompt.md 注入 `{title}/{content_type}/{primary_keyword}/{word_count}/{content}` 调 LLM 输出 JSON
    - 任意异常（无 key/网络/解析失败）：捕获并回退规则阶段结果（不让流水线中断）
  - 输出字段：
    - 权威：`article{title, content_md, meta_description}` + `quality_score{overall, dimensions}` + `issues_found` + `polishing_notes` + `approval_status`
    - 兼容：同时输出 `reviewed_article`（映射到 `article`）与 `revised_article`（同样映射）
    - 附加：`tool_results.grammar` / `tool_results.quality` 便于排查与调参

### C) 让 QualityScorer 读取 config.yaml，并修正评分口径

**修改文件**
- `agents/editor_agent/tools/quality_scorer.py`

1) 读取配置
- 构造函数改为 `QualityScorer(config: Optional[Dict[str, Any]] = None)`
- 从 config 读取：
  - `quality_scoring.weights`
  - `quality.pass_threshold`
  - `brand_consistency.prohibited_words`（启用/动作/词表）

2) 统一维度命名（与 config 对齐）
- 输出维度 key 使用：`content_quality/logical_clarity/language_expression/seo_optimization/brand_consistency`

3) 修正字数与关键词密度口径（低成本版本）
- 字数：基于“清洗后的文本”计数（剔除 Markdown 标记、链接、代码块的影响）
- 关键词密度：以词/字计数为分母（中文按汉字、英文按单词），而不是 `len(content)` 字符数

4) 输出协议对齐
- 输出结构中同时给出：
  - `quality_score.overall`（0-100）
  - `quality_score.dimensions`（0-100 或 1-10，最终统一到 0-100）
  - `pass`（>= pass_threshold）
  - `issues_found`（包含禁用词命中、关键词缺失、结构问题等）

### D) grammar_checker 改为 issues + patches（默认不直接改正文）

**修改文件**
- `agents/editor_agent/tools/grammar_checker.py`

1) 保留现有轻量规则，但 `check()` 结构升级：
- `issues`: 兼容现有
- `patches`: 新增（每条给出 start/end/replacement/reason/confidence）

2) `auto_correct()` 保留但不在工作流默认调用
- EditorAgent 根据 `config.execution.auto_fix.fix_grammar` 决定是否选择性应用 patches（例如只应用标点/重复字符等高置信规则）

### E) 把工具接入 workflow，并统一下游字段

**修改文件**

1) `workflows/langgraph_workflow.py`
- `_edit_node` 改为调用 `EditorAgent.execute(article=write_result["article"], topic=state["topic"])`
- 将结果写入 `state["edit_result"]`，确保 `edit_result.article.content_md` 存在

2) `workflows/hybrid_workflow.py`
- `_edit_node` 改为调用 `EditorAgent.execute(...)`（替换当前“LLM 直接审校 JSON”）
- 保持 `_extract_quality_score()` 可从新旧字段提取（必要时补充兼容键）

3) `workflows/crewai_workflow.py`
- 创建 `self.editor_agent` 时挂载 tools：
  - `tools=[get_grammar_checker_tool(), get_quality_scorer_tool()]`
- 更新 edit_task 描述：要求调用工具并把 tool 输出合并到最终 JSON（至少要有 `quality_score.overall` 与 `article.content_md`）

4) 下游字段统一建议（在本次改造中落地到工作流拼装处）
- CMS 节点、SEO 节点优先读取 `edit_result.article.content_md`（若缺失再回退旧字段）

### F) 测试补齐（editor_agent）

**新增测试文件（unittest）**
- `tests/test_editor_prompt_has_content_placeholder.py`
  - 断言 prompt.md 含 `{content}`
- `tests/test_quality_scorer_uses_config.py`
  - 禁用词命中、pass_threshold 生效、维度 key 对齐
- `tests/test_editor_agent_output_contract.py`
  - execute 输出包含 `article.content_md` 且兼容字段存在
- `tests/test_editor_agent_llm_gate_and_fallback.py`
  - env 未开启 → 不调用 LLM；env+config 开启且 LLM 抛错 → 回退规则并不中断
- `tests/test_workflow_edit_result_field_alignment.py`
  - mock EditorAgent.execute，确保 workflow 能把结果写入 `state.edit_result` 并被下游正确读取

---

## Verification

1) 编译检查
- `venv\\Scripts\\python.exe -m py_compile agents\\editor_agent\\tools\\grammar_checker.py agents\\editor_agent\\tools\\quality_scorer.py`
- `venv\\Scripts\\python.exe -m py_compile agents\\editor_agent\\editor_agent.py workflows\\langgraph_workflow.py workflows\\hybrid_workflow.py workflows\\crewai_workflow.py`

2) 单测
- `venv\\Scripts\\python.exe -m unittest -v`

3) 手工验证（离线可重复）
- `main.py --engine langgraph` 或 `--engine hybrid` 跑通一条链路：
  - 确认 edit_result 包含 `article.content_md`
  - 确认 quality_score.overall 可被路由/打印提取
  - dry-run 下不触发 LLM（默认规则审校）

