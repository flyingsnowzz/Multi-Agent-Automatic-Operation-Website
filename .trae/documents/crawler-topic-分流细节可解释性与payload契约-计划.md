## 总结

在不修改 agents/topic_agent/、不把 TopicAgent 接入 crawler、且不修改 hybrid 流程的前提下，完善 crawler 的 topic 分流细节：为每条爬虫文章输出可追踪的评分来源（score_source）、可解释原因（reason/warnings）、稳定的 payload 契约（publish/rewrite/discard），并保证数据库状态字段（status_to_update）与下游对接字段（next_agent/next_payload）一致可调试；同时补齐/加固测试与文档，确保所有测试通过。

## 现状分析（基于仓库实况）

### 1) 输出结构现状

- `run_crawler_workflow()` 输出 `out["items"]` 来自 `_record_node` 写入的 `state["processed"]`：见 [crawler_workflow.py:L918-L1005](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L918-L1005) 与 [_record_node](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L846-L888)。
- 单条 item 已包含 `record_id/title/decision/reason/score_source/status_to_update/next_agent/next_payload/dedup/evaluation/dry_run`，但不包含你提出的 `source_url/score/quality_score` 等“便于调试的顶层字段”。

### 2) score_source / 非法 score 处理现状

- item.score 合法性已按 0<=score<=100 严格校验（非 clamp）：`_parse_valid_score()` 见 [crawler_workflow.py:L184-L191](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L184-L191)。
- `_evaluate_node` 内已实现：
  - 合法 item.score → 覆盖 quality_score 且 `score_source="item.score"`
  - 缺失/非法 item.score → `score_source="content_evaluator"`，并对“存在但非法”的 score 写入 `warnings += ["ignored_invalid_item_score"]`：见 [crawler_workflow.py:L678-L728](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L678-L728)。
- ContentEvaluator 评分失败时会强制 discard，并写 `reason="scoring_failed"`：同上。

### 3) reason 现状缺口

- 目前只有 `scoring_failed` 由 `_evaluate_node` 明确写入 `state["reason"]`。
- 其他硬拦截（missing required fields、duplicate、copyright risk）以及 90/40 分流原因尚未系统化输出为 reason：
  - missing required fields：见 [crawler_workflow.py:L595-L636](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L595-L636)
  - 规则分流：见 [_decide](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L300-L388)

### 4) payload 契约现状

- publish payload 已符合你给出的 CMSAgent 兼容结构：见 [_build_publish_payload](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L390-L423) 与 CMSAgent 抽取逻辑 [cms_agent.py:L55-L103](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L55-L103)。
- rewrite payload 仍是基础结构（缺少 rewrite_goal/avoid 等字段）：见 [_build_rewrite_payload](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L425-L447)。

### 5) 文档现状（Prompt/SKILL）滞后

