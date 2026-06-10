## Summary
本计划用于完善 TopicAgent 在 mock/dry-run 模式下的“选题生成”逻辑，重点解决：
- 关键词扩展与问题型关键词生成存在机械拼接（如“怎么EMBA”“EMBA技巧/方法/工具”）。
- 标题生成过度依赖通用模板（如“完整指南：核心要点与实用建议”），导致中文表达不自然、语义模糊。
- priority_score 偏“流量/难度/竞争”等指标，无法代表语义质量；缺少业务语义过滤与质量闸门。

目标：在不接入大模型、不访问真实 API 的前提下，让 mock 输出可业务消费、语义清晰、中文自然，并保持 TopicAgent.execute() 返回结构兼容（允许新增字段）。

## Current State Analysis
### KeywordResearchTool（mock）现状
- 相关关键词扩展通过 `expand_keyword_cluster()` 的固定后缀列表拼接生成（包含“技巧/方法/工具”等泛词），见 [keyword_research.py:L268-L281](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/topic_agent/tools/keyword_research.py#L268-L281)。
- 问题型关键词通过模板 `["如何{kw}", "怎么{kw}", ...]` 生成，导致出现“怎么EMBA”，见 [keyword_research.py:L236-L248](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/topic_agent/tools/keyword_research.py#L236-L248)。
- 过滤仅基于 config.yaml 的 `keyword_research.filters.exclude`（子串排除），见 [keyword_research.py:L302-L311](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/topic_agent/tools/keyword_research.py#L302-L311)；缺少业务语义禁用模式、短语完整性检查。

### TopicAgent 标题与优先级现状
- 标题生成 `_suggest_title()` 仍以通用模板拼接，容易生成病句/歧义标题，见 [topic_agent.py:L419-L431](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/topic_agent/topic_agent.py#L419-L431)。
- priority_score 目前已支持读取 `output.priority_weights`，但语义质量不参与评分与排序（且 config.yaml 的 prefer_terms 仍包含“技巧/方法”等泛词）。
- 现有 tests 主要验证契约与基本类型，不覆盖业务语义质量，见 [test_topic_agent_tools.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/tests/test_topic_agent_tools.py)。

### 配置现状
- [agents/topic_agent/config.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/topic_agent/config.yaml) 中存在 filters.prefer（含“技巧/方法/流程”等），但没有“业务语义配置/禁用模式/质量闸门阈值”。
- [config/keywords.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/config/keywords.yaml) 目前仅作为“本地关键词指标池”（keywords 列表）使用，未包含行业语义约束。

## Proposed Changes
以下修改严格限定在用户给定范围内（并补充必要测试），且不引入大模型/真实 API。

### 1) 增加业务语义配置（agents/topic_agent/config.yaml + config/keywords.yaml）
**目标**：把“EMBA/高管教育”的业务约束变成可配置数据，避免硬编码散落在逻辑里；同时保持现有 `keywords:` 指标池结构不被破坏。

**变更点**
- 在 `agents/topic_agent/config.yaml` 新增（或补充）业务语义段，例如：
  - `business_semantics.industry`
  - `business_semantics.target_audience`
  - `business_semantics.preferred_topic_angles`
  - `business_semantics.forbidden_patterns`
  - `business_semantics.generic_bad_suffixes`（如 技巧/方法/工具/模板 等）
  - `quality_gates.semantic_min_score`（过滤阈值）
  - `quality_gates.title_min_len`、`quality_gates.keyword_min_len` 等
- 调整 `keyword_research.filters.prefer`：将“技巧/方法/工具”移出 prefer（避免在 mock 里被加权放大），改为更贴近 EMBA 的倾向词（如 报考条件/申请流程/院校/区别/学费/回报/适合/值得/课程 等）。保留 filters.exclude 并补充“工具/技巧/方法”等泛词排除（与 forbidden_patterns 协同）。
- 在 `config/keywords.yaml` 保持原 `keywords:` 列表不动，新增可选顶层结构（示例：`profiles.emba`），用于：
  - 领域词/角度词库（angle_terms）
  - audience 词库
  - forbidden_patterns（可与 agent config 合并）
  - mock 扩展模板（mock_expansion_templates / mock_question_templates）
 这样 KeywordResearchTool 可以在找不到 agent config 时回退到全局 profile，确保可移植性。

**合并策略**
- 运行时以 `agents/topic_agent/config.yaml` 为主；如某字段缺失，再回退 `config/keywords.yaml` 的 profile（例如 `profiles.emba`）。
- profile 选择规则：mock 模式下若 seed 含 “EMBA”/“商学院”等，则使用 emba profile；否则使用 default profile（不做强行业约束）。

### 2) 重写关键词扩展与问题型关键词生成（agents/topic_agent/tools/keyword_research.py）
**目标**：mock 模式下不再用“后缀拼接”和“怎么{kw}”模板，改为“角度驱动 + 语义过滤”的生成方式，输出更接近真实搜索意图的中文关键词。

**核心方案**
- 引入“领域角度驱动扩展”：
  - 对 EMBA 种子词，优先生成清晰意图短语：报考条件、申请流程、院校怎么选、和MBA区别、适合人群、学费与回报、课程价值、是否值得等。
  - 扩展方式以配置中的 `preferred_topic_angles` 和/或 `profiles.emba.angle_terms` 为主，不再固定拼接“技巧/方法/工具”。
- 问题型关键词生成不再使用 `怎么{kw}` 这类前置疑问模板；改为“自然问法”的结构：
  - `{kw}报考条件是什么`
  - `{kw}申请流程怎么走`
  - `{kw}院校怎么选`
  - `读{kw}有什么用`
  - `企业高管适合读{kw}吗`
  - `{kw}学费值不值`
- 增加 mock 语义过滤器（在 KeywordResearchTool 内部）：
  - forbidden_patterns：命中直接丢弃
  - generic_bad_suffixes：如 “技巧/方法/工具/模板/清单” 这类“泛词+无对象/无承诺”的关键词（对 EMBA 场景）进行丢弃或强降权
  - 短语完整性：剔除过短（如长度 < keyword_min_len）、缺少核心实体/角度词的短语
  - 病句结构：如 “怎么EMBA”“如何EMBA” 这种 “疑问词 + 名词” 的不完整结构直接剔除
- 输出保持 `KeywordResearchResult` 契约不变，仍返回 primary_keywords/long_tail_keywords/questions/gaps。

### 3) 优化标题生成策略（agents/topic_agent/topic_agent.py）
**目标**：标题不再“模板+关键词硬拼”，而是“角度/意图驱动”，包含读者或场景、明确内容承诺、中文自然，并适合下游 Research/Writer 消费。

**核心方案**
- 用“角度识别”替代 content_type 单一模板：
  - 从 keyword 中识别 angle（报考条件/申请流程/院校选择/EMBA vs MBA/课程价值/职业回报/适合人群/学费回报）
  - 每个 angle 提供 2-3 个高质量标题模板（不接 LLM、纯规则）
- 标题模板遵循：
  - “对象 + 场景 + 承诺”结构
  - 必要时补足主语（例如“企业高管/管理者/准备申请者”）
  - 避免“完整指南/核心要点/实用建议/避坑”这种泛化词堆叠
  - 可选加入年份（如 2026）作为 mock 规则（可配置开关）

### 4) 增加语义质量闸门（agents/topic_agent/topic_agent.py）
**目标**：引入 `semantic_quality_score` 与 `quality_warnings`，并把“低语义质量”从最终 topics 中过滤掉或强降权（按用户要求：低分不应出现在最终 topics）。

**核心方案**
- 新增 `_semantic_quality_check()`（规则评分，0-100）：
  - forbidden_patterns 命中：score 直接拉低并记录 warning
  - 过短：keyword/title 长度低于阈值 -> 降分/过滤
  - 泛词/病句：含 “技巧/方法/工具/完整指南/核心要点”等 -> 降分
  - 业务相关性：包含行业实体（EMBA/MBA/商学院/高管等）+ 包含角度词（报考/申请/院校/学费/回报/课程/区别/适合/值得）-> 加分
  - 标题承诺：标题中需至少出现角度词或承诺结构（详解/对比/是否值得/怎么选/流程/条件等）-> 加分；否则降分
- 在 `execute()` 构建 topics 时，为每个 topic 追加：
  - `semantic_quality_score`
  - `quality_warnings`
- 在最终 topics 输出前应用 gate：
  - `semantic_quality_score < semantic_min_score` 的 topic 从 topics 列表剔除
  - 同时将 `priority_score` 与 `semantic_quality_score` 合并成 `final_rank_score`（内部使用，不写入契约也可），确保“语义质量”对排序有决定性影响

### 5) 保持契约兼容
**顶层返回结构**保持不变：
- topics/raw_keyword_data/raw_serp_data/warnings/is_mock/data_confidence/generated_at 必须保留（现有测试依赖）。

**topic 内字段**保持现有字段不变，并新增：
- priority_score（已有）
- semantic_quality_score（新增）
- quality_warnings（新增）

### 6) 补充/修改测试（tests/）
**新增或修改覆盖点**
- KeywordResearchTool(mock) 不再生成：
  - “怎么EMBA”“如何EMBA”
  - “EMBA技巧”“EMBA方法”“EMBA 工具”
- TopicAgent(mock) 输出标题不包含 forbidden_patterns / 泛化病句模板
- 每个 topic 都有 semantic_quality_score 与 quality_warnings
- `semantic_quality_score` 低的 topic 不会出现在最终 topics
- 输入 EMBA 时至少生成 3 个语义清晰、业务相关的选题（标题自然、角度明确）

**建议测试文件**
- 扩展 [test_topic_agent_tools.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/tests/test_topic_agent_tools.py)（保持原契约断言不删，追加新字段断言）
- 新增 `tests/test_topic_agent_semantic_quality_mock.py`（专门覆盖 forbidden + 至少 3 个合格选题）

## Assumptions & Decisions
- 不接入 LLM、不访问真实 API：所有语义质量判断均为规则/词表/模板驱动。
- 当前阶段只重点保证 EMBA/高管教育 profile 的 mock 输出可信；其他 seed 关键词走 default profile（尽量不变差）。
- 过滤策略：按用户要求，低 semantic_quality_score 的 topic 直接不进入最终 topics（不是仅降权）。
- 评分解释性优先：quality_warnings 作为可观测输出，便于后续调参。

## Verification
按用户要求（以及兼容本仓库常见 venv 结构），执行以下验证：

1) 语法检查
```powershell
.\.venv\Scripts\python.exe -m py_compile agents\topic_agent\topic_agent.py agents\topic_agent\tools\keyword_research.py
```
如项目实际虚拟环境目录为 `venv`，则替换为：
```powershell
.\venv\Scripts\python.exe -m py_compile agents\topic_agent\topic_agent.py agents\topic_agent\tools\keyword_research.py
```

2) 单测（用户指定 + 如新增测试需补跑）
```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_topic_agent_tools.py tests\test_topic_agent_priority_weights.py tests\test_topic_to_hybrid_adapter.py -q
```
若新增了 `tests/test_topic_agent_semantic_quality_mock.py`，需追加：
```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_topic_agent_semantic_quality_mock.py -q
```

