# ResearchAgent -> WriterAgent research_brief 落库与任务衔接计划

## Summary

- 目标：完善 `40-80` 分 `rewrite_candidate` 链路中 `ResearchAgent -> WriterAgent` 的数据库持久化与任务衔接，让 `research_brief` 先落库，再由 `WriterAgent` 基于数据库重新读取，而不是依赖上一个函数的内存传参。
- 成功标准：
  - `ResearchAgent` 的 rewrite 任务把 `research_brief` 持久化到 `tasks.output_data`。
  - `ResearchAgent` 任务完成后自动创建 `WriterAgent` rewrite 任务。
  - `WriterAgent` 数据库模式不再要求直接传入 `research_brief/materials`，而是通过 `research_task_id` 从 `tasks.output_data` 重读。
  - 若缺少 `research_brief`，`WriterAgent` 任务被更新为 `writing_blocked`，并写入 `missing_research_brief` 错误原因。
  - `HybridWorkflow` 现有内存式 `Research -> Writer` 调用保持兼容，不被破坏。
- 短期实现策略：
  - 复用现有 `tasks.output_data`，暂不新增 `research_briefs` 表。
  - 默认以 `research_task_id` 作为 Writer 的主回读键。
  - 需要时可在 `input_data` 预留 `research_brief_id` 字段，但本轮实际查询仍走 `research_task_id`。

## Current State Analysis

- `scripts/init_db.py` 中的 `tasks` 表已经具备本轮短期方案所需字段：
  - `id`
  - `agent_name`
  - `task_type`
  - `input_data`
  - `output_data`
  - `status`
  - `error_message`
  - `started_at`
  - `completed_at`
  因此不需要先新增 `research_briefs` 表或改 DB schema。
- `workflows/topic_candidate_workflow.py` 当前会为 `rewrite_candidate` 创建 `ResearchAgent` 任务，但：
  - `task_type` 仍是通用的 `research`
  - 不会在任务完成后写 `output_data`
  - 不会自动创建 `WriterAgent` 任务
- `agents/research_agent/research_agent.py` 当前已经能在 rewrite 场景生成规则版 `research_brief`，但这是普通函数返回值：
  - 没有与 `tasks.output_data` 的持久化绑定
  - 没有“Research 完成 -> 创建 Writer task”的数据库衔接逻辑
- `agents/writer_agent/writer_agent.py` 当前只支持内存式输入：
  - `topic`
  - `outline`
  - `materials`
  - `brand_config`
  它不会读取 `tasks` 表，也不认识 `research_task_id` / `research_brief_id`。
- `workflows/hybrid_workflow.py` 当前 `_research_node()` 与 `_write_node()` 仍是纯内存链路：
  - `_research_node()` 把 `ResearchAgent.execute(...)` 的返回值放到 `state["research_result"]`
  - `_write_node()` 再把 `research_result` 作为 `materials` 直接传给 `WriterAgent`
  这是需要保留兼容的现有路径。
- 仓库检索后，没有发现现成的“task runner / task transition”实现：
  - 没有谁在更新 `tasks.output_data`
  - 没有谁在根据某个 completed `ResearchAgent` task 派生 `WriterAgent` task
  - 也没有 `writing_blocked` / `missing_research_brief` 的现有状态约定
- 现有相关测试覆盖：
  - `tests/test_topic_candidate_integration.py` 只覆盖 Topic 通过后是否创建 `ResearchAgent` 任务及其输入载荷
  - `tests/test_research_agent_contract.py` 已覆盖 rewrite 场景会返回 `research_brief`
  - `tests/test_hybrid_research_node_contract.py` 与 `tests/test_hybrid_workflow_writer_agent_alignment.py` 保护的是内存式兼容路径
  - 目前没有测试覆盖 DB 模式下 `Research -> Writer` 的落库与重读

## Assumptions & Decisions

- 本轮只实现 `rewrite_candidate/full_rewrite_flow` 的 `ResearchAgent -> WriterAgent` 落库与任务衔接，不扩展到 `Editor/SEO/Image/CMS` 后续阶段。
- 本轮不修改 `Crawler` 的三层初筛规则，不修改 `TopicAgent` 的筛选逻辑。
- 保持 `HybridWorkflow` 内存链路不变；数据库模式作为新增能力，而非替换现有实现。
- 由于短期明确复用 `tasks.output_data`，本轮默认采用：
  - `research_task_id` 作为 Writer 任务的主回读键
  - `research_brief_id` 仅作为未来拆表时的可选预留字段，不作为本轮主查询条件
- 对任务状态与错误语义采用以下短期约定：
  - `ResearchAgent` 成功：`status = completed`
  - `ResearchAgent` 失败：`status = failed`
  - `WriterAgent` 待读：`status = pending`
  - `WriterAgent` 缺少 brief：`status = writing_blocked`
  - 阻断原因：`error_message = missing_research_brief`
