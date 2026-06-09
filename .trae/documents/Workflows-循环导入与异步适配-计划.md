# Workflows 循环导入、异步适配与行为验证计划

## Summary

目标：修复 `workflows/` 中已经被单独运行 crawler 测试暴露出的真实缺陷，并把几个“当前测试顺序掩盖、但在生产/调度/异步调用场景下高风险”的问题一次性收敛：

1. 修复 `crawler_workflow.py` 与 `agents/crawler_processor_agent` 的循环导入。
2. 处理 `hybrid_workflow.py` / `langgraph_workflow.py` 中多个 `asyncio.run()` 的同步调用隐患。
3. 统一 Hybrid 工作流的质量评分协议为内部 `0-100`。
4. 将 `langgraph_workflow.py` 的图片/CMS 节点从“简化占位”提升到更接近 `hybrid_workflow.py` 的真实接线。
5. 用结构化日志和结构化错误替代 `print` 与裸 `str(e)`。
6. 把现有源码字符串测试升级为行为测试，尤其覆盖 crawler import、workflow 状态流转和质量路由。

本计划按用户已确认的偏好执行：
- 异步方案：**同步适配层**
- LangGraph 定位：**接近 hybrid**
- 评分协议：**内部统一 0-100**

## Current State Analysis

### 1) crawler workflow 循环导入已真实存在

