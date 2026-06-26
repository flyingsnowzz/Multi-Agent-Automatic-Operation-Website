## Summary

本次改造按“**先收口边界，再收口契约，最后再实现**”执行，目标是把 `CMSAgent` 收敛为纯粹的**发布执行层**，避免后续实现再次把内容判断、质量判断或改写决策塞回 CMS。

本次计划明确：

- 保留 `CMSAgent` 的发布执行、字段组装、发布前校验、媒体挂载、幂等发布、审计回写职责。
- 删除或禁止继续扩散一切非发布职责，包括内容价值判断、质量判断、改写决策和内容生产。
- 补齐输入契约、校验契约、输出契约，尤其修正当前“标题未校验”“update 成功后 `article_url` 不稳定”“dry-run 预检不够严格”等缺口。
- 对上下游的影响控制在最小范围内：本次**不新增 `publish_intent` 代码字段**，只在文档中明确“CMS 只接收已进入发布链路的内容”。
- 对 `config.yaml` 中未被实现消费的配置，本次**先不删除**，只在计划中标注风险和对齐策略，避免无关扩散。

---

## Current State Analysis

### 1. 当前 `CMSAgent` 的真实职责形态

- `CMSAgent.execute()` 当前只接收三组输入：`article`、`page_info`、`images`。
  - [cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L367-L372)
- payload 组装逻辑已经存在，负责提取标题、正文、slug、分类、标签、封面图、SEO 字段和 `topic_id`。
  - [cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L55-L102)
- 现有职责整体偏“发布执行层”，但文档、配置和部分契约还没有完全收口。

### 2. 当前发布前校验的缺口

- 现有代码只检查：
  - `content_not_empty`
  - `category_assigned`
  - `featured_image_set`
  - `slug_unique`
  - [cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L304-L365)
