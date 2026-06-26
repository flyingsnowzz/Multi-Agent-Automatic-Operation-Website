# CrawlerProcessorAgent 双链路门禁计划

## Summary

本轮只规划并后续只实施 `agents/crawler_processor_agent` 目录内调整，不修改其他 Agent，也不在本轮实施中修改目录外工作流文件。

目标是把 `CrawlerProcessorAgent` 的职责正式收敛为“门禁层”，并把链路明确规定成只有两条：

- 出现问题：`discard`
- 无问题：传给下一个 `文章重要性 Agent`

这意味着 crawler 不再承担 Topic 候选链路定位，也不承担 publish/rewrite 三态业务分发；它只负责把原始爬虫内容过滤成“可进入重要性层的合格素材”。

## Current State Analysis

### 1. 目录内口径仍是旧两态

- `agents/crawler_processor_agent/prompt.md` 将该模块定义为 `discard / pass_to_topic` 两态决策，并以 `material_score`、`topic_hint` 为核心评估结果。
- `agents/crawler_processor_agent/SKILL.md` 同样仍将输出定义为 `discard / pass_to_topic`。
- `agents/crawler_processor_agent/config.yaml` 仍保留 `pass_to_topic_status`、`material_score_threshold`、`publish_candidate_threshold`、`require_topic_hint`、`pass_to_topic_count` 等旧口径字段。

### 2. 当前仓库存在多套下游链路定义

- `workflows/crawler_workflow.py` 当前实现已使用 `discarded / ready_to_publish / ready_to_rewrite` 三态。
- `agents/topic_agent/tools/topic_candidate_reader.py`、`workflows/topic_candidate_workflow.py`、`main.py` 及多条测试仍明确依赖 `pass_to_topic`。
- 同时，现阶段产品方向已经明确：后续要剥离 `topic`，crawler 的下一层应当是“文章重要性层”。
- 结论：当前仓库里同时存在“Topic 候选链路”和“直接业务分发链路”的历史痕迹，需要通过本轮计划先把 crawler 的职责和出口重新规定清楚。

### 3. 评估职责与后续链路存在重合

- `agents/crawler_processor_agent/tools/content_evaluator.py` 当前输出 `quality_score / relevance_score / seo_potential_score`，本质上已在做综合质量评估。
- 这与系统中后续质量链路的字数、标题、SEO、结构、表达等评估职责存在重复。
- 但由于本轮不修改其他 Agent 和工作流，本轮只能先在 `crawler_processor_agent` 内部完成职责收敛和字段预备，不能一次完成整条链路切换。

### 4. 当前测试和外部调用仍以旧链路为事实标准

- `tests/test_crawler_workflow_score_routing.py`
- `tests/test_crawler_workflow_material_routing.py`
- `tests/test_crawler_workflow_publish_payload_contract.py`
- `tests/test_crawler_workflow_dry_run_no_llm.py`
- `tests/test_crawler_workflow_decision_fallback.py`

这些测试仍以 `pass_to_topic` 为预期状态，说明本轮若直接实施目录外链路切换，会影响现有测试契约，不符合本轮 scope；因此本轮计划重点是先把 crawler 自身定义和向“重要性层”交接的契约写清楚。

## Assumptions & Decisions

### 已确认决策

- 本轮范围仅包含 `agents/crawler_processor_agent` 目录。
- 本轮先做文档、配置、评估器输出的口径收敛。
- 本轮不修改其他 Agent。
- 本轮不实施目录外工作流的决策切换，只为下一阶段预留迁移基础。
- 当前系统链路需要按最新目标重新制定，不能简单回退到旧 Topic 链路，也不能直接沿用旧三态分发链路。
- crawler 的正式出口收敛为两条：
  - `discard`
  - `pass_to_importance_agent`（名称可在实施时落成兼容字段，但语义必须是“传给文章重要性 Agent”）

### 规划原则

- `CrawlerProcessorAgent` 在本轮文档定义中统一为：只负责入口门禁与合格素材交接，不负责内容重要性评分，不负责原文质量评分，不负责 publish/rewrite 最终分发。
- crawler 不再以 `TopicAgent` 作为目标链路定义。
- crawler 的“通过”结果语义统一为“进入文章重要性 Agent”。
- 本轮新增字段采用“新增不替换”的兼容策略：先补充新字段，不移除旧字段。
- 本轮不讨论 publish/rewrite 三态，不把它作为 crawler 的职责目标。

### 本轮明确 Out of Scope

- `workflows/crawler_workflow.py` 的决策逻辑切换
- 目录外“文章重要性 Agent”真实实现文件
- `agents/topic_agent/**` 的剥离或替换
- `workflows/topic_candidate_workflow.py`
- `main.py`
- 任何测试用例的行为改写

