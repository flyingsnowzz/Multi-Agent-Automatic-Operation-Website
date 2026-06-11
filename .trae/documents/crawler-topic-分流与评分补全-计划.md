## 总结

本计划在不修改 hybrid 流程、且不触碰 agents/topic_agent/ 的前提下，修复并完善 crawler_workflow 的“爬虫文章 topic 分流”能力，重点修复 item.score 校验漏洞：禁止对 item.score 做 clamp 后直接使用，改为严格校验（仅 0<=score<=100 视为合法）并明确 score_source；非法/缺失评分必须回退使用 ContentEvaluator 的 quality_score 继续分流；同时补齐针对 -1/101/"abc"/None/缺失的单测，确保 90/40 分流边界不被破坏。

## 现状分析（基于仓库实况）

### 现有分流逻辑位置

- 分流规则核心在 [crawler_workflow.py:L297-L385](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L297-L385)
  - publish：quality_score >= auto_publish_threshold（默认 90）
  - rewrite：quality_score >= rewrite_threshold（默认 40）且未 publish
  - discard：低于 min_quality 或其他硬规则（字数/重复等）
- 评分获取在评估节点 [crawler_workflow.py:L676-L706](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L676-L706)
  - 固定调用 `evaluate_content(... use_llm=False ...)`，得到 eval_result
  - 若 item.score 存在，则用 `_score_0_100()` 归一化后覆盖 eval_result["quality_score"] 并写 score_source="item.score"
