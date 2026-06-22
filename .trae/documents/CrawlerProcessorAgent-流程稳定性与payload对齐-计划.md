## Summary

补齐并修复 `agents/crawler_processor_agent` 与 `workflows/crawler_workflow.py` 的关键缺陷，目标是让“爬虫批处理摄取流程”在 **无 LLM / 无 CMS / dry-run** 场景下也能稳定运行，并输出可解释、可追踪、可对齐下游的结构化结果：

- 修复 `crawler_db_reader.py` 的运行期错误与安全风险（aiomysql 作用域、SQL 标识符校验、Mongo collection 配置、Mongo updated_at 写法、Mongo 读写字段标准化）
- 去重链路真正接入 `published_content_db`（按“最近 N 条”可控查询），并增强低成本可靠去重策略（URL 精确匹配、标题归一化、HTML 清洗后相似度）
- 决策链路改为“默认规则决策”，LLM 决策 **双闸门显式开启**，且 dry-run 强制不调用；LLM 异常自动回退规则决策，流程不中断
- publish 分支 payload 对齐当前 CMSAgent 真实输入契约（`article/page_info/images` 结构），仍保持“只生成 payload，不直投 CMSAgent”的稳定模式
- 让 `config.yaml` 中关键规则真正生效：`required_fields`、`dedup.action_on_duplicate`、`copyright_risk`（规则版）、`decision_rules`（安全表达式求值的最小实现）
- 补测试覆盖关键回归点：MySQL/Mongo 标准化、SQL 标识符校验、去重命中、规则决策分流、LLM 异常回退、dry-run 不触发 LLM

---

## Current State Analysis (Grounded)

### 1) 数据库读取器存在运行期错误与 SQL 注入风险

- `crawler_db_reader.py` 在 `_get_mysql_conn()` 内局部 import `aiomysql`，但 `_read_mysql_pending()` 使用 `aiomysql.DictCursor`，MySQL 真连上后存在 `NameError` 风险。
  - [crawler_db_reader.py:L68-L80](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L68-L80)
  - [crawler_db_reader.py:L150-L161](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L150-L161)
- MySQL SQL 语句中表名/字段名直接拼接（`self.table`、`self.status_field`），配置被污染时存在 SQL 注入风险。
  - [crawler_db_reader.py:L150-L156](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L150-L156)
  - [crawler_db_reader.py:L276-L280](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L276-L280)

### 2) MongoDB 分支字段不统一且更新时间写法错误

- Mongo 分支固定使用 `self.table` 作为 collection，但示例配置字段为 `collection`。
  - [crawler_db_reader.py:L45-L51](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L45-L51)
  - [crawler_db_reader.py:L195-L220](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L195-L220)
- Mongo 读取结果没有像 MySQL 一样标准化为 `id/title/content/source_url/...`，上游字段不稳定。
  - [crawler_db_reader.py:L186-L220](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L186-L220)