## Proposed Changes

### 1. 调整 `agents/crawler_processor_agent/prompt.md`

#### What

- 重写模块定位、任务描述、决策逻辑和输出示例。
- 将职责定义从“内容评分 + Topic 候选分流”改为“门禁检查 + 传递给文章重要性 Agent”。
- 明确区分：
  - 基础相关性：判断是否属于目标内容池
  - 基础可用性：判断是否具备进入后续处理的最低条件
- 明确门禁项与非门禁项边界：
  - 门禁项：重复、版权风险、来源风险、内容残缺/采集异常、高噪声、不相关、不可用
  - 非门禁项：内容重要性、时效性、通知属性、原文写作质量
- 对“结果”部分统一成两条链路：
  - `discard`
  - `pass_to_importance_agent`

#### Why

- 当前 `prompt.md` 仍把该模块描述为“给 TopicAgent 供料”，已不符合“剥离 topic、下一层转向重要性层”的目标。
- 先把链路规定清楚，后续实施时才不会继续在 crawler 内堆叠不属于它的评分职责。

#### How

- 在“系统角色”中新增总定义：
  - `CrawlerProcessorAgent 只负责入口门禁与合格素材交接，不负责内容重要性判断，不负责原文质量判断。`
- 将“内容评估”章节改写为：
  - 评估基础相关性
  - 评估基础可用性
  - 评估风险项
  - 输出门禁标志与交接字段
- 将输出示例改为双链路结构：
  - 新字段：`base_relevance_score`、`base_usability_score`、`source_ok`、`content_complete`、`noise_ratio`、`direct_publish_candidate`
  - 旧字段：`quality_score`、`relevance_score`、`seo_potential_score`、`material_score` 标注为兼容/过渡字段
  - 新结果：`gate_result`、`next_agent`
- 输出示例中的 `next_agent` 统一写成 `ArticleImportanceAgent` 或“文章重要性 Agent”。
- 增加“迁移说明”小节，明确本轮仅完成 crawler 自身口径收敛，目录外下一层实现仍在后续阶段落地。

### 2. 调整 `agents/crawler_processor_agent/SKILL.md`

#### What

- 与 `prompt.md` 保持一致，统一职责、输入、输出、工作流程、与其他 Agent 的关系。

#### Why

- 当前 `SKILL.md` 与 `prompt.md` 共同构成维护者理解入口，如果两者不一致，后续实现会继续偏离。

#### How

- 将“决策引擎”表述改为“门禁与交接准备”。
- 将输入中的 `material_score_threshold` 等旧概念调整为：
  - `min_base_relevance_score`
  - `min_base_usability_score`
  - `require_source_ok`
  - `require_content_complete`
- 输出改为“门禁评估报表 + 兼容链路字段”，明确 `pass_to_topic` 仍为兼容状态而非长期职责定义。
- 在“与其他 Agent 的关系”中加入说明：
  - crawler 的目标下游是“文章重要性 Agent”
  - 当前仓库中的 `TopicAgent` 相关链路属于历史兼容痕迹，不再作为目标架构

### 3. 调整 `agents/crawler_processor_agent/config.yaml`

#### What

- 新增门禁导向配置键。
- 保留旧键，但标记为兼容/待废弃。
- 补充双链路与交接语义注释。

#### Why

- 当前配置名强绑定旧 `material_score` 语义，无法支撑“门禁+粗路由”的新职责定义。
- 但目录外仍有旧调用，因此本轮不能直接删除旧键。

#### How

- 在 `evaluation_criteria` 下新增以下键：
  - `min_base_relevance_score`
  - `min_base_usability_score`
  - `require_content_complete`
  - `max_noise_ratio`
  - `trusted_source_domains`
- 删除本轮不再需要的“发布候选”语义配置规划：
  - 不再新增 `publish_candidate_*` 方向配置
- 保留以下旧键，但注释改为“兼容字段/待迁移”：
  - `material_score_threshold`
  - `discard_below_score`
  - `require_topic_hint`
- 在配置注释中新增“正式链路定义”：
  - crawler 只产生两类结果：`discard` / `pass_to_importance_agent`
  - 旧 `pass_to_topic_status` 仅为兼容历史调用，不再代表目标架构
- 在 `metrics.metrics_to_track` 中补充或注释以下指标迁移方向：
  - 旧：`pass_to_topic_count`、`average_material_score`
  - 新建议：`gate_pass_count`、`discard_count`、`average_base_relevance_score`、`average_base_usability_score`
- 本轮不强制移除旧指标，只做注释和新增候选指标占位。

