# Crawler 80分以上 publish_candidate Topic 分流闭环计划

## Summary

目标是补齐 `material_score >= 80` 素材从 `Crawler` 进入 `Topic` 再进入轻处理发布链路的完整闭环，且不破坏已基本完成的 `40-80 rewrite_candidate` 路径。

目标闭环固定为：

```text
Crawler:
material_score >= 80
  -> status = pass_to_topic
  -> route_tier = publish_candidate
  -> rewrite_required = false
  -> publish_candidate = true

Topic:
读取 status=pass_to_topic 且 route_tier=publish_candidate 的候选
  -> 做选题筛选
  -> 通过：status = topic_accepted，创建 light_publish_flow 任务
  -> 拒绝：status = topic_rejected，写入 reject_reason，不创建任务
```

已确认的实现决策：

- `publish_candidate` 被 Topic 接受后，`tasks` 表写入：
  - `agent_name = "CMSAgent"`
  - `task_type = "publish"`
  - `input_data.workflow_route = "light_publish_flow"`
- 不再为 `publish_candidate` 创建 `WriterAgent` 的 `rewrite` 任务。

## Current State Analysis

### 1. Crawler 分级已基本具备

`workflows/crawler_workflow.py` 当前已经具备三层分流字段产出能力：

- `_normalize_crawler_config()` 已默认注入 `publish_candidate_threshold = 80.0`
- `_decide()` 已实现：
  - `< discard_below_score` -> `discard`
  - `40~80` -> `pass_to_topic + rewrite_candidate`
  - `>= 80` -> `pass_to_topic + publish_candidate`
- `_build_topic_payload()` 已把 `route_tier`、`rewrite_required`、`publish_candidate` 写入 `next_payload`

因此本需求中 `Crawler` 侧大概率只需要补验证，不需要大改主逻辑。

涉及位置：

- `workflows/crawler_workflow.py`

### 2. TopicAgent 已识别 publish_candidate，但未补 workflow_route

`agents/topic_agent/topic_agent.py` 中 `execute_on_candidates()` 已读取并保留：

- `route_tier`
- `rewrite_required`
- `publish_candidate`
- `material_score`

但当前仅在 `route_tier == "rewrite_candidate" and is_accepted` 时设置：

- `workflow_route = full_rewrite_flow`

对于 `publish_candidate`：

- `workflow_route` 仍为 `None`

这正是 Topic 分流闭环缺口之一。

涉及位置：

- `agents/topic_agent/topic_agent.py`

### 3. topic_candidate_workflow 仍无差别创建 Writer rewrite 任务

`workflows/topic_candidate_workflow.py` 当前在 Topic 接受后统一执行两步持久化：

1. 写入 `topics`
2. 无条件写入 `tasks`

其中任务固定为：

- `agent_name = "WriterAgent"`
- `task_type = "rewrite"`

即使 `publish_candidate` 也会如此处理，这与“轻处理发布链路”目标冲突。

同时当前逻辑只从配置读取：

- `workflow_routes.rewrite_candidate = full_rewrite_flow`

尚无：

- `workflow_routes.publish_candidate = light_publish_flow`

涉及位置：

- `workflows/topic_candidate_workflow.py`
- `agents/topic_agent/config.yaml`

### 4. 候选读取与状态回写基础已具备

`agents/topic_agent/tools/topic_candidate_reader.py` 已能从爬虫库读取：

- `status = pass_to_topic`
- `route_tier`
- `rewrite_required`
- `publish_candidate`
- `routing_payload`

`agents/crawler_processor_agent/tools/crawler_db_reader.py` 已支持将 `routing_payload` 回写到爬虫库。

因此不需要新增新的数据库读写工具。

### 5. 测试基线已存在，但 publish_candidate 断言仍停留在“兼容现状”

`tests/test_topic_candidate_integration.py` 里已经覆盖：

- `rewrite_candidate` 接受与拒绝
- `publish_candidate` 兼容路径

但当前 `publish_candidate` 用例断言的仍是旧行为：