- [SKILL.md](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/SKILL.md) 的输出描述仍是 `processing_result/content_to_publish/...`，与实际 workflow 输出 items 契约不一致。
- [prompt.md](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/prompt.md#L170-L195) 的“决策 JSON 样例”字段也与真实 `_decide_with_crewai` 要求不一致（真实要求见 [crawler_workflow.py:L494-L512](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L494-L512)）。

## 方案设计（决策收敛）

### A. workflows/crawler_workflow.py：补齐 items 顶层字段 + reason 体系化 + rewrite payload 契约增强

#### A1) 统一 items 顶层可调试字段

在 `_record_node` 里对每条 processed item 增补（不移除既有字段，保持向后兼容）：
- `source_url`: `item.get("source_url")`
- `score`: 原始 `item.get("score")`（保留原始值，便于排查上游脏数据）
- `quality_score`: `(state.get("eval_result") or {}).get("quality_score")`（分流使用的最终质量分）
- `score_source`: 已有（从 eval_result）
- `reason`: 已有（从 state）

说明：`evaluation` 保持原字典输出，用于详细审计；顶层字段用于“快速定位问题”。

#### A2) 体系化 reason：输出可解释且具备优先级

新增一个纯函数（例如 `_compute_reason(...)` 或 `_finalize_decision_and_reason(state)`），在每条 item 的处理链路中保证 reason 必填且遵循“硬拦截优先”：
- 关键约束：reason 必须在最终 decision 确定之后再计算或修正（finalize），因为 LLM 决策、mark_duplicate 覆写以及硬拦截都可能改变 decision，不允许在中途提前写死 reason。

reason 的优先级建议如下（final decision 上下文）：
1. scoring_failed → `reason="scoring_failed"`（已在 `_evaluate_node` 做）
2. missing required fields → `reason="missing_required_fields"`（在 `_pick_next_item_node` 触发 discard 时写入）
3. duplicate → `reason="duplicate_discard"`（在 dedup/decide 覆写 decision 或最终 decision 为 discard 且 is_duplicate 时写入）
4. copyright risk → `reason="copyright_risk"`（当 `eval_result.has_copyright_risk=True` 导致 discard 时写入）
5. 分值分流原因（仅在非硬拦截时写入）：
   - publish：`score_gte_90_publish`
   - rewrite：`score_between_40_and_89_rewrite`
   - discard：`score_lt_40_discard`

对于 “invalid score ignored”：
- 不强制占用 reason（避免覆盖分流原因），统一在 `evaluation.warnings` 里写 `invalid_score_ignored`（将现有 `ignored_invalid_item_score` 统一为你定义的枚举：`invalid_score_ignored`），满足“reason 或 warnings 中记录 invalid_score_ignored”的要求。
- 补充约束：如果 item 中显式传入 `score: None`，也视为非法/无效外部评分，必须回退使用 content_evaluator 的 quality_score，并建议写入 `invalid_score_ignored`；如果 item 完全没有 score 字段，则可以不写 `invalid_score_ignored`。

#### A3) rewrite payload 契约增强（不接入 hybrid，不引入 TopicAgent）

在 `_build_rewrite_payload` 中补齐你要求的字段（默认值可配置化，先给安全默认）：
- `rewrite_goal`: 固定 `"提升到90分以上"`
- `must_keep`: 默认 `[]`，若 LLM 决策输出 must_keep 则覆盖
- `avoid`: 默认 `["照搬原文", "未经核实的数据"]`
- 保留现有 `original_title/original_content/source_url/target_keywords/rewrite_instructions/meta`

说明：当前 writer_agent 的 execute 接口不是直接吃该 payload；这里的目标是“契约可对接/可落库/可调试”，并不把 rewrite 接入 hybrid。

#### A4) decision/status_to_update 逻辑保持 90/40，且支持 reason 注入

对 [_decide](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L300-L388) 做最小增量改动：
- 规则分流不改阈值、不改 decision_rules 机制；仅在输出 decision dict 中补上 `reason`（或在 _decide_node 汇总完毕后统一补 reason）。
- 在 `mark_duplicate` 覆写 decision 的分支也要写 reason=duplicate_discard（当前只有 decision/status）。

### B. agents/crawler_processor_agent/tools/content_evaluator.py：reason/copyright_risk 可解释性（按需）

原则：不拆分为独立 Agent、不引入 LLM。

按需增强点（如果需要更强可解释输出/测试断言）：
- 当 copyright risk 检测开启且命中时，在 `details` 中写明命中关键字/正则（避免仅给 True，难以 debug）。

### C. agents/crawler_processor_agent/config.yaml / prompt.md / SKILL.md：对齐“crawler topic 语境”与输出契约

#### C1) config.yaml
- 保持 0-100 分制阈值与 status 字段不变，仅补充/校对注释口径与状态建议（pending/ready_to_publish/ready_to_rewrite/discarded/processed/failed）。

#### C2) prompt.md
- 更新“决策输出 JSON 样例”，与 `_decide_with_crewai` 实际要求字段一致，并补充：
  - crawler topic 是爬虫文章 + target_keywords 指导信号，不是 TopicAgent。
  - 输出中必须包含 score_source/reason（若启用 LLM 决策则仍需遵守 reason 枚举/或由 workflow 侧修正）。

#### C3) SKILL.md
- 将“输出”章节改为与 `run_crawler_workflow` 一致的 items 列表契约（包含你定义的核心字段清单）。

### D. 测试：补齐 reason / warnings / payload 字段断言（保持现有测试文件边界）

在你指定的测试文件中补齐至少如下断言：

1) [test_crawler_workflow_score_routing.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/tests/test_crawler_workflow_score_routing.py)
- 边界分流已有，补充对 `reason` 的断言：
  - publish → score_gte_90_publish
  - rewrite → score_between_40_and_89_rewrite
  - discard → score_lt_40_discard