- 当前**没有 `title_not_empty` 校验**，但 `config.yaml` 的 custom contract 又把 `title` 定义为必填字段，这里存在实现与契约不一致。
  - [config.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/config.yaml#L42-L57)

### 3. 当前真实发布与 dry-run 行为

- 真实发布必须同时满足：
  - `publishing.dry_run == false`
  - 环境变量 `CMS_ENABLE_REAL_PUBLISH=true`
  - [cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L48-L53)
  - [SKILL.md](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/SKILL.md#L45-L52)
- 当前 `slug_unique` 在 dry-run 下只有开启 `CMS_ENABLE_SLUG_CHECK` 时才会真的检查，因此 dry-run 的风险暴露并不完整。
  - [cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L322-L345)
- 现有单测已经覆盖 dry-run 和 slug 冲突策略，具备较好的回归基础。
  - [test_cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/tests/test_cms_agent.py)

### 4. 当前输出契约的缺口

- 创建成功时，`CMSAgent` 直接把底层返回的 `post_id/post_url` 映射为 `article_id/article_url`。
  - [cms_agent.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L547-L554)
- 但 `update_post()` 当前只返回 `post_id` 和 `updated`，**不会返回 `post_url`**，所以 `overwrite_update` 场景下 `article_url` 不稳定。
  - [cms_client.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/tools/cms_client.py#L444-L502)

### 5. 当前配置与文档的收口问题

- `config.yaml` 中存在当前实现未直接消费的配置，例如：
  - `llm`
  - `publishing.auto_publish`
  - `execution.require_approval`
  - `quality.pre_publish`
  - [config.yaml](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/config.yaml)
- 用户已明确：**本次先不删除这些配置项**，但计划里要把“未生效/未落地”的事实说明清楚，防止误解。

### 6. 当前工作流接入现状

- `langgraph_workflow.py` 的 `_cms_node` 会汇总 `edit_result`、`write_result`、`seo_result`、`image_result` 后调用 `CMSAgent`，形态与“发布汇合点”一致。
  - [langgraph_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L602-L681)
- `hybrid_workflow.py` 的 `_cms_node` 当前从 `state["cms_result"]` 反推 `article/page_info/images` 再调用 `CMSAgent`，该接入点可疑，存在契约混乱风险。
  - [hybrid_workflow.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L601-L630)
- 用户已明确：本次计划**只标记 `hybrid_workflow` 风险，不把它作为本轮必须修正项**。

### 7. 当前文档基础

- `SKILL.md` 和 `prompt.md` 已经把 `CMSAgent` 定位成发布员，但仍需进一步收口边界，并同步补充标题校验、严格 dry-run 预检和 update 输出契约。
  - [SKILL.md](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/SKILL.md)
  - [prompt.md](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/prompt.md)

---

## Proposed Changes

### Phase A. 先收口边界

#### 目标

把 `CMSAgent` 的定位彻底固定为“发布执行层”，并用文档和调用约束防止职责外溢。

#### 变更文件

1. `agents/cms_agent/SKILL.md`
2. `agents/cms_agent/prompt.md`

#### 具体改动

- 重写 `CMSAgent` 的一句话定义：
  - 只负责发布执行与落库，不负责内容价值判断、写作质量判断、改写决策、正文生产。
- 明确标准输入：
  - `article`
  - `page_info`
  - `images`
- 明确业务前提：
  - CMS 只处理“已被上游决定进入发布链路”的内容。
  - 本次**只写入文档，不新增 `publish_intent` 代码字段**。
- 明确标准职责：
  - 字段组装
  - 发布前校验
  - 图片挂载
  - 创建/更新/草稿/定时发布
  - 幂等和冲突处理
  - 审计回写
- 明确禁止事项：
  - 不做价值判断
  - 不做质量判断
  - 不做改写路由
  - 不做内容生产

#### 为什么这么改

- 用户已经确认 CMS 要被严格收敛为“发表链路”。
- 当前实现本身已接近这个定位，先统一文档边界能减少后续实现漂移。

### Phase B. 再收口契约

#### 目标

把“输入契约、校验契约、输出契约”写成实现可对齐的单一口径。

#### 变更文件

1. `agents/cms_agent/config.yaml`
2. `agents/cms_agent/cms_agent.py`
3. `agents/cms_agent/tools/cms_client.py`
4. `agents/cms_agent/SKILL.md`
5. `agents/cms_agent/prompt.md`

#### 具体改动

##### B1. 输入契约收口

- 维持现有 `execute(article, page_info, images)` 形式，不新增第四个参数。
- 在文档中明确三类输入字段分层：
  - 必需项：`title`、正文、分类、slug（可自动生成）、发布模式信息
  - 条件必需项：封面图、发布时间、SEO 字段
  - 审计可选项：`topic_id`、`content_md`、`schema_json`

##### B2. 校验契约收口

- 在 `cms_agent.py` 中新增**内建必校验**：
  - `title_not_empty`
- 保留现有校验：
  - `content_not_empty`
  - `category_assigned`
  - `featured_image_set`
  - `slug_unique`
- 调整校验分层：
  - 基础字段校验：标题、正文、分类
  - 发布资源校验：封面图
  - 唯一性校验：slug
- `title_not_empty` 作为**内建基础要求**，不依赖配置是否显式列入 `pre_publish_check`。
- 在 `config.yaml` 和文档里同步记录新的校验项，避免代码与说明不一致。

##### B3. dry-run 预检收口

- 按用户要求，把 dry-run 升级为**默认严格预检**：
  - 不再依赖 `CMS_ENABLE_SLUG_CHECK` 才做 slug 风险暴露。
  - dry-run 尽量复用真实发布前检查，提前暴露缺字段、缺图、slug 冲突等问题。
- 仍保持“双闸门真实发布”不变：
  - `publishing.dry_run == false`
  - `CMS_ENABLE_REAL_PUBLISH=true`
- 即：
  - **检查更严格**
  - **执行仍然安全**

##### B4. 输出契约收口

- 统一定义不同状态下的返回结构：
  - `dry_run`
  - `failed`
  - `draft`
  - `scheduled`
  - `publish`
- 明确：
  - `checks` 必须稳定返回
  - `errors` 必须稳定返回
  - `payload` 在 `dry_run/failed` 必须可用于审计
- 重点修正 update 场景：
  - `overwrite_update` 更新成功后应**尽量补齐 `article_url`**
  - 如底层 `PATCH` 响应无 URL，则通过已有信息或追加查询补齐
  - 若确实无法补齐，需在契约中明确兜底行为并保证 `article_id` 一定可用

##### B5. 配置收口策略

- `config.yaml` 本次**先不删除**未使用配置。
- 但要在计划执行时做两件事：
  - 明确哪些配置已被实际消费
  - 明确哪些配置目前只是保留项/未生效项
- 这样可以先控制改动面，避免一次性做破坏性清理。

#### 为什么这么改

- 当前最核心的问题不是“功能不够多”，而是“口径不够硬”。
- 先把契约硬化，后续不管对接哪种 CMS 或哪个 workflow，行为都会更稳定。

### Phase C. 最后落到实现

#### 目标

在不引入新职责的前提下，完成最小必要实现改造。

#### 变更文件

1. `agents/cms_agent/cms_agent.py`
2. `agents/cms_agent/tools/cms_client.py`
3. `tests/test_cms_agent.py`
4. `tests/integration/test_custom_cms_contract.py`
5. `workflows/langgraph_workflow.py`
6. `workflows/hybrid_workflow.py`（仅风险记录或最小兼容核对，不作为本轮主改文件）

#### 具体改动

##### C1. `cms_agent.py`

- 增加 `title_not_empty` 校验实现。
- 重构 `_pre_publish_checks()`，让基础校验和 slug 校验结构更清晰。
- 调整 dry-run 行为，使其默认执行严格预检。
- 明确不同状态的返回结构，保证 `checks/errors/payload` 的一致性。

##### C2. `cms_client.py`

- 改造 `update_post()` 返回结构，尽量与 `create_post()` 对齐。
- 为 update 成功返回 `post_url` 提供能力：
  - 优先从响应解析
  - 解析不到时，再按 provider/custom contract 做补查
- 保持 create/update 的统一输出风格，减少 `CMSAgent` 侧分支判断复杂度。

##### C3. `test_cms_agent.py`

- 保留现有测试覆盖：
  - `dry_run` 闸门
  - slug 冲突自动改写
  - slug 冲突 fail
  - slug 冲突选择 update
  - retry helper
- 新增或更新测试：
  - 标题缺失时返回失败
  - dry-run 默认严格预检 slug
  - `overwrite_update` 成功时尽量返回 `article_url`
  - 不同状态下 `checks/errors/payload` 的结构稳定性

##### C4. `test_custom_cms_contract.py`

- 核对现有 contract 集成测试与新契约保持一致。
- 如果 update URL 补齐需要新增联调断言，则在这里补充最小必要覆盖。

##### C5. `langgraph_workflow.py`

- 仅做**必要兼容核对**：
  - 确保它传给 `CMSAgent` 的 `article/page_info/images` 满足新契约要求。
- 本轮不在 workflow 中新增发布决策逻辑。

##### C6. `hybrid_workflow.py`

- 本次不把它作为主实现目标。
- 在执行阶段只做以下两件事之一：
  - 如无需改动则仅记录风险
  - 如因新契约必须做最小兼容修补，则仅限于不扩散职责的修补

#### 为什么这么改

- 这批实现改动都直接服务于“边界收口 + 契约收口”。
- 不做额外功能增强，不扩大改造面，不把 CMS 变成新的决策中心。

---

## File-by-File Change Map

### `agents/cms_agent/SKILL.md`

- **What**：重写边界定义、输入/职责/输出规范、禁止事项。
- **Why**：把 CMS 从“看起来会做很多事”收敛为“只做发布执行”。
- **How**：更新职责描述、输入说明、输出状态说明、dry-run 说明、风险与边界说明。

### `agents/cms_agent/prompt.md`

- **What**：同步边界、补充标题校验与严格 dry-run 预检口径。
- **Why**：避免 prompt 继续暗示 CMS 承担超出发布层的职责。
- **How**：更新检查清单、输出示例、发布控制说明。

### `agents/cms_agent/config.yaml`

- **What**：补充/同步新校验项和契约说明，但先不删未落地配置。
- **Why**：让配置与实现、文档口径一致，同时控制改动风险。
- **How**：更新 `pre_publish_check` 说明、必要注释和契约注释。

### `agents/cms_agent/cms_agent.py`

- **What**：实现 `title_not_empty`、严格 dry-run 预检、统一输出契约。
- **Why**：当前实现最直接的功能缺口在这里。
- **How**：调整 `_pre_publish_checks()`、返回结构、dry-run 判定流程。

### `agents/cms_agent/tools/cms_client.py`

- **What**：收口 create/update 返回契约，尽量补齐 update 的 URL。
- **Why**：当前 `article_url` 在 update 场景不稳定。
- **How**：统一 `create_post()` 和 `update_post()` 返回结构，必要时增加补查。

### `tests/test_cms_agent.py`

- **What**：补回归测试并锁死新契约。
- **Why**：本次是契约收口，不加测试容易回退。
- **How**：围绕标题校验、严格 dry-run、update URL 契约新增测试。

### `tests/integration/test_custom_cms_contract.py`

- **What**：最小调整契约断言。
- **Why**：保证 custom CMS 联调不被新契约破坏。
- **How**：仅在必要时补 update 场景或返回字段断言。

### `workflows/langgraph_workflow.py`

- **What**：核对调用参数是否满足新契约。
- **Why**：CMS 契约收口后，上游要至少不缺字段。
- **How**：只做必要兼容，不改变职责分工。

### `workflows/hybrid_workflow.py`

- **What**：记录 `_cms_node` 的接入风险。
- **Why**：当前输入来源可疑，但用户要求本轮只标风险。
- **How**：除非被新契约逼出最小兼容修补，否则不纳入主改动。

---

## Assumptions & Decisions

### 用户已确认的决策

- **不在本次代码里引入显式 `publish_intent` 字段**，只先写进文档边界。
- **`config.yaml` 中未使用配置本次先不删除**。
- **dry-run 默认改为严格预检**。
- **`overwrite_update` 成功后尽量补齐 `article_url`**。
- **`title_not_empty` 作为内建必校验**，而不是仅文档要求或纯配置项。
- **`hybrid_workflow.py` 本次只标风险，不作为主改造目标**。

### 本计划的实现假设

- `CMSAgent` 继续作为发布链路末端执行层，不新增新的评分或路由职责。
- `langgraph_workflow.py` 仍是本次主要工作流兼容对象。
- `custom CMS` contract 继续存在并保持可用，不做 provider 大规模重构。

---

## Verification

### 1. 静态检查

- 读取并核对近期改动文件的诊断信息，确保未引入明显语法或类型错误。

### 2. 单元测试

- 跑 `tests/test_cms_agent.py`，重点验证：
  - 标题为空失败
  - dry-run 严格预检
  - slug 冲突路径
  - update 返回契约

### 3. 集成测试

- 跑 `tests/integration/test_custom_cms_contract.py`，确保 custom contract 仍兼容。
- 如果本次为 update URL 补齐增加了联调逻辑，则补对应断言。

### 4. 工作流兼容验证

- 核对 `langgraph_workflow.py` 对 `CMSAgent` 的调用输入仍满足新契约。
- 确认 `hybrid_workflow.py` 至少被显式标注风险，不在本轮悄悄扩散改造范围。

### 5. 验收标准

- `CMSAgent` 的定义清晰且单一：只负责发布执行。
- `title_not_empty` 生效。
- dry-run 默认执行严格预检。
- `create/update` 的输出契约更一致。
- 文档、配置、实现三者口径一致。
- 工作流兼容影响最小，没有把内容判断逻辑回流到 CMS。