- `workflow_route is None`
- 仍创建 `WriterAgent/rewrite` 任务

这些测试需要按新闭环改写，并新增更明确的轻处理任务断言。

## Proposed Changes

### A. 对齐 Topic 路由配置

文件：

- `agents/topic_agent/config.yaml`

修改内容：

- 在 `workflow_routes` 下新增：
  - `publish_candidate: "light_publish_flow"`

保留：

- `rewrite_candidate: "full_rewrite_flow"`

原因：

- 让 Topic 层和 workflow 层都从统一配置读取发布候选路由，而不是在代码里硬编码第二条分支。

实现要点：

- 不改已有 `candidate_status.accepted/rejected`
- 不引入新的状态名

### B. 补齐 TopicAgent 对 publish_candidate 的 workflow_route 赋值

文件：

- `agents/topic_agent/topic_agent.py`

修改内容：

- 在 `execute_on_candidates()` 中同时读取：
  - `rewrite_route = workflow_routes.rewrite_candidate`
  - `publish_route = workflow_routes.publish_candidate`
- 根据候选类型和接受结果设置 `workflow_route`：
  - `rewrite_candidate && is_accepted` -> `full_rewrite_flow`
  - `publish_candidate && is_accepted` -> `light_publish_flow`
  - 未通过 -> `None`

原因：

- `workflow_route` 是 Topic 结果对象、`topics.outline.candidate_metadata`、`routing_payload` 和下游任务创建之间的唯一显式路由标记，必须在 Topic 决策时就确定。

实现要点：

- 仍保留 `route_tier / rewrite_required / publish_candidate / material_score` 原样透传
- 对 rejected 项继续保留 `reject_reason`
- 不改现有筛选规则本身，只改接受后的路由产物

### C. 让 topic_candidate_workflow 按 route_tier 分支创建不同任务

文件：

- `workflows/topic_candidate_workflow.py`

修改内容：

1. 配置读取层：
   - 增加 `publish_route = cfg_routes.get("publish_candidate") or "light_publish_flow"`

2. accepted topic 持久化层：
   - `topics.outline.candidate_metadata.workflow_route` 写入 Topic 已计算出的 `workflow_route`
   - `publish_candidate` 不再保留 `None`

3. tasks 创建层改为按 `route_tier/workflow_route` 分支：
   - `rewrite_candidate`
     - `agent_name = "WriterAgent"`
     - `task_type = "rewrite"`
     - `input_data.workflow_route = "full_rewrite_flow"`
   - `publish_candidate`
     - `agent_name = "CMSAgent"`
     - `task_type = "publish"`
     - `input_data.workflow_route = "light_publish_flow"`

4. rejected 分支保持不创建任务：
   - 仅更新爬虫库为 `topic_rejected`
   - 写入 `reject_reason`

5. accepted 状态回写：
   - 继续更新爬虫库为 `topic_accepted`
   - `routing_payload.topic_accepted = True`
   - `routing_payload.workflow_route = workflow_route`
   - 对 `publish_candidate` 应写入 `light_publish_flow`

原因：

- 闭环真正断在这里：当前 workflow 统一创建 Writer rewrite 任务，与 `publish_candidate` 的轻处理目标相违背。

实现要点：

- `publish_candidate` 只切换任务归属，不改 topics 表结构
- 不新增数据库 migration，因为 `tasks` 现有字段足够表达新任务
- 如果 `workflow_route` 缺失，应按 `route_tier` 回退到对应默认路由，避免脏数据导致 accepted 后无任务

### D. 仅做必要的 Crawler 侧验证，不重写其主逻辑

文件：

- `workflows/crawler_workflow.py`
- `tests/test_crawler_workflow_*`

修改内容：

- 复核并补测试，确认 `material_score >= 80` 时：
  - `decision = pass_to_topic`
  - `status_to_update = pass_to_topic`
  - `next_payload.route_tier = publish_candidate`
  - `next_payload.rewrite_required = false`
  - `next_payload.publish_candidate = true`

原因：

- 当前代码已经接近目标，不应为本次需求重做 Crawler 逻辑，但要用测试把闭环入口锁死。

