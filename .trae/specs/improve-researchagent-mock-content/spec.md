# ResearchAgent Mock 内容质量优化 Spec

## Why
当前 ResearchAgent 的 mock 输出虽然契约稳定，但存在章节标题机械拼接、引用结构不统一的问题，影响 WriterAgent 的稳定消费与可读性。

## What Changes
- 优化 mock outline 章节生成：从 title/primary_keyword 提取自然主题词，生成语义自然的章节标题与非空要点列表
- 统一 citations 的 item schema：每条 citation 输出固定字段，不再混入 sources 原始结构
- 补充测试断言，确保标题不出现重复硬拼/不自然片段，citations 结构统一

## Impact
- Affected specs: ResearchAgent mock 输出质量、HybridWorkflow research 节点下游可消费性、WriterAgent 材料输入稳定性
- Affected code:
  - [research_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/research_agent/research_agent.py)
  - [test_research_agent_contract.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/tests/test_research_agent_contract.py)

## ADDED Requirements
### Requirement: Mock Outline 章节自然化
ResearchAgent 在 `mode="mock"` 时 SHALL 生成语义自然、可直接用于写作的 `outline.sections`。

#### Scenario: EMBA 报考条件（自然章节）
- **WHEN** `topic.title="2026年EMBA报考条件详解：适合人群、申请流程与准备建议"` 且 `primary_keyword="EMBA报考条件"`
- **THEN** `outline.sections` 至少包含 3 个 section
- **AND** 每个 section MUST 包含 `title`、`key_points`、`notes`
- **AND** `key_points` MUST 是非空 list
- **AND** 章节标题 MUST NOT 出现机械拼接重复（例如 `"EMBA报考条件报考条件"`）
- **AND** 章节标题 MUST NOT 出现不自然片段（例如 `"读EMBA报考条件"`）

### Requirement: Citations 结构统一
ResearchAgent 输出的 `citations` SHALL 始终为 list，且每一项必须是相同结构的 dict。

#### Scenario: Mock 引用输出
- **WHEN** ResearchAgent 在 mock 模式生成引用
- **THEN** `citations` 中每项必须包含字段：
  - `title`（str）
  - `url`（str，可为空）
  - `source`（str，固定为 `"mock_source"`）
  - `authority`（str，固定为 `"low"`）
  - `citation`（str）
  - `note`（str，固定为 `"mock_source"`）
- **AND** `citations` MUST NOT 混入 `sources` 的原始结构项

## MODIFIED Requirements
### Requirement: ResearchAgent mock 输出可消费性
系统 SHALL 保持现有 ResearchAgent.execute() 输出契约不变（顶层字段与基本类型），同时提升内容质量并保持 WriterAgent 可直接消费：
- `json.dumps(research_result, ensure_ascii=False)` 不报错
- `outline.sections` 存在且每项具备写作最小信息量（title + 非空要点）
- `citations` 结构一致，便于 WriterAgent 稳定遍历提取

## REMOVED Requirements
无

