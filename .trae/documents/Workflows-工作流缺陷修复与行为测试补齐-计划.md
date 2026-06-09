# Workflows 工作流缺陷修复与行为测试补齐计划

## Summary

- 目标：修复 `workflows/` 中已确认的真实缺陷，补齐被测试顺序掩盖的问题，并把工作流运行行为、错误模型、日志输出和关键状态流转测试统一到可回归的形态。
- 成功标准：
  - 单独导入或单独运行 crawler 相关测试时，不再出现 `partially initialized module` 循环导入错误。
  - `HybridWorkflow` 与 `MultiAgentWorkflow` 不再在业务节点中直接使用裸 `asyncio.run()`；同步适配只保留在统一 helper 中，并在已有 event loop 内给出明确错误。
  - `HybridWorkflow` 质量评分内部统一使用 `0-100`，兼容历史 `0-1` 输入与 `0.8` 阈值配置。
  - `langgraph_workflow.py` 的图片/CMS 节点输出契约与 `hybrid_workflow.py` 对齐，并走真实 agent/tool 接线而非纯占位。
  - `workflows` 中错误信息统一为结构化对象，`crewai_workflow.py` 与 `langgraph_workflow.py` 使用结构化日志替代 `print`。
  - 新增行为测试覆盖循环导入、评分归一化、同步适配、LangGraph 图片/CMS 状态流转，并完成 `compileall` 与 `pytest` 回归验证。

## Current State Analysis

- 已确认 `agents/crawler_processor_agent/crawler_processor_agent.py` 把 `run_crawler_workflow` 挪到了 `execute()` 内部延迟导入，`agents/crawler_processor_agent/__init__.py` 也改成了 `__getattr__` 惰性导出；说明循环导入已开始修复，但仍需要通过独立测试稳定锁定。
- `tests/test_crawler_workflow_import_stability.py` 已存在，覆盖了“先导入 agent 再导入 workflow”和相反顺序的两种路径，可作为本轮回归的基础用例。
- `workflows/hybrid_workflow.py` 已新增 `_run_async_sync()`、`_workflow_error()`、`_extract_quality_score()` 等 helper，但 `_route_after_edit()` 当前仍写成 `if score < threshold * 100`；由于阈值已在前面被归一化过一次，这里会造成二次放大，属于未完成修复。
- `workflows/langgraph_workflow.py` 也已引入 `_run_async_sync()` 和结构化错误 helper，并把写作/编辑/图片/CMS 节点部分接到了真实 agent/tool；但文件中仍残留大量 `print(...)`，尚未完成日志治理。
- `workflows/crewai_workflow.py` 目前已经使用 `logger.info()/warning()`，`print(...)` 已清空，说明 CrewAI 部分日志迁移已完成。
- `workflows/crawler_workflow.py` 目前已有 `llm_error: Optional[Dict[str, Any]]` 和结构化错误写入点，说明 crawler 工作流错误模型已有基础，但仍需结合导入测试和定向回归一起验证。
- 现有 workflow 测试中，`tests/test_workflow_edit_result_field_alignment.py` 已是行为测试；`tests/test_workflow_seo_result_presence.py` 的 LangGraph SEO 节点测试已是行为测试，但其中 `test_crewai_seo_agent_has_tools()` 仍是源码字符串检查风格，本轮不强制扩展到 SEO，只在 workflow 新缺陷范围内补真实行为测试。

## Assumptions & Decisions

- 保持当前对外入口为同步工作流接口，不做整套 async API 重写；同步入口在检测到已有 event loop 时明确失败，并通过错误信息说明不支持该调用方式。
- `HybridWorkflow` 与 `MultiAgentWorkflow` 的内部质量评分协议统一为 `0-100`；若 agent 返回 `0-1` 浮点分数，则在 helper 中归一化为 `0-100`。
- 历史配置中的 `quality_threshold=0.8` 继续兼容，统一解释为 `80`；若显式配置大于 `1` 的阈值，则按 `0-100` 直接使用。
- `langgraph_workflow.py` 的图片和 CMS 节点不再保留“示例/占位”语义，而是按当前已落地的 `hybrid_workflow.py` 契约对齐：
  - 图片节点输出 `featured_image_url`、`featured_alt`、`featured_prompt`、`inline_images`、`license`
  - CMS 节点接收文章、SEO、图片结果并调用 `CMSAgent.execute(...)`