### 4. 调整 `agents/crawler_processor_agent/tools/content_evaluator.py`

#### What

- 将 `ContentEvaluator` 的主输出语义从“综合质量评分”收敛到“门禁评估”。
- 新增门禁导向字段，同时保留旧字段兼容。

#### Why

- 该文件是目录内最核心的语义实现；如果只改文档和配置，不改输出结构，口径不会真正落地。
- 但由于本轮不修改 `crawler_workflow.py`，必须保留旧字段，确保目录外调用不会立刻断裂。

#### How

- 保留现有返回结构中的以下字段：
  - `success`
  - `quality_score`
  - `relevance_score`
  - `seo_potential_score`
  - `word_count`
  - `readability_score`
  - `has_copyright_risk`
  - `details`
- 在返回结构中新增以下字段：
  - `base_relevance_score`
  - `base_usability_score`
  - `source_ok`
  - `content_complete`
  - `noise_ratio`
  - `gate_passed`
  - `gate_result`
  - `next_agent`
  - `compatibility_mode`
- 调整内部函数分工：
  - 保留 `_rule_relevance()` 作为基础相关性的近似来源，并将其结果同步映射到 `base_relevance_score`
  - 将 `_rule_quality()` 重命名或包装为基础可用性评分来源，并同步映射到 `base_usability_score`
  - `_rule_seo()` 保留为兼容字段来源，但在注释中明确“不再是入口主决策字段”
- 在 `_content_signals()` 中新增门禁辅助信号：
  - `noise_ratio`
  - `content_complete`
  - `trusted_source`
- `source_ok` 的规则实现采用低风险策略：
  - 当前阶段仅做基础 URL 存在性和格式可用性检查
  - 若配置中存在 `trusted_source_domains`，则增加可信域名命中判断并写入 `details`
- `gate_passed` 的规则只由门禁项决定：
  - 重复
  - 版权风险
  - 来源异常
  - 内容残缺/采集异常
  - 噪声过高
  - 基础相关性不足
  - 基础可用性不足
- `gate_result` 只允许两种语义值：
  - `discard`
  - `pass_to_importance_agent`
- `next_agent` 固定输出 `ArticleImportanceAgent`
- 给新增字段加清晰注释，说明“当前为迁移预备字段，下一阶段由工作流接管”。

### 5. 轻量补充 `agents/crawler_processor_agent/crawler_processor_agent.py`

#### What

- 保持逻辑不变，只补充类级或方法级说明，注明本类当前仅为 `workflows.crawler_workflow.run_crawler_workflow` 的入口封装。

#### Why

- 该类名称容易让维护者误以为目录内已经自含完整决策逻辑；补充说明可减少误解。

#### How

- 不修改执行行为。
- 只增加说明注释，明确：
  - 本轮目录内仅完成职责定义与门禁字段收敛
  - crawler 的目标下游是“文章重要性 Agent”
  - 当前仓库中的真实状态落库行为仍由目录外工作流决定

## Verification Steps

### 文档与配置一致性检查

- 对照 `prompt.md`、`SKILL.md`、`config.yaml`，确认以下表述完全一致：
  - 模块职责
  - 两条结果链路
  - 下一个 Agent 定义
  - 基础相关性定义
  - 基础可用性定义
  - 风险项定义
  - 本轮兼容链路说明
  - 下一阶段联动边界说明

### 评估器兼容性检查

- 运行或补充针对 `ContentEvaluator.evaluate()` 的最小单测/调用检查，确认：
  - 旧字段仍存在
  - 新字段已输出
  - `gate_result` 只有 `discard / pass_to_importance_agent` 两种结果
  - `next_agent` 固定为 `ArticleImportanceAgent`
  - 对缺省 `source_url`、短文本、噪声文本、正常文章文本都能返回稳定结构

### 回归影响确认

- 不修改目录外测试预期。
- 至少人工确认以下目录外事实未被当前 round 破坏：
  - `crawler_workflow` 现有旧字段依赖仍可从 `content_evaluator` 获得
  - 目录外链路仍可继续运行，直到下一阶段正式切换到“重要性 Agent”交接

## Next Phase Dependencies

以下事项不在本轮实施范围内，但必须作为下一阶段联动计划：

- 新建或指定“文章重要性 Agent”的真实实现入口
- 修改目录外工作流，让 crawler 的通过结果从历史状态链路切换到“传给文章重要性 Agent”
- 决定历史 `TopicAgent` 链路如何下线或兼容迁移
- 若交接状态字段发生变化，需要同步联动：
  - `workflows/crawler_workflow.py`
  - `main.py`
  - 相关测试用例