- 当前 `_score_0_100()` 的实现会将越界分数 clamp 到 [0, 100]（导致 item.score=-1 会当成 0，item.score=101 会当成 100），不符合“非法评分必须触发重新评分”的约束：见 [crawler_workflow.py:L184-L190](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L184-L190)
- ContentEvaluator 工具为内部工具（非独立 Agent），定义在 [content_evaluator.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/content_evaluator.py)，其输出契约包含 success/quality_score/relevance_score/seo_potential_score，且设计为 0-100 分制（提示词亦如此）。
- 当前 crawler_workflow 未对 `evaluate_content` 返回 `success=False` 的情况做硬性分流保护，无法满足“评分失败必须 discard 且 reason=scoring_failed”。
- 已有测试覆盖了你要求的分流边界断言（100/99/90 -> publish；89.99/89/40 -> rewrite；39.99/39/0 -> discard）：[test_crawler_workflow_score_routing.py:L27-L71](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/tests/test_crawler_workflow_score_routing.py#L27-L71)
- 但目前缺少以下测试：
  - item.score 缺失时必须补评分、并标记 score_source="content_evaluator"
  - item.score 非法（"abc"/None/<0/>100）必须忽略 item.score，不允许直接 publish
  - ContentEvaluator 评分失败必须 discard 且 reason=scoring_failed

## 目标与验收对齐

严格对齐用户验收标准：
- crawler 质量评分统一为 0-100 分制（dedup.threshold 继续是 0-1 相似度阈值）。
- 99/90 必须 publish；89/40 必须 rewrite；39/0 必须 discard（边界精确到小数）。
- 未评分/非法评分必须先由 ContentEvaluator 补/重评分，再进入分流。
- 不新增独立评分 Agent，不把 TopicAgent 接入 crawler，不修改 hybrid、不修改 agents/topic_agent/。
- target_keywords 继续用于：相关性、SEO 潜力、改写方向、CMS 标签映射（publish payload page_info.tags / primary_keyword）。

## 方案设计（决策已收敛）

### 1) workflows/crawler_workflow.py：修正 item.score 合法性判定 + 评分失败兜底 + 输出字段补齐

改动点与实现要点：

1. 修复 item.score 校验漏洞：新增（或替换为）严格校验函数 `_parse_valid_score(value) -> Optional[float]`
   - 解析为 float 失败：返回 None（例如 "abc"/None）
   - score < 0 或 score > 100：返回 None（例如 -1/101）
   - 仅当 0 <= score <= 100：返回 float(score)
   - 明确禁止 clamp 逻辑用于“合法性判断”，避免：
     - -1 被当成 0 → 错误 discard
     - 101 被当成 100 → 错误 publish
   - 明确约束：crawler_workflow.py 的 `_evaluate_node` 不得再调用 `_score_0_100(item.get("score"))` 判断 item.score 合法性，必须替换为 `_parse_valid_score`。

2. 按修复要求改造 `_evaluate_node` 的覆盖逻辑（以 crawler_workflow 为准，不引入独立评分 Agent）
   - 先调用 ContentEvaluator（现状：`evaluate_content(... use_llm=False ...)`）拿到 eval_result（quality_score 作为默认分流依据）
   - 读取 external_score = _parse_valid_score(item.get("score"))
   - 如果 external_score is not None：
     - eval_result["quality_score"] = external_score
     - eval_result["score_source"] = "item.score"
   - 否则（缺失/非法 score）：
     - eval_result["score_source"] = "content_evaluator"
     - 不允许写入/覆盖 quality_score
     - 可选：eval_result["warnings"] 追加 "ignored_invalid_item_score"

3. ContentEvaluator 评分失败硬兜底（满足 scoring_failed 要求）
   - 若 eval_result 不是 dict 或 eval_result.get("success") is False：
     - 必须先写入 state["eval_result"] = eval_result（确保 record_node 的 evaluation 字段能看到失败原因/错误信息）
     - 直接设置：
       - state["decision"]="discard"
       - state["status_to_update"]=discarded（从 cfg.crawler_db.discard_status 读取，默认 discarded）
       - state["next_agent"]=None
       - state["next_payload"]=None
       - state["reason"]="scoring_failed"
     - 同时保留 eval_result（包含 error），并强制写 score_source="content_evaluator"
   - 由于工作流节点开头会 `if state.get("decision") is not None: return state`，这样 decide_node 会被自然跳过，确保“评分失败不进入 LLM 决策/不进入分流 payload 生成”。

4. 在 `_record_node` 的 processed item 中补齐可读字段（不破坏兼容，增量字段）
   - 追加字段：
     - "score_source": (eval_result 或 {}).get("score_source")
     - "reason": state.get("reason")
   - 原有字段保持不变，避免影响现有消费者与测试。

### 2) agents/crawler_processor_agent/tools/content_evaluator.py：保持“内部工具”边界，补强失败语义（如需）

原则：不把评分抽成独立 Agent，仅在工具内部补强契约稳定性。

拟做调整（若现状无法满足测试/契约）：
- 确保 evaluate_content 无论成功失败都返回 dict，并在失败时包含：
  - success=False
  - error=str(...)
  - 可选：quality_score/relevance_score/seo_potential_score 置为 0.0（便于日志与下游序列化一致性）

说明：即使不修改该文件，也可以仅在 crawler_workflow 侧通过 success 字段做兜底；是否需要改以实际测试为准。

### 3) agents/crawler_processor_agent/config.yaml：确认 0-100 阈值与决策规则一致（少改或不改）

现状已经是 0-100：
- execution.auto_publish_threshold=90、rewrite_threshold=40
- evaluation_criteria.min_quality_score=40
- dedup.threshold=0.8（0-1 相似度）

计划动作：
- 仅在发现“旧 0-1 质量评分表述”时做清理；否则保持不动，避免无关改动。

### 4) agents/crawler_processor_agent/prompt.md 与 SKILL.md：补充 score_source/非法评分/target_keywords 的边界说明

计划动作：
- 明确 crawler 语境下的两层 topic 含义：
  - 被处理对象：爬虫文章（scraped item/article）
  - 指导信号：target_keywords（用于相关性、SEO 潜力、改写方向、CMS tags/primary_keyword）
- 明确评分来源优先级：
  - 合法 item.score（0-100）优先
  - 否则使用 content_evaluator 计算 quality_score
  - 非法 item.score 必须忽略并重评分
  - content_evaluator 失败直接 discard（reason=scoring_failed）
- 保持不引入 TopicAgent、不描述 crawler 调用 TopicAgent。

### 5) main.py：仅校对 crawler demo 调用参数命名（如需）

计划动作：
- 保持 run_crawler_ingest 使用 target_keywords 作为输入（当前已是 list[str]），如文档/示例中把它误写成 topic 或混入 TopicAgent 语境，则做最小修订。

### 6) 文档（00-方案概述.md / 01-Agent架构图.md / 03-工作流编排.md）：清晰化 crawler topic 边界与 0-100 评分口径

计划动作（最小改动、只修口径与边界）：
- 03-工作流编排.md：
  - 在“爬虫内容处理流”小节补充：分流依据是 0-100 quality_score；target_keywords 是指导信号；分流不依赖 TopicAgent。
  - 将任何“crawler 内部触发 TopicAgent”的描述改为“系统的独立选题流程可并行补充选题”，避免架构误导。
- 00-方案概述.md / 01-Agent架构图.md：
  - 确认 crawler 决策规则仍是 90-100 publish、40-89 rewrite、<40 discard（当前已符合）；补充 target_keywords 用途（tags/primary_keyword）。

### 7) 测试：在既有测试文件内补齐缺失用例（保证进入你指定的验证命令）

遵循你的“运行验证”清单（首轮会显式跑若干测试文件），因此新增测试优先放进你指定的现有文件中，避免漏跑。

计划改动：
- 扩展 [test_crawler_workflow_score_routing.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/tests/test_crawler_workflow_score_routing.py)
  1. 新增“非法/缺失 item.score 必须忽略并使用 content_evaluator”用例（覆盖 -1/101/"abc"/None/缺失）
     - monkeypatch workflows.crawler_workflow.evaluate_content 为固定返回（示例）：
       - success=True、quality_score=95、word_count 合法、其他字段补齐
     - items 构造：
       - score=-1 / 101 / "abc" / None / 不含 score 字段
     - 断言：
       - decision 应基于 95 分 → publish（用于证明未采用非法 score）
       - evaluation.quality_score == 95（必须断言 quality_score 等于 monkeypatch 返回值，证明未采用 -1/101 clamp 后的 0/100）
       - evaluation.score_source == "content_evaluator"
       - evaluation.warnings（若实现该字段）包含 ignored_invalid_item_score（对 -1/101/"abc"/None 生效；缺失 score 可不写 warning）
  2. 新增“合法边界 item.score 仍优先生效”用例（覆盖 0/40/90/100）
     - 同样 monkeypatch evaluate_content 固定返回 quality_score=95（确保如果采用 evaluator 会 publish）
     - items 分别带 score=0 / 40 / 90 / 100
     - 断言：
       - decisions == ["discard","rewrite","publish","publish"]
       - evaluation.score_source == "item.score"
  3. 新增“评分失败必须 discard + reason=scoring_failed”用例：
     - monkeypatch evaluate_content 返回 success=False、error="x"
     - 断言 decision=discard，next_payload=None，reason=scoring_failed（字段落点按实现断言）

说明：测试使用 unittest 风格（与现有一致），通过 monkeypatch 模块级函数引用（workflows.crawler_workflow.evaluate_content）。

## 假设与关键决策

- score_source 与 reason 字段落点：优先写入 eval_result（最终出现在 out["items"][i]["evaluation"]），并在 processed item 顶层镜像一份以便检索；新增字段为向后兼容的增量，不会破坏现有使用方。
- 对 item.score 的“合法性”采用严格校验（0<=score<=100），不再 clamp；这是满足“非法评分必须重评分”的必要条件。
- 评分失败的强制 discard 在 evaluate 节点完成，以确保不会进入后续 LLM 决策与 payload 构建路径。
- 不调整 decision_rules 表达式语法与安全沙盒（_safe_eval_bool_expr），只确保输入 ctx 中的 quality_score 语义稳定。

## 验证步骤（按你的要求）

1. 先跑 item.score 校验相关用例（必须全绿）：

```bash
python -m pytest -q tests/test_crawler_workflow_score_routing.py
```

2. 再跑其余你指定的用例（必须全绿）：

```bash
python -m pytest -q tests/test_crawler_workflow_publish_payload_contract.py tests/test_crawler_workflow_decision_fallback.py tests/test_crawler_workflow_dry_run_no_llm.py tests/test_workflow_run_artifacts.py
```

3. 最后跑全量回归：

```bash
python -m pytest -q tests
```

4. 额外人工核对（非命令项）：
- 确认 -1 不会被当成 0 使用、101 不会被当成 100 使用、非数字不会被使用、缺失 score 会使用 content_evaluator。
- 确认 99/90/89/40/39 等边界行为不被破坏，score_source 字段符合预期。
- 确认 agents/topic_agent/ 未发生任何文件变更，hybrid 相关测试不受影响。
