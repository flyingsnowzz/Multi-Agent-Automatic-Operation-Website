# ResearchAgent rewrite_candidate 规则版 research_brief 计划

## Summary

- 目标：为 `40-80` 分 `rewrite_candidate` 链路补齐 `ResearchAgent` 的规则驱动 `research_brief` 能力，不接入大模型、不依赖 LLM，先建立稳定的输入/输出契约。
- 成功标准：
  - `Crawler -> TopicAgent -> ResearchAgent -> WriterAgent -> EditorAgent -> SEOAgent -> ImageAgent -> CMSAgent` 中，`ResearchAgent` 能消费 `TopicAgent` 通过后的 `rewrite_candidate` 任务。
  - `ResearchAgent` 基于规则从原素材中提取关键信息，输出结构化 `research_brief`。
  - 本轮不让 `ResearchAgent` 写文章、不做选题价值判断、不发布内容。
  - `ResearchAgent` 输出采用“双轨兼容”：既有新的 `research_brief`，也保留/映射出当前 `WriterAgent` 还能直接消费的 `outline/sources/citations/...` 结构。
  - 输入契约首版必须包含原文正文，不只依赖 `source_title/source_summary/source_url`。

## Current State Analysis

- `workflows/topic_candidate_workflow.py` 当前已把 `rewrite_candidate` 的首任务改为 `ResearchAgent/research`，但写入 `tasks.input_data` 的字段只有：
  - `topic_id`
  - `title`
  - `workflow_route`
  - `candidate_id`
  - `source_url`
  这远小于本次需求中的 Research 输入契约。
- `agents/topic_agent/tools/topic_candidate_reader.py` 当前能从爬虫候选中拿到：
  - `id`
  - `material_score`
  - `route_tier`
  - `rewrite_required`
  - `publish_candidate`
  - `topic_hint`
  - `source_title`
  - `source_summary`
  - `source_url`
  - `dedup`
  - `evaluation`
  - `routing_payload`
  但没有把候选原文正文 `content` 显式透传为稳定字段，也没有把 `raw_data` 一并暴露给下游规则链路。
- `agents/crawler_processor_agent/tools/crawler_db_reader.py` 已确认底层标准化结果中本来就有 `content` 和 `raw_data`，说明源正文与原始记录是可发现的；当前缺口主要在 `TopicCandidateReader` 没把它们显式纳入候选契约。
- `agents/topic_agent/topic_agent.py` 的 `execute_on_candidates()` 当前会为 accepted topic 产出：
  - `title`
  - `target_keywords`
  - `search_intent`
  - `content_type`
  - `outline_points`
  - `candidate_id`
  - `route_tier`
  - `rewrite_required`
  - `publish_candidate`
  - `source_title`
  - `source_summary`
  - `source_url`
  - `material_score`
  但没有显式产出 `primary_keyword`、`secondary_keywords`、`content_angle`、`source_content`。
- `agents/research_agent/research_agent.py` 当前 `execute(topic=..., mode="mock")` 仍是“通用 mock 调研包”路径：
  - 会走 `DataCollector.collect(...)`
  - 输出 `background/statistics/cases/quotes/sources/citations/outline`
  - 没有 `research_brief`
  - 没有 rewrite_candidate 专用规则提取逻辑
  - 也没有“禁止外部依赖”的专用执行分支
- `workflows/hybrid_workflow.py` 当前 `_write_node()` 仍按老契约把 `research_result` 当作 `materials` 传给 `WriterAgent`，并从其中读取 `outline`。这意味着若本轮只产出全新 `research_brief` 而不做兼容映射，会立刻破坏现有 Writer 对接。
- `tests/test_research_agent_contract.py`、`tests/test_hybrid_research_node_contract.py` 当前只保护通用 `ResearchAgent` mock 输出，不覆盖 rewrite_candidate 规则简报场景。
- `tests/test_topic_candidate_integration.py` 目前只验证 `rewrite_candidate` 会创建 `ResearchAgent/research` 任务，没有验证该任务载荷是否已补齐到本次目标契约。

## Assumptions & Decisions

- 保持 `Crawler` 的 `40/80` 分阈值、`route_tier`、`rewrite_required`、`publish_candidate` 等初筛逻辑不变；本轮只补 Topic 通过后的 Research 链路。
- 不新增数据库表结构，不修改 `tasks` 表 schema；只扩充 `tasks.input_data` JSON 内容。
- 优先复用现有 `ResearchAgent.execute(...)` 入口，在内部增加 `rewrite_candidate/full_rewrite_flow` 的规则执行分支，而不是另开完全独立的第二个 Agent 接口，减少后续编排改造面。
- 对 `rewrite_candidate` 规则分支明确禁用：
  - `LLM`
  - `DataCollector`
  - 任意外部在线数据依赖