- 错误对象统一最小结构：
  - `stage`
  - `type`
  - `message`
  - `input_id`
  - `trace_id`
- 日志治理范围包含 `workflows/langgraph_workflow.py` 与 `workflows/crewai_workflow.py`；其中 `crewai_workflow.py` 主要做一致性检查，`langgraph_workflow.py` 需要把现有 `print` 迁移为 `logger`。

## Proposed Changes

### 1. 稳定 crawler 导入链路

**文件**
- `agents/crawler_processor_agent/crawler_processor_agent.py`
- `agents/crawler_processor_agent/__init__.py`
- `tests/test_crawler_workflow_import_stability.py`

**修改内容**
- 保持当前延迟导入与惰性导出实现，不再回退到任何顶层互相引用写法。
- 检查 `CrawlerProcessorAgent` 与 `crawler_workflow` 之间是否还有隐式导入链（例如经由 tool package 或 `__all__`）导致二次初始化风险；如有，继续改为局部导入或惰性导出。
- 保留并回归 `tests/test_crawler_workflow_import_stability.py`，必要时补充“直接导入 `CrawlerProcessorAgent` 符号”的断言，确保 `from agents.crawler_processor_agent import CrawlerProcessorAgent` 也稳定。

**原因**
- 这是当前唯一已被单独运行测试明确复现的真实缺陷，优先级最高。

### 2. 收敛同步适配层并移除业务节点裸 `asyncio.run`

**文件**
- `workflows/hybrid_workflow.py`
- `workflows/langgraph_workflow.py`

**修改内容**
- 只保留 `_run_async_sync()` 中的 `asyncio.run()`，不允许业务节点直接调用。
- 检查所有节点调用异步 agent 的路径，统一经过 `_run_async_sync()`。
- 明确 `_run_async_sync()` 的错误语义：
  - 无运行中 event loop：正常 `asyncio.run(coro)`
  - 已有运行中 event loop：关闭协程并抛出带 `stage/input_id/trace_id` 的明确异常
- 确保该 helper 在 `HybridWorkflow` 与 `MultiAgentWorkflow` 两边行为一致，避免一边抛结构化错误、一边抛裸字符串。

**原因**
- 当前剩余两处 `asyncio.run()` 都集中在 helper 内，说明主体重构接近完成；这一轮要把 helper 契约和测试补完整，防止未来回归。

### 3. 修正 Hybrid 质量阈值归一化逻辑

**文件**
- `workflows/hybrid_workflow.py`
- `tests/test_hybrid_workflow_quality_score_normalization.py`（新增）

**修改内容**
- 修复 `_route_after_edit()` 中对 `threshold` 的重复乘以 `100` 问题。
- 明确以下路由规则：
  - `score=0.75` 与 `quality_threshold=0.8` 时，应视为 `75 < 80`，触发重写。
  - `score=75` 与 `quality_threshold=0.8` 时，也应触发重写。
  - `score=85` 与 `quality_threshold=80` 时，应继续后续阶段。
  - 缺失质量分数时，保持当前策略直接继续，不额外强制失败。
- 新增测试覆盖：
  - `_extract_quality_score()` 接收 `0-1`
  - `_extract_quality_score()` 接收 `0-100`
  - `_route_after_edit()` 对旧阈值 `0.8`
  - `_route_after_edit()` 对新阈值 `80`
  - 超过重试上限时不再回写 `retry_write`

**原因**
- 当前 helper 已做了半套归一化，但路由判断仍有逻辑错误；如果不补测试，很容易再次被重构破坏。

### 4. 补齐 LangGraph 图片与 CMS 节点

**文件**
- `workflows/langgraph_workflow.py`
- 相关依赖文件仅做调用，不改契约：
  - `agents/image_agent/tools/image_generator.py`
  - `agents/image_agent/tools/alt_text_generator.py`
  - `agents/cms_agent/...`
- `tests/test_langgraph_image_cms_flow.py`（新增）