- Mongo 更新 `updated_at` 使用字符串 `$$NOW`，不会产生真实时间。
  - [crawler_db_reader.py:L299-L308](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/crawler_db_reader.py#L299-L308)

### 3) 去重未接入 published_content_db，算法偏粗

- `DedupChecker._query_published_articles()` 仍是 TODO，返回空列表，导致默认永远不命中重复。
  - [dedup_checker.py:L97-L104](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/dedup_checker.py#L97-L104)
- 去重仅做字符级相似度，缺少 URL 精确匹配、标题归一化、HTML 清洗等更可靠的低成本策略。
  - [dedup_checker.py:L122-L174](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/tools/dedup_checker.py#L122-L174)
- `crawler_workflow` 没有消费 `published_content_db` 配置，只有调用方可手动注入 `published_articles`。
  - [crawler_workflow.py:L440-L460](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L440-L460)

### 4) 决策节点强依赖 LLM，dry-run 也可能触发

- `_decide_node()` 无条件调用 `_decide_with_crewai()`，且没有 try/except；LLM/CrewAI 异常会影响批处理稳定性。
  - [crawler_workflow.py:L487-L554](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L487-L554)
- dry-run 只阻止落库，不阻止 LLM 决策调用。
  - [crawler_workflow.py:L557-L578](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L557-L578)

### 5) publish payload 与 CMSAgent 当前契约不一致

- 当前 publish payload 仍是旧结构 `title/content/source_url/meta`，而 CMSAgent.execute 需要 `execute(article, page_info, images)` 并在内部抽取 `content_md/content_html/meta_title/meta_description/...`。
  - [crawler_workflow.py:L229-L247](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crawler_workflow.py#L229-L247)
  - [CMSAgent.execute](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py#L367-L456)

### 6) 文档与实际代码不一致

- `agents/crawler_processor_agent/SKILL.md` 描述存在 `CrawlerProcessorAgent` 类，但目录下只有 tools/config/prompt，没有可导入主类。
  - [SKILL.md](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/crawler_processor_agent/SKILL.md)

---

## Decisions (Locked)

- published_content_db 默认查询范围：按“最近 N 条”查询（例如 2000），避免依赖 CMS 状态字段，性能可控
- LLM 决策：config + 环境变量双闸门；且 dry-run 强制不调用；LLM 异常回退规则决策
- publish 分支：只生成 CMSAgent payload（不直接调用 CMSAgent），保持离线稳定与可重复

---

## Proposed Changes (Decision-Complete)

### A) 修复并加固数据库读取器（运行期稳定 + 安全）

**修改文件**
- `agents/crawler_processor_agent/tools/crawler_db_reader.py`

1) 修复 aiomysql 作用域问题
- 在使用 `aiomysql.DictCursor` 的函数内显式 import，或在 `_get_mysql_conn()` 时将模块保存到 `self._aiomysql`，避免 NameError
- 同时对缺少依赖时给出可解释错误（`aiomysql_missing` / `motor_missing`）

2) SQL 标识符校验与安全拼接
- 新增 `_validate_identifier(name)`：仅允许 `[A-Za-z0-9_]+`
- 对 `self.table`、`self.status_field` 做校验；不通过则在初始化或执行时直接返回 `success:false + error: invalid_identifier`
- 统一用反引号包裹 MySQL 标识符（`` `table` `` / `` `status` ``），避免保留字问题

3) Mongo collection 配置统一
- `self.collection = config.get("collection") or config.get("table") or ""`
- Mongo 分支统一使用 `db[self.collection]`

4) Mongo 读取标准化
- Mongo 读取后也输出统一结构（与 MySQL 一致）：
  - `id`（字符串化的 `_id`）
  - `title/content/source_url/published_at/author/category/spider_name`
  - `raw_data`（保留原始 doc）
- 同样使用 `field_mapping` 与 `_default_mapping` 适配杂乱字段

5) Mongo updated_at 写入修复
- `update_one(..., {"$set": {...}, "$currentDate": {"updated_at": True}})` 作为默认方案（稳定、无需传 datetime）
- 同时修复 `_id` 匹配：当 `record_id` 为字符串时尝试转换成 `bson.ObjectId`

### B) DedupChecker 接入 published_content_db + 低成本可靠去重策略

**修改文件**
- `agents/crawler_processor_agent/tools/dedup_checker.py`
- `workflows/crawler_workflow.py`（dedup 节点接线）

1) 对接 published_content_db（MySQL/Mongo）
- `DedupChecker.__init__` 接收 `published_db_config`（来自 `config.yaml: published_content_db`）
- 实现 `_query_published_articles(limit=N)`：
  - MySQL：SELECT `title_field`,`content_field`,（可选 `source_url_field`） FROM `table` ORDER BY id DESC LIMIT N
  - Mongo：find({}, projection=...) sort(_id,-1) limit N
- 同样做标识符校验（table/field names）
- 输出统一为 `[{title, content, source_url?}]`

2) 新增低成本去重策略（在原有相似度基础上前置判断）
- URL 精确匹配：若待处理 item 的 `source_url` 与已发布库任一 `source_url` 相同，直接命中 duplicate（最高优先级）
- 标题归一化匹配：lower + 去空白/标点（中英文）后相等直接命中 duplicate
- 内容清洗后相似度：
  - 去 HTML 标签、压缩空白
  - 对 cosine/jaccard 使用“token 级”（按中英文词/字切分）而不是纯字符 tf（提升鲁棒性且不引入大依赖）

3) 在 crawler_workflow 中接线 published_content_db
- `_init_node` 中把 `published_content_db` 存入 state（例如 `state["published_db_cfg"]`）
- `_dedup_node`：
  - 若调用方传入 `published_articles` 则优先使用（便于测试）
  - 否则用 `DedupChecker` 自动从 published_content_db 拉取最近 N 条
- 同时让 `dedup.action_on_duplicate` 生效：duplicate 时在 record 里写入原因，并确保决策可按配置执行 discard/mark_duplicate（mark_duplicate=更新为 discard_status 或新增 duplicate_status，二选一并在实现中明确）

### C) 决策链路默认规则决策，LLM 显式开启且异常回退

**修改文件**
- `workflows/crawler_workflow.py`
- `agents/crawler_processor_agent/config.yaml`（新增开关项）

1) 新增开关（双闸门）
- config 增加：`execution.llm_decision_enabled: false`（默认 false）
- env 增加：`CRAWLER_ENABLE_LLM_DECISION=false`（需要 true 才允许）
- 判定函数：`_should_use_llm_decision(state)`：
  - dry_run=True → false
  - config llm_decision_enabled=False → false
  - env 未开启 → false
  - 其余 → true