- `ResearchAgent` 的首版输出采用双轨兼容：
  - 新增 `research_brief`
  - 同时映射保留 `outline/sources/citations/background/statistics/cases/quotes/warnings/generated_at/data_confidence`
  以保证当前 `WriterAgent` 和 `HybridWorkflow` 仍能消费。
- `TopicAgent`/任务载荷侧补齐以下首版输入字段：
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
  - `source_url`
  - `source_content`
  - `material_score`
  - `evaluation`
  - `dedup`
  - `routing_payload`
- 其中字段来源约定如下：
  - `primary_keyword`：优先取 `target_keywords[0]`
  - `secondary_keywords`：取 `target_keywords[1:]`，没有则为空数组
  - `content_angle`：优先复用 `TopicAgent` 已有 `_infer_topic_angle()` 的判定结果并对外输出稳定字符串
  - `source_content`：优先取候选素材原文 `content`，为空时退化为 `source_summary`
- 首版 `research_brief` 的职责只做“素材整理与写作约束提炼”，不做“值得不值得写”的再次判断。

## Proposed Changes

### 1. 扩充候选素材读取契约，为规则提取准备正文与原始记录

**文件**
- `agents/topic_agent/tools/topic_candidate_reader.py`

**修改内容**
- 在候选结果中显式补出以下字段：
  - `source_content`
  - `raw_data`
- 保持现有 `source_title/source_summary/source_url/evaluation/dedup/routing_payload` 字段不变。
- `source_content` 的取值规则：
  - 优先取标准化后的 `item["content"]`
  - 若为空则回退到 `item["raw_data"]` 中映射后的正文
  - 再为空则回退到 `source_summary`

**原因**
- Research 规则提取要稳定产出 `facts/highlights/risk_points`，只靠标题和摘要不够；而底层读取器已能拿到正文，这里只需把契约补齐。

### 2. 让 Topic 输出并任务化时携带完整 Research 输入字段

**文件**
- `agents/topic_agent/topic_agent.py`
- `workflows/topic_candidate_workflow.py`
- `tests/test_topic_candidate_integration.py`

**修改内容**
- 在 `TopicAgent.execute_on_candidates()` 的 accepted `topic_item` 中补齐并持久化：
  - `primary_keyword`
  - `secondary_keywords`
  - `content_angle`
  - `source_content`
  - `evaluation`
  - `dedup`
  - `routing_payload`
- 保持现有 `target_keywords/search_intent/content_type/source_*` 字段不变。
- 在 `run_candidate_to_topics_workflow()` 创建 `ResearchAgent/research` 任务时，把上述字段一并写入 `tasks.input_data`，形成完整的 rewrite research 输入契约。
- 更新 `tests/test_topic_candidate_integration.py`，不再只断言 `agent_name/task_type`，还要断言 `rewrite_candidate` 的任务载荷至少包含：
  - `workflow_route=full_rewrite_flow`
  - `route_tier=rewrite_candidate`
  - `rewrite_required=True`
  - `publish_candidate=False`
  - `topic_id`
  - `candidate_id`
  - `title`
  - `target_keywords`
  - `primary_keyword`
  - `search_intent`
  - `content_type`
  - `source_title`
  - `source_summary`
  - `source_url`
  - `source_content`

**原因**
- 现在真正缺口不是只把任务交给了 `ResearchAgent`，而是交过去的 payload 太薄，Research 根本没有足够原料生成规则版 `research_brief`。

### 3. 在 ResearchAgent 内增加 rewrite_candidate 专用规则分支

**文件**
- `agents/research_agent/research_agent.py`
- `agents/research_agent/config.yaml`

**修改内容**
- 在 `ResearchAgent` 中新增面向 `rewrite_candidate/full_rewrite_flow` 的规则 helper，建议按职责拆成小函数，例如：
  - 输入归一化
  - 关键词归一化
  - 正文清洗/截断
  - 句段切分
  - 标题/摘要/正文提取关键句
  - 风险点与缺口提示提取
  - Writer 兼容结构映射
- 对 `execute(...)` 增加规则分支判断：
  - 当输入包含 `workflow_route=full_rewrite_flow` 且 `route_tier=rewrite_candidate` 时，直接走规则版 brief 生成
  - 不调用 `DataCollector.collect(...)`
  - 不调用任何 LLM
- 在 `config.yaml` 中补充纯规则配置项，建议只放稳定阈值与裁剪参数，例如：
  - `brief.max_source_chars`
  - `brief.max_highlights`
  - `brief.max_risk_points`
  - `brief.max_outline_sections`
  - `brief.max_keywords`
  - `brief.max_facts`

**原因**
- 当前 `ResearchAgent` 的 mock 路径本质是“生成式调研包”，与本次“基于现有素材整理规则简报”的目标不同，需要单独的 deterministic 分支。