**修改内容**
- 保留当前已接近完成的 `_image_node()` / `_cms_node()` 方向，继续补齐缺省字段、异常分支和状态写回。
- 图片节点：
  - `plan_only` 模式下，接受 LLM 生成的配图计划，并补齐缺省键，保证输出结构稳定。
  - `generate` 模式下，通过 `ImageGenerator` 与 `AltTextGenerator` 填充 `url/alt/license`，失败时写入结构化错误。
- CMS 节点：
  - 统一从 `topic`、`seo_result`、`image_result` 组装 payload。
  - 调用 `CMSAgent.execute(...)` 产出真实 `cms_result`，不再只返回 LLM 规划稿。
  - 对 `featured_image_url`、`featured_alt`、`meta_title`、`meta_description` 等字段做兼容映射，避免上下游契约差异导致空字段。
- 新增行为测试：
  - mock `llm.invoke()` 返回图片规划 JSON，断言 `_image_node()` 写入统一字段。
  - mock `CMSAgent.execute()`，断言 `_cms_node()` 正确消费 `image_result` 并把结果写入 `cms_result`。
  - 覆盖 `plan_only` 路径，避免测试依赖真实图片 API。

**原因**
- 当前 LangGraph 已不再是纯占位，但还没有被测试锁定；需要把“接近 Hybrid”的目标变成明确行为。

### 5. 统一 workflow 错误对象与日志输出

**文件**
- `workflows/hybrid_workflow.py`
- `workflows/langgraph_workflow.py`
- `workflows/crawler_workflow.py`
- `workflows/crewai_workflow.py`
- `tests/test_workflow_sync_adapter.py`（新增，部分覆盖错误语义）

**修改内容**
- 检查 `except Exception as e` 分支，确保所有 `state["error"]` / `state["llm_error"]` 都写入统一结构，而不是裸字符串。
- `crawler_workflow.py` 保持现有结构化错误风格，并补查是否还有漏网的 `str(e)` 写回状态。
- `langgraph_workflow.py`：
  - 将现有阶段性 `print(...)` 迁移为 `logger.info()/warning()/error()`。
  - 启动、阶段开始/结束、失败、工作流结束统一为结构化日志格式，至少带 `workflow`、`stage`、`trace_id`，必要时带 `topic` 或 `input_id`。
- `crewai_workflow.py`：
  - 只做一致性补查，保证不回退到 `print`，并确认日志字段风格与其他工作流一致。
- 新增测试覆盖同步适配器错误：
  - 在运行中的 event loop 内调用 `_run_async_sync()`，断言抛出的错误包含 `running_event_loop_not_supported_for_sync_workflow` 与阶段信息。

**原因**
- 当前日志治理只完成了一半，`langgraph_workflow.py` 仍保留 50+ 处 `print`；如果不统一，调度器调用时仍难以排障。

### 6. 回归与验收

**文件/命令**
- `workflows/`
- `agents/crawler_processor_agent/`
- `tests/`

**验证步骤**
- 语法回归：
  - `python -m compileall workflows agents/crawler_processor_agent tests`
- 定向测试：
  - `python -m pytest -q tests/test_crawler_workflow_import_stability.py`
  - `python -m pytest -q tests/test_hybrid_workflow_quality_score_normalization.py`
  - `python -m pytest -q tests/test_workflow_sync_adapter.py`
  - `python -m pytest -q tests/test_langgraph_image_cms_flow.py`
- 全量测试：
  - `python -m pytest -q tests`
- 若 `pytest` 环境异常，再补充：
  - `python -m unittest discover -s tests -v`

**验收口径**
- crawler 独立导入与测试稳定通过。
- workflow 新增行为测试全部通过。
- 全量测试不因本轮修复引入新的失败。
- `langgraph_workflow.py` 与 `crewai_workflow.py` 不再保留业务阶段 `print` 输出。

## Out of Scope

- 不把整个 `HybridWorkflow` / `MultiAgentWorkflow` 改造成完全异步公开 API。
- 不在本轮扩展到 `main.py`、`scheduler/`、CLI 系统测试，除非 workflow 修改直接导致现有测试必须同步调整。
- 不新增新的 SEO/Writer/Research 功能；本轮只处理 `workflows/` 目录及其直接依赖的稳定性问题。