已确认的导入链路：
- [crawler_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L30-L35) 顶层导入 `agents.crawler_processor_agent.tools.*`
- Python 在解析 `agents.crawler_processor_agent.tools.*` 前会执行包初始化 [agents/crawler_processor_agent/__init__.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/__init__.py#L1-L3)
- `__init__.py` 顶层导入 `CrawlerProcessorAgent`
- [crawler_processor_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/crawler_processor_agent.py#L1-L34) 顶层又反向导入 `from workflows.crawler_workflow import run_crawler_workflow`

这会在某些导入顺序下形成 `partially initialized module`，是用户描述的真实缺陷，不是理论风险。

### 2) workflows 中确实有 6 个 `asyncio.run()` 点

已确认位置：
- [langgraph_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L269-L269)
- [langgraph_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L316-L316)
- [langgraph_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L477-L477)
- [hybrid_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L308-L308)
- [hybrid_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L461-L461)
- [hybrid_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L523-L523)

在普通同步 CLI 中可运行，但在调度器、已有 event loop、异步 Web 框架、Notebook 场景都会有 `RuntimeError: asyncio.run() cannot be called from a running event loop` 风险。

### 3) Hybrid 工作流质量阈值语义不清

已确认：
- [hybrid_workflow.py:_extract_quality_score](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L82-L95) 直接返回原始分值，不做单位归一化。
- [hybrid_workflow.py:_route_after_edit](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L316-L336) 默认 `quality_threshold=0.8`，实际比较是 `score < threshold * 100`。

这隐含“score 必须是 0-100”的假设，但函数并未保证这一点。

### 4) LangGraph 图片/CMS 节点仍有占位或简化痕迹

已确认：
- [langgraph_workflow.py:_image_node](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L428-L443) 明确写着“当前为简化占位实现”
- [langgraph_workflow.py:_cms_node](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L445-L492) 注释仍说明“当前为简化占位实现”

虽然 CMS 节点已实际调用 `CMSAgent`，但文档、行为和 hybrid 并未完全对齐；图片节点仍只是阶段推进。

### 5) 错误结构与日志结构都比较粗糙

已确认：
- `langgraph_workflow.py` 多处 `state["error"] = str(e)`，例如 [langgraph_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L422-L425)
- `hybrid_workflow.py` 多处 `state["error"] = str(e)`，例如 [hybrid_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L311-L313)
- `crewai_workflow.py` 中有较多 `print()` 输出，已确认 17 处，例如初始化和流水线起止阶段

这会丢失阶段、错误类型、上下文摘要，不利于调度和生产日志采集。

### 6) 现有 tests 已覆盖部分 workflow，但仍有“字符串测试”

当前测试中与 workflow 直接相关的项包括：
- `tests/test_crawler_workflow_decision_fallback.py`
- `tests/test_crawler_workflow_dry_run_no_llm.py`
- `tests/test_crawler_workflow_publish_payload_contract.py`
- `tests/test_workflow_edit_result_field_alignment.py`
- `tests/test_workflow_seo_result_presence.py`

其中后两个已是近期补成的行为测试；crawler 相关仍缺少“单独 import CrawlerProcessorAgent/crawler_workflow 不触发循环导入”的稳定导入用例，以及对质量阈值和同步适配层的行为覆盖。

## Proposed Changes

### A) 先修最明确真实缺陷：打断 crawler 循环导入

#### 目标文件
- `agents/crawler_processor_agent/crawler_processor_agent.py`
- `agents/crawler_processor_agent/__init__.py`
- 视实际需要，`workflows/crawler_workflow.py` 只做最小辅助调整

#### 实施方式
- 将 [crawler_processor_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/crawler_processor_agent.py#L3-L3) 的 `from workflows.crawler_workflow import run_crawler_workflow` 改为**延迟导入**，放进 `execute()` 方法内部。
- 同时把 `agents/crawler_processor_agent/__init__.py` 从“顶层导入主类”改成更安全的导出方式，避免只为了 import `tools.*` 就触发整个 agent 主类初始化。

#### 决策
- 优先采用“延迟导入 + 精简 `__init__.py`”双保险，而不是只改其中一个点。

### B) 建立统一的同步适配层，替代 workflow 节点里的裸 `asyncio.run()`

#### 目标文件
- `workflows/hybrid_workflow.py`
- `workflows/langgraph_workflow.py`

#### 实施方式
- 在两个 workflow 文件中引入统一 helper，例如：
  - `_run_async_sync(coro, *, stage: str)` 或相近命名
- 该 helper 的行为固定为：
  - 若当前**没有运行中的 event loop**：安全地同步执行 coroutine
  - 若当前**已经存在 event loop**：不直接 `asyncio.run()`，而是抛出结构化错误（说明该同步入口不可在 running loop 内调用），并提供后续 async 入口预留点

#### 为什么采用同步适配层
- 符合用户确认的偏好
- 改动范围小于“全链路 async 化”
- 能先消除运行时爆炸点，同时为后续 async 入口保留结构空间

#### 范围
- 替换 `langgraph_workflow.py` 的 3 个 `asyncio.run()`
- 替换 `hybrid_workflow.py` 的 3 个 `asyncio.run()`

### C) 统一 Hybrid 内部评分协议为 0-100

#### 目标文件
- `workflows/hybrid_workflow.py`
- 相关测试文件

#### 实施方式
- 修改 `_extract_quality_score()`：
  - 若输入在 `0-1` 范围，自动转换成 `0-100`
  - 若输入已在 `0-100` 范围，保持原值
  - 非法值返回 `None`
- 修改 `_route_after_edit()`：
  - `quality_threshold` 内部也统一成 `0-100`
  - 为兼容现有调用，若传入阈值 `<=1`，视为旧语义并自动转换成 `threshold * 100`

#### 结果
- 内部比较逻辑统一
- 旧调用方不用立即改动
- 行为可测试、可解释

### D) 让 LangGraph 的图片/CMS 节点尽量对齐 hybrid

#### 目标文件
- `workflows/langgraph_workflow.py`

#### 图片节点处理方式
- 不再只做“阶段推进”
- 复用 hybrid 中已经存在的图片生成/填充模式：
  - 继续允许 plan-only / generate 两种行为
  - 至少保证输出结构与 hybrid 一致：`featured_image_url / featured_alt / featured_prompt / inline_images / license`

#### CMS 节点处理方式
- 保留 `CMSAgent` 真调用
- 将注释与行为改成“接近真实工作流”，不再标注“占位”
- 与 hybrid 对齐：
  - 文章字段映射
  - 页面信息拼装
  - images 结构传递

### E) 引入结构化错误对象，而不是只存 `str(e)`

#### 目标文件
- `workflows/hybrid_workflow.py`
- `workflows/langgraph_workflow.py`
- `workflows/crawler_workflow.py`

#### 错误结构
- 统一为：

```json
{
  "stage": "...",
  "type": "...",
  "message": "...",
  "input_id": "...",
  "trace_id": "..."
}
```

#### 实施方式
- 增加 helper，例如 `_workflow_error(stage, exc, input_id=None, trace_id=None) -> Dict[str, Any]`
- `state["error"]` 存结构化 dict，而不是字符串
- `message` 保持用户可读
- `type` 使用异常类名
- `input_id`：
  - content workflow 可优先取 `topic.id / topic.title`
  - crawler workflow 可优先取 `current_item.id`
- `trace_id`：
  - 若 state 中无现成字段，则在入口阶段生成一个时间戳/短 UUID

#### 日志策略
- traceback 不直接塞进 state，但用 `logging.exception()` 写入日志

### F) 把 CrewAI workflow 的 `print` 改成结构化 logging

#### 目标文件
- `workflows/crewai_workflow.py`

#### 实施方式
- 新增模块级 logger
- 将 `print()` 替换为 `logger.info()/warning()/error()`
- 日志内容至少包含：
  - workflow 名称
  - 当前 stage
  - topic 或 title 摘要
  - run_id（若入口已有）

#### 范围
- 初始化配置加载
- agent 创建
- 流水线开始/结束
- 结果输出与失败日志

### G) 增强行为测试，避免再被测试顺序掩盖

#### 新增测试建议
- `tests/test_crawler_workflow_import_stability.py`
  - 单独 import `agents.crawler_processor_agent`
  - 单独 import `workflows.crawler_workflow`
  - 断言二者可在任意顺序下导入，不抛循环导入异常

- `tests/test_hybrid_workflow_quality_score_normalization.py`
  - 覆盖 `0.8` 与 `80` 两种 edit 分数输入
  - 断言 `_route_after_edit()` 一致走向

- `tests/test_workflow_sync_adapter.py`
  - mock async agent 执行，验证同步适配层在普通同步上下文下可运行
  - 对 running loop 场景至少验证会抛出预期的结构化错误/明确异常

- `tests/test_langgraph_image_cms_flow.py`
  - mock 图片与 CMS agent
  - 断言 `state["image_result"]` / `state["cms_result"]` 结构真实产生并与 hybrid 对齐

#### 修改现有测试
- 保留现有 crawler 行为测试
- 继续使用已存在的行为测试风格，不再回到源码字符串断言

## Assumptions & Decisions

- 不做“全链路 async 化”，而是做**同步适配层**，这是本次范围内最稳妥、最少破坏的方案。
- `state["error"]` 结构化后，所有依赖“只判断 truthy/falsy”的现有逻辑仍可兼容；若有直接字符串假设的地方，将一并修正。
- `langgraph_workflow.py` 会被提升到“接近可用参考实现”，而不是继续保留明显的 demo/placeholder 定位。
- Hybrid 内部评分统一使用 `0-100`，同时兼容外部旧输入。
- 不在本次计划内扩展调度器功能本身，只修复会直接影响 workflow 调用安全性与可观测性的部分。

## Verification Steps

### 1) 语法检查

```powershell
.\venv\Scripts\python.exe -m compileall -q workflows agents\crawler_processor_agent tests
```

### 2) 全量测试

```powershell
.\venv\Scripts\python.exe -m pytest -q tests
```

### 3) 重点定向验证

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_crawler_workflow_import_stability.py
.\venv\Scripts\python.exe -m pytest -q tests/test_crawler_workflow_decision_fallback.py tests/test_crawler_workflow_dry_run_no_llm.py tests/test_crawler_workflow_publish_payload_contract.py
.\venv\Scripts\python.exe -m pytest -q tests/test_hybrid_workflow_quality_score_normalization.py tests/test_workflow_sync_adapter.py tests/test_langgraph_image_cms_flow.py
```

### 4) 运行时验收标准

- 单独运行 crawler 相关测试不再触发循环导入
- workflow 在同步上下文可正常调用，不再在内部裸用 `asyncio.run()`
- 若在 running event loop 内误用同步入口，报错信息明确且可定位
- Hybrid 对 `0-1` 和 `0-100` 两种质量分数输入路由一致
- LangGraph 图片/CMS 节点能真实写入 `image_result/cms_result`
- `crewai_workflow.py` 不再依赖 `print()` 作为主要日志输出
- `state["error"]` 为结构化对象，包含至少 `stage/type/message`