2) `_decide_node` 调整
- 默认先用 `_decide()` 产生规则结果
- 只有 `_should_use_llm_decision()` 为 true 时，才尝试 `_decide_with_crewai()` 覆盖决策
- `_decide_with_crewai()` 外层加 try/except：任何异常都记录到 state（例如 `state["llm_error"]`），并保持规则结果不变
- 对 LLM 输出做 schema 校验（decision/status_to_update/next_agent/rewrite_instructions），不合规则忽略并回退规则

3) dry-run 行为保证
- 明确：dry-run 不落库、不调用 LLM，确保离线/可重复/低成本

### D) 对齐 CMSAgent payload（publish 分支）

**修改文件**
- `workflows/crawler_workflow.py`

把 `_build_publish_payload()` 改为输出 CMSAgent 可直接消费的结构：

```python
{
  "article": {
    "title": "...",
    "content_md": "...",         # crawler content 默认放这里
    "content_html": "",          # 若后续有 html 清洗可补
    "meta": {
      "source_url": "...",
      "source": "crawler",
      "crawler_record_id": ...,
      ...
    }
  },
  "page_info": {
    "slug": "",                  # 可为空，CMSAgent 会自动 slugify
    "category": "...",           # 来自 item.category（或映射后的值）
    "tags": [...],               # 默认用 target_keywords 做 tags（去重、限长）
    "primary_keyword": ""        # 可选：取 target_keywords[0]
  },
  "images": null
}
```

保持“只生成 payload，不直投 CMSAgent”的执行模式不变（next_agent 仍标记为 CMSAgent 以便上游路由）。

### E) 让 config.yaml 中关键规则真正生效（最小实现）

**修改文件**
- `workflows/crawler_workflow.py`
- `agents/crawler_processor_agent/tools/content_evaluator.py`

1) required_fields 生效
- 在 `pick_next` 或 `evaluate` 前做字段校验：缺字段直接 decision=discard，并写入原因（missing_fields）

2) copyright_risk 生效
- `content_evaluator._check_copyright()` 改为读取 config：
  - `check_enabled` false → 返回 false
  - `risk_keywords` / `risk_patterns`（正则）命中 → true
- 在规则决策 `_decide()` 中已包含 `has_copyright_risk` 判断，确保与 config 语义一致

3) decision_rules 生效（安全表达式求值）
- 实现一个最小安全 evaluator：只允许变量、数字、比较运算、and/or/not
- 把 `decision_rules.discard_conditions/publish_conditions/rewrite_conditions` 从“说明书”变为实际规则来源：
  - 计算上下文变量：`quality_score/relevance_score/seo_potential_score/word_count/is_duplicate/has_copyright_risk` + 阈值变量（来自 execution/criteria）
  - 优先使用 decision_rules；若配置为空则回退现有 `_decide()` 逻辑

### F) 文档/代码一致性：补主类入口（可导入）

**新增文件**
- `agents/crawler_processor_agent/__init__.py`
- `agents/crawler_processor_agent/crawler_processor_agent.py`

实现 `CrawlerProcessorAgent.execute(...)` 作为统一入口，内部直接调用 `workflows.crawler_workflow.run_crawler_workflow()`，并对外暴露与 SKILL.md 一致的使用方式（同时更新 SKILL.md 示例代码不再是 pass）。

---

## Verification

### 1) 静态校验
- `venv\\Scripts\\python.exe -m py_compile agents\\crawler_processor_agent\\tools\\crawler_db_reader.py agents\\crawler_processor_agent\\tools\\dedup_checker.py agents\\crawler_processor_agent\\tools\\content_evaluator.py workflows\\crawler_workflow.py`

### 2) 单元测试（新增）

- `tests/test_crawler_db_reader_security.py`
  - table/status_field 非法标识符 → 返回明确错误
- `tests/test_crawler_db_reader_mongo_normalize.py`
  - Mongo doc 输入 → 输出标准化字段（id/title/content/source_url）
- `tests/test_dedup_checker_strategies.py`
  - URL 精确命中、标题归一化命中、内容清洗后相似度命中/不命中
- `tests/test_crawler_workflow_decision_fallback.py`
  - LLM 开关关闭时只走规则
  - LLM 开关开启但 CrewAI 抛错时回退规则且流程不中断
- `tests/test_crawler_workflow_dry_run_no_llm.py`
  - dry_run=True 时不调用 LLM（通过 mock/monkeypatch 断言）
- `tests/test_crawler_workflow_publish_payload_contract.py`
  - publish payload 符合 `{"article","page_info","images"}` 结构，且字段满足 CMSAgent.extract_article_payload 的最小输入

### 3) 行为验证（手工）
- `main.py --engine crawler --keyword xxx`：确认 dry-run 下完整跑通且无 LLM 调用
- 配置 `CRAWLER_ENABLE_LLM_DECISION=true` + config 开关后：确认仅此时走 LLM，且异常会回退规则

