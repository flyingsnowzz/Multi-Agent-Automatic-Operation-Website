# TopicCandidateWorkflow full_rewrite_flow 先经 ResearchAgent 再到 WriterAgent 计划

## Summary

- 目标：调整 `40-80` 分 `rewrite_candidate` 素材在通过 `TopicAgent` 筛选后的后续任务创建方式，使 `workflow_route=full_rewrite_flow` 的首个任务从直接写作改为先进入 `ResearchAgent`。
- 成功标准：
  - `Crawler` 的三层初筛与 `40/80` 分阈值保持不变，仍只负责把素材分流到 `discard` 或 `pass_to_topic`。
  - `TopicAgent` 接受 `rewrite_candidate` 后，`workflows/topic_candidate_workflow.py` 写入 `tasks` 表时不再创建 `WriterAgent/rewrite`，而是创建 `ResearchAgent/research`。
  - `publish_candidate` 对应的 `light_publish_flow` 保持现状，仍创建 `CMSAgent/publish`。
  - 相关集成测试更新为断言新的首任务契约，并补充边界验证，防止后续再回退到直接写作。

## Current State Analysis

- `workflows/crawler_workflow.py` 的 `_decide()` 已明确把 `40 <= material_score < 80` 路由为 `pass_to_topic`，并在 `next_payload` 中写入 `route_tier="rewrite_candidate"`、`rewrite_required=True`；这正是用户要求保持不变的三层初筛规则。
- `tests/test_crawler_workflow_score_routing.py` 已覆盖 `39.0 / 40.0 / 79.99 / 80.0 / 81.0` 的路由行为，当前能作为“本轮不应改动 Crawler 分流”的回归护栏。
- `agents/topic_agent/config.yaml` 中 `workflow_routes.rewrite_candidate` 已配置为 `full_rewrite_flow`，`publish_candidate` 为 `light_publish_flow`；说明路由名本身已正确，不需要改配置。
- `workflows/hybrid_workflow.py` 已确认完整主链路入口是 `Research -> Write -> Edit -> SEO -> Image -> CMS`，与本次期望链路一致；偏差不在编排主流程，而在 Topic 通过后落库的首任务创建逻辑。
- `workflows/topic_candidate_workflow.py` 当前在 `route_tier == "rewrite_candidate"` 时硬编码创建：
  - `agent_name = "WriterAgent"`
  - `task_type = "rewrite"`
  这正是需要调整的唯一业务分支。
- `tests/test_topic_candidate_integration.py` 中 `test_workflow_e2e_accepted_rewrite_candidate` 当前也明确断言第二次插入 `tasks` 时是 `WriterAgent/rewrite`，因此测试必须与实现一起更新。
- 全仓库检索后，当前仅在 `workflows/topic_candidate_workflow.py` 发现 `WriterAgent/rewrite` 这一处 `rewrite_candidate` 首任务创建逻辑，没有发现其他并行实现需要同步修改。

## Assumptions & Decisions

- 保持 `route_tier="rewrite_candidate"` 与 `workflow_route="full_rewrite_flow"` 的含义不变；只调整该路由写入 `tasks` 表时的首个执行 Agent。
- 本轮不修改 `Crawler` 的阈值、分流枚举、状态更新逻辑，也不新增新的 `route_tier`。
- 本轮不改 `TopicAgent` 的筛选规则、分数逻辑、接受/拒绝状态名。
- 对 `full_rewrite_flow` 采用以下首任务契约：
  - `agent_name = "ResearchAgent"`
  - `task_type = "research"`
- 对 `light_publish_flow` 保持以下契约不变：
  - `agent_name = "CMSAgent"`
  - `task_type = "publish"`
- `tasks.input_data` 继续保留现有公共字段：
  - `topic_id`
  - `title`
  - `workflow_route`
  - `candidate_id`
  - `source_url`
- 若后续系统存在基于 `task_type` 的消费者，本轮默认采用最直观且与 Agent 职责匹配的 `research` 命名；若执行阶段发现下游调度器对 `task_type` 有额外约束，再以现有调度实现为准做最小兼容修正。

## Proposed Changes

### 1. 调整 rewrite_candidate 的首任务创建

**文件**
- `workflows/topic_candidate_workflow.py`

**修改内容**
- 保持 `topics` 表写入逻辑不变，尤其保留 `outline.candidate_metadata.workflow_route = full_rewrite_flow`。
- 仅修改 `topic_id` 成功生成后的任务创建分支：
  - 当 `route_tier == "rewrite_candidate"` 时，把 `task_agent_name` 从 `WriterAgent` 改为 `ResearchAgent`。
  - 把 `task_type` 从 `rewrite` 改为 `research`。
- `publish_candidate` 分支继续创建 `CMSAgent/publish`，不做任何行为调整。

**原因**
- 现有 `HybridWorkflow` 和工作流文档都把调研置于写作之前；Topic 通过后直接给 `WriterAgent` 会绕过研究阶段，与目标链路不一致。

### 2. 更新 Topic 工作流集成测试

**文件**
- `tests/test_topic_candidate_integration.py`

**修改内容**
- 更新 `test_workflow_e2e_accepted_rewrite_candidate` 的任务断言：
  - 第二次 `INSERT INTO tasks` 仍应存在。
  - `agent_name` 由 `WriterAgent` 改为 `ResearchAgent`。
  - `task_type` 由 `rewrite` 改为 `research`。
  - `input_data.workflow_route` 继续断言为 `full_rewrite_flow`。
- 保留并回归以下现有断言，确保本轮没有越界：
  - `topics` 插入中的 `candidate_metadata.workflow_route == "full_rewrite_flow"`
  - crawler 状态更新仍为 `topic_accepted`
  - `routing_payload.workflow_route == "full_rewrite_flow"`
  - `publish_candidate` 相关测试仍断言 `CMSAgent/publish`

**原因**
- 当前测试明确锁定了旧行为；若不更新，执行时会出现“实现正确但测试失败”的假红。

### 3. 补一个更聚焦的回归断言

**文件**
- `tests/test_topic_candidate_integration.py`

**修改内容**
- 在已有 `accepted_rewrite_candidate` 用例基础上，额外强化一条断言：`rewrite_candidate` 的首任务不应再是 `WriterAgent`。
- 实现方式优先选择在当前测试中直接补 `assertNotEqual(..., "WriterAgent")`，避免新增低价值重复测试文件。

**原因**
- 这次变更的核心风险就是未来又被改回“Topic 过后直接写作”，加一条否定式断言可以更清晰保护目标行为。

## Verification Steps

- 语法检查：
  - `python -m compileall workflows tests`
- 定向测试：
  - `python -m pytest -q tests/test_topic_candidate_integration.py`
  - `python -m pytest -q tests/test_crawler_workflow_score_routing.py`
- 如项目当前环境更依赖 `unittest`，补充执行：
  - `python -m unittest tests.test_topic_candidate_integration -v`
  - `python -m unittest tests.test_crawler_workflow_score_routing -v`

## Out of Scope

- 不修改 `workflows/crawler_workflow.py` 的 `discard / pass_to_topic` 决策逻辑。
- 不调整 `40/80` 分阈值、`rewrite_required`、`publish_candidate` 等 Crawler 初筛字段。
- 不修改 `HybridWorkflow`、`ResearchAgent`、`WriterAgent`、`EditorAgent` 等实际执行节点的内部实现。
- 不新增 Topic 通过后的完整调度器或串联执行器；本轮只改 `full_rewrite_flow` 的首个 `tasks` 创建方式与对应测试。