- 非法 score（-1/101/"abc"/None/缺失）：
  - 断言 `score_source == "content_evaluator"`
  - 断言 `evaluation.quality_score == monkeypatch 返回值`（证明未 clamp）
  - 断言 `warnings` 包含 `invalid_score_ignored`（对 -1/101/"abc"/None 生效；缺失 score 可不写 warnings）
- 评分失败（success=False）：
  - decision=discard，reason=scoring_failed，next_payload=None，evaluation.score_source=content_evaluator（已有用例可加固）

2) [test_crawler_workflow_publish_payload_contract.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/tests/test_crawler_workflow_publish_payload_contract.py)
- 增补字段级断言：
  - `payload["article"]["meta"]["crawler_record_id"]` == input id
  - `payload["article"]["meta"]["source_url"]` 保留
  - `payload["page_info"]["tags"]` 来自 target_keywords
  - `payload["page_info"]["primary_keyword"] == target_keywords[0]`（无则空字符串）

3) 新增或扩展 rewrite payload 测试（建议仍放入 score_routing 文件或新建 `test_crawler_workflow_rewrite_payload_contract.py`）
- 当 decision=rewrite 时断言 next_payload 含：
  - `rewrite_goal`
  - `avoid`
  - `target_keywords`
  - `original_content`
  - `meta.crawler_record_id`
- discard 分支断言 next_payload is None。

4) 架构边界测试
- 不在单元测试里依赖 git diff。
- 使用 grep/读取 `workflows/crawler_workflow.py` 文件内容断言没有引用：
  - `agents.topic_agent`
  - `TopicAgent`
  - `HybridWorkflow`
  - `workflows.topic_to_hybrid_adapter`

## 假设与关键决策

- 顶层 `score` 采用原始 `item.get("score")` 值（即便非法），以便排查上游数据；分流使用的最终分数固定来自 `evaluation.quality_score`（已经被 item.score 合法覆盖后写入）。
- `invalid_score_ignored` 优先落在 `evaluation.warnings`，reason 仍用于解释“为什么 publish/rewrite/discard”，避免 reason 被“评分来源原因”占用而丢失决策解释。
- 保持现有 `decision_rules` 表达式机制不做重构，reason 由 workflow 侧补齐，确保稳定可解释。
- reason 的计算/修正发生在 final decision 确定之后（包含 LLM 覆写与 mark_duplicate 覆写后的最终态），保证 reason 与最终 decision 一致。

## 验证步骤（按你要求）

1. 先跑：

```bash
python -m pytest -q tests/test_crawler_workflow_score_routing.py
```

2. 再跑：

```bash
python -m pytest -q tests/test_crawler_workflow_publish_payload_contract.py tests/test_crawler_workflow_decision_fallback.py tests/test_crawler_workflow_dry_run_no_llm.py tests/test_workflow_run_artifacts.py
```

3. 最后跑：

```bash
python -m pytest -q tests
```