### 4. 定义首版 research_brief 输出契约，并映射到旧 Writer 可消费结构

**文件**
- `agents/research_agent/research_agent.py`
- `tests/test_research_agent_contract.py`
- `tests/test_hybrid_research_node_contract.py`

**修改内容**
- 为 rewrite research 分支定义稳定的 `research_brief` 顶层对象，建议至少包含：
  - `brief_type`
  - `workflow_route`
  - `route_tier`
  - `topic_id`
  - `candidate_id`
  - `title`
  - `primary_keyword`
  - `secondary_keywords`
  - `target_keywords`
  - `search_intent`
  - `content_type`
  - `content_angle`
  - `source_snapshot`
  - `source_highlights`
  - `key_facts`
  - `risk_points`
  - `rewrite_constraints`
  - `writer_outline`
  - `suggested_sections`
  - `warnings`
  - `generated_at`
- 同时把规则结果映射回旧结构，至少保证：
  - `outline.sections` 可供现有 `WriterAgent` 使用
  - `sources` 至少包含当前源文章 URL/标题
  - `citations` 可由源文章 URL 生成最小可回链引用项
  - `background/statistics/cases/quotes` 即使没有充分信息，也返回稳定空结构而不是缺字段
- `tests/test_research_agent_contract.py` 新增 rewrite 链路断言：
  - 输入完整任务 payload 时返回 `research_brief`
  - 不缺关键字段
  - `outline.sections` 与 `sources/citations` 仍存在
  - `warnings` 为 list
  - 输出可 `json.dumps(...)`
- `tests/test_hybrid_research_node_contract.py` 需要回归确认：
  - 现有 `HybridWorkflow._research_node()` 仍拿到 dict
  - `outline/sources/citations` 结构未被破坏

**原因**
- 用户已明确要先把 `research_brief` 做稳定；同时你又选择了“双轨兼容”，所以新旧结构必须同时成立。

### 5. 明确规则提取边界，只做素材整理，不做生成式写作

**文件**
- `agents/research_agent/research_agent.py`
- `tests/test_research_agent_contract.py`

**修改内容**
- 规则版 `ResearchAgent` 仅从 `source_title/source_summary/source_content` 和 Topic 字段中提炼：
  - 主题摘要
  - 关键句/关键点
  - 可直接复用的事实
  - 潜在缺口或需补证据的风险提示
  - 给 Writer 的改写约束
  - 建议大纲
- 明确禁止在规则版分支中：
  - 生成完整文章正文
  - 再次判断是否立项
  - 发布 CMS payload
- 测试中增加否定式保护，确保输出里不会混入 Writer/CMS 职责字段，例如不要求出现完整 article body、publish result 等。

**原因**
- 这是用户明确给出的职责边界，应该用测试锁定，避免后续把 ResearchAgent 再次做成“大而全”。

### 6. 评估并最小调整 Writer 对接点

**文件**
- `tests/test_hybrid_workflow_writer_agent_alignment.py`
- 如确有必要再改：`workflows/hybrid_workflow.py`

**修改内容**
- 优先不改 `WriterAgent.execute()` 公开签名。
- 若 `ResearchAgent` 双轨输出已保证 `outline/materials` 兼容，则 `HybridWorkflow` 只需回归测试，不主动改逻辑。
- 若测试表明 `outline` 字段命名或结构仍与 Writer 预期有偏差，则只做最小兼容适配，例如优先读取：
  - `research_result.research_brief.writer_outline`
  - 回退到 `research_result.outline`

**原因**
- 你的选择是“双轨兼容”，因此本轮不应主动放大到重构 Writer；只有在兼容映射仍不足时才做最小修补。

## Verification Steps

- 定向单测：
  - `tests/test_research_agent_contract.py`
  - `tests/test_hybrid_research_node_contract.py`
  - `tests/test_topic_candidate_integration.py`
  - `tests/test_hybrid_workflow_writer_agent_alignment.py`
- 回归保护：
  - `tests/test_crawler_workflow_score_routing.py`
  - `tests/test_topic_candidate_integration.py`
- 核验口径：
  - `rewrite_candidate` 创建的 `ResearchAgent` 任务载荷已补齐正文与 topic 元数据
  - `ResearchAgent` 在 rewrite 链路不触发 `LLM`/`DataCollector`
  - 输出同时包含 `research_brief` 与旧 Writer 可消费结构
  - `publish_candidate` 链路不受影响
  - `Crawler` 40/80 分分流规则不变

## Out of Scope

- 不接入任何大模型或在线检索增强。
- 不把 `ResearchAgent` 扩展为文章撰写器、选题裁决器或发布器。
- 不改 `Crawler` 初筛规则与阈值。
- 不在本轮引入新的数据库 schema 或新的工作流引擎。