- Writer 阻断采用“先建后阻断”：
  - `ResearchAgent` 完成后仍创建 `WriterAgent` 任务
  - `WriterAgent` 执行时自行回读 `research_task_id`
  - 若读不到 `output_data.research_brief`，则把自身任务更新为 `writing_blocked / missing_research_brief`
- 任务类型短期收敛为更明确的数据库契约：
  - `ResearchAgent.task_type = research_for_rewrite`
  - `WriterAgent.task_type = rewrite_from_research`

## Proposed Changes

### 1. 强化 Topic 阶段创建的 Research task 契约

**文件**
- `workflows/topic_candidate_workflow.py`
- `tests/test_topic_candidate_integration.py`

**修改内容**
- 将 `rewrite_candidate` 对应的 `ResearchAgent` 初始任务 `task_type` 从通用 `research` 调整为 `research_for_rewrite`。
- 保持现有 `input_data` 中的 rewrite research 输入字段不变，包括：
  - `workflow_route`
  - `route_tier`
  - `rewrite_required`
  - `publish_candidate`
  - `topic_id`
  - `candidate_id`
  - `title`
  - `primary_keyword`
  - `secondary_keywords`
  - `target_keywords`
  - `search_intent`
  - `content_type`
  - `content_angle`
  - `source_title`
  - `source_summary`
  - `source_content`
  - `source_url`
  - `material_score`
  - `routing_payload`
- 更新 Topic 集成测试，使其断言新建的 Research task 是：
  - `agent_name = ResearchAgent`
  - `task_type = research_for_rewrite`
  - `status = pending`

**原因**
- 本轮后续要依赖任务类型来识别“这个 task 完成后应派生 Writer task”，使用更明确的 `task_type` 可减少歧义。

### 2. 为数据库模式新增最小的 rewrite task 运行/衔接层

**文件**
- 新增 `workflows/rewrite_task_workflow.py`

**修改内容**
- 在 `workflows/` 下新增一个面向数据库模式的最小执行/衔接模块，用于处理：
  - 读取待执行的 `ResearchAgent` rewrite task
  - 标记 `started_at/status=running`
  - 调用 `ResearchAgent.execute(...)`
  - 将 `research_brief` 与兼容结构写入 `tasks.output_data`
  - 将 `ResearchAgent` task 更新为 `completed/completed_at`
  - 基于成功的 Research task 创建一个 `WriterAgent` task
- 同一模块中新增 Writer 任务执行 helper，用于：
  - 读取待执行的 `WriterAgent` rewrite task
  - 从其 `input_data.research_task_id` 查找 upstream Research task
  - 读取 `tasks.output_data.research_brief`
  - 找不到时将当前 Writer task 更新为 `writing_blocked/error_message=missing_research_brief`
  - 找到时再把 research 输出映射为 `WriterAgent.execute(...)` 所需的 `outline/materials`
- 该模块只面向 `rewrite_candidate/full_rewrite_flow`，不触碰 `HybridWorkflow`。

**原因**
- 仓库里目前没有任何 `tasks` 执行与状态迁移代码；若不新增最小运行层，就无法真正实现“Research 落库 + Writer 读库”的数据库模式。

### 3. 统一 Research task 的 output_data 持久化契约

**文件**
- `agents/research_agent/research_agent.py`
- 新增或扩展 `tests/test_research_agent_contract.py`

**修改内容**
- 在 `ResearchAgent` 现有 rewrite 规则输出基础上，明确用于落库的 `output_data` 契约，建议至少包含：
  - `research_brief`
  - `outline`
  - `sources`
  - `citations`
  - `background`
  - `statistics`
  - `cases`
  - `quotes`
  - `warnings`
  - `generated_at`
  - `data_confidence`
- 若需要，补一个专门的 normalize/helper，保证写入 DB 的结构：
  - JSON 可序列化
  - 字段稳定
  - `research_brief` 一定放在顶层 `output_data.research_brief`
- 扩展 Research 契约测试，断言 rewrite research 结果满足“可直接落到 `tasks.output_data`”的稳定形状。

**原因**
- `WriterAgent` 后续要通过 `research_task_id` 读库，因此 `ResearchAgent` 输出不能只是“逻辑上有 brief”，还必须有稳定的顶层持久化位置。

### 4. 定义 Writer task 的数据库输入契约与阻断语义

**文件**
- 新增 `workflows/rewrite_task_workflow.py`
- `agents/writer_agent/writer_agent.py`
- 新增或扩展 `tests/test_hybrid_workflow_writer_agent_alignment.py`
- 新增数据库模式 Writer 测试文件，例如 `tests/test_rewrite_task_persistence.py`

**修改内容**
- `WriterAgent` 数据库模式任务建议采用如下 `input_data` 形状：
  - `workflow_route = full_rewrite_flow`
  - `route_tier = rewrite_candidate`
  - `topic_id`
  - `candidate_id`
  - `title`
  - `primary_keyword`
  - `secondary_keywords`
  - `target_keywords`
  - `search_intent`
  - `content_type`
  - `content_angle`
  - `research_task_id`
  - 可选预留：`research_brief_id = null`