### E. 更新测试为“新闭环”而非“兼容旧行为”

文件：

- `tests/test_topic_candidate_integration.py`
- `tests/test_topic_to_hybrid_adapter.py`
- 视需要补充 `tests/test_crawler_workflow_publish_payload_contract.py` 或新增针对 80+ 路由的用例

修改内容：

1. `publish_candidate` Topic 集成测试改写：
   - accepted 时：
     - `topic["workflow_route"] == "light_publish_flow"`
     - `topics.outline.candidate_metadata.workflow_route == "light_publish_flow"`
     - 任务写入为 `CMSAgent + publish`
     - `input_data.workflow_route == "light_publish_flow"`
     - 爬虫状态更新为 `topic_accepted`
     - `routing_payload.topic_accepted == True`
     - `routing_payload.workflow_route == "light_publish_flow"`

2. `publish_candidate` rejected 测试新增：
   - 状态改为 `topic_rejected`
   - `reject_reason` 已写入
   - 不创建任何 tasks 记录

3. `rewrite_candidate` 回归测试保留：
   - 仍应走 `WriterAgent + rewrite + full_rewrite_flow`

4. `topic_to_hybrid_adapter.py` 仅在需要时补充契约测试：
   - 如果后续仍会消费 `publish_candidate` 的 `workflow_route`，则新增 `light_publish_flow` 透传断言
   - 若当前无直接消费，不必扩大改动范围

原因：

- 现有测试已经把旧错误行为“固化”为通过条件，必须同步改掉，否则实现正确也会被旧断言阻塞。

## Assumptions & Decisions

### 已锁定决策

- `publish_candidate` 被 Topic 接受后，创建的任务写入：
  - `agent_name = "CMSAgent"`
  - `task_type = "publish"`
  - `input_data.workflow_route = "light_publish_flow"`

### 计划内假设

- `light_publish_flow` 当前只作为任务路由标识，不要求本次同时打通执行器对该路由的真实消费。
- `topic_candidate_workflow.py` 仍是本次闭环的唯一任务创建入口。
- `status = pass_to_topic` 的候选读取逻辑无需新增额外过滤条件，仅依赖 `TopicAgent` 内部的 `route_tier` 决策和筛选结果。
- 不修改 `HybridWorkflow` 主流程，不把 `publish_candidate` 强行接入 `hybrid_workflow.py`。
- 不修改 `tasks` 表结构，不新增 migration。

### 明确不做

- 不改 `40-80 rewrite_candidate` 的既有通路语义
- 不新增新的中间状态
- 不在本次计划里实现 `light_publish_flow` 的实际消费者或调度器
- 不改变 Topic 筛选分数模型、业务语义规则、拒绝规则阈值

## Verification Steps

执行阶段完成后，按以下顺序验证：

1. 运行定向测试：
   - `tests.test_topic_candidate_integration`
   - `tests.test_topic_to_hybrid_adapter`
   - 相关 `tests.test_crawler_workflow_*`

2. 重点断言：
   - `material_score >= 80` 的 crawler item 产出：
     - `route_tier = publish_candidate`
     - `rewrite_required = False`
     - `publish_candidate = True`
   - `publish_candidate` 经 Topic 接受后：
     - 爬虫状态更新为 `topic_accepted`
     - `workflow_route = light_publish_flow`
     - `tasks` 插入为 `CMSAgent / publish`
   - `publish_candidate` 被 Topic 拒绝后：
     - 状态更新为 `topic_rejected`
     - `reject_reason` 已写入
     - 不创建任务
   - `rewrite_candidate` 继续保持：
     - `workflow_route = full_rewrite_flow`
     - `WriterAgent / rewrite`

3. 运行全量回归：
   - `python -m unittest discover -s tests`

4. 如有需要，做一次只读代码走查确认：
   - `TopicAgent.execute_on_candidates()` 输出对象
   - `topic_candidate_workflow.py` 中 `topics` 持久化的 `candidate_metadata`
   - `tasks.input_data.workflow_route`