- `WriterAgent` 保持现有公开签名兼容：
  - `topic`
  - `outline`
  - `materials`
  - `brand_config`
- 数据库模式的“读库”与“阻断更新”优先放在新的 `rewrite_task_workflow.py` 中处理，而不是把 DB 读写强耦合到 `WriterAgent.execute(...)` 里。
- 只有在测试暴露出必须由 `WriterAgent` 自己识别某些 DB 模式输入时，才在 `writer_agent.py` 中补最小兼容逻辑；优先不把 DB 访问职责塞进 Agent。
- 为 Writer 阻断新增明确断言：
  - upstream Research task 不存在
  - `output_data` 为空
  - `output_data` 缺少 `research_brief`
  以上任一情况，当前 Writer task 都更新为：
  - `status = writing_blocked`
  - `error_message = missing_research_brief`

**原因**
- 用户明确要求“Writer 从数据库读取 brief，而不是依赖内存传参”；同时又要求不破坏 `HybridWorkflow`，因此最合适的切点是新增数据库模式运行层，而非直接改坏 `WriterAgent` 的现有函数接口。

### 5. 定义 Research 完成后派生 Writer task 的规则

**文件**
- 新增 `workflows/rewrite_task_workflow.py`
- 新增数据库模式测试文件，例如 `tests/test_rewrite_task_persistence.py`

**修改内容**
- 仅当 `ResearchAgent` task 满足以下条件时，创建 downstream Writer task：
  - `agent_name = ResearchAgent`
  - `task_type = research_for_rewrite`
  - `status = completed`
  - `output_data.research_brief` 存在
- 新建 `WriterAgent` task 时：
  - `agent_name = WriterAgent`
  - `task_type = rewrite_from_research`
  - `status = pending`
  - `input_data` 写入 topic 元数据 + `research_task_id`
- 防止重复派生：
  - 以 `research_task_id` + `WriterAgent/rewrite_from_research` 查询是否已存在下游任务
  - 已存在则不重复创建
- 若 `ResearchAgent` 完成但 brief 缺失：
  - 不直接在此处创建失败 Writer 任务也可
  - 但按本轮决策采用“先建后阻断”，因此仍创建 Writer task，让 Writer 运行阶段统一收敛为 `writing_blocked`

**原因**
- 这一步是本次链路的核心交接点：没有显式派生规则，就无法把“Research 已完成”转成“Writer 待读且可追溯到 upstream task”。

### 6. 保持 HybridWorkflow 内存兼容，不让数据库模式破坏现有主流程

**文件**
- `workflows/hybrid_workflow.py`
- `tests/test_hybrid_research_node_contract.py`
- `tests/test_hybrid_workflow_writer_agent_alignment.py`

**修改内容**
- 优先不改 `HybridWorkflow._research_node()` / `_write_node()` 的主逻辑。
- 如果为复用 normalize/helper 必须轻微调整，也只允许：
  - 复用 `ResearchAgent` 的规范化输出
  - 不引入任何 DB 依赖
  - 继续让 `_write_node()` 直接消费 `state["research_result"]`
- 用现有两类 Hybrid 测试继续锁定：
  - `research_result` 仍是 dict
  - `outline/sources/citations` 仍能被 `WriterAgent` 使用

**原因**
- 用户已明确“不要破坏现有 HybridWorkflow 的内存式 Research -> Writer 调用”，因此数据库模式必须是增量能力，而不是重构主流程。

## Verification Steps

- 数据库契约测试：
  - `tests/test_topic_candidate_integration.py`
  - 新增 `tests/test_rewrite_task_persistence.py`
- Research 契约测试：
  - `tests/test_research_agent_contract.py`
- Hybrid 兼容回归：
  - `tests/test_hybrid_research_node_contract.py`
  - `tests/test_hybrid_workflow_writer_agent_alignment.py`
- Crawler/Topic 回归保护：
  - `tests/test_crawler_workflow_score_routing.py`
- 关键验收口径：
  - Topic 通过后创建的任务为 `ResearchAgent/research_for_rewrite`
  - Research 执行完成后，`tasks.output_data.research_brief` 已持久化
  - Writer task 的 `input_data` 带 `research_task_id`
  - Writer 执行时从 DB 成功读取 brief，而不是依赖直接传入的 `materials`
  - 缺失 brief 时 Writer task 更新为 `writing_blocked/missing_research_brief`
  - `HybridWorkflow` 的内存链路仍通过现有测试

## Out of Scope

- 本轮不新增独立的 `research_briefs` 表。
- 本轮不把 `Editor/SEO/Image/CMS` 的产物也迁移到统一 task 持久化模型。
- 本轮不改 `Crawler` 的 40/80 分规则和 Topic 筛选策略。
- 本轮不把所有 Agent 都改造成直接访问数据库的模式。
