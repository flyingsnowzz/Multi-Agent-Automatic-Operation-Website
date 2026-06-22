## Summary

补齐 `agents/data_agent` 的主类与链路，把 DataAgent 从“工具散落/占位采集/静态模板报告”提升为可运行的闭环：

- 新增 `DataAgent.execute(report_type, date_range, sources)`：统一读取配置、选择数据源、采集、对比、异常检测、生成结构化报告（JSON 为权威输出）
- 工作流入口 `run_data_workflow()` 改为调用真实 `DataAgent.execute()`，并按 `agents/data_agent/config.yaml` 的 `data_sources.*.enabled` 选择数据源
- 首个真实数据源落地 **GA4（Google Analytics Data API）**，凭证方式采用 **Service Account JSON**（`GOOGLE_APPLICATION_CREDENTIALS`）
- 修复已知接口缺口：`baidu_index` 参数错位、`compare` action 未实现
- 异常检测按配置阈值输出 anomalies（至少对 GA4 的 sessions/bounce_rate 形成可用结果）
- 报告生成从“静态模板”改为“用真实数据填充”的结构化 JSON + Markdown（HTML 使用正式 Markdown 渲染库，不做字符串替换）

---

## Current State Analysis (Grounded)

### 1) 缺少 DataAgent 主类

`agents/data_agent` 目录目前只有 config/prompt/tools，无 `DataAgent` 统一入口与执行策略。

- 目录：`agents/data_agent/*`

### 2) 工作流入口只采 GA 且不读取配置

`run_data_workflow()` 直接调用 `AnalyticsCollector.collect([DataSource.GOOGLE_ANALYTICS])`，不读取 `config.yaml` 里的启用状态。

- [crewai_workflow.py:L482-L512](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py#L482-L512)
- [config.yaml:L20-L42](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/config.yaml#L20-L42)

### 3) GA4/GSC/百度统计等采集均为占位

`_collect_ga/_collect_gsc/_collect_baidu` 返回 0，并标注“需要配置 API”。

- [analytics_collector.py](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py)

### 4) Baidu index 参数错位 + compare action 缺失

- `collect()` 将日期传给 `_collect_baidu_index(keyword, days)`（参数语义错位）
- tool 文档写 `compare`，实现缺少分支

相关位置：

- [analytics_collector.py:L36-L67](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L36-L67)
- [analytics_collector.py:L299-L353](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L299-L353)

### 5) 异常检测占位、报告模板不填充、HTML 不可靠

- 异常检测 `detect_anomalies()` 总是返回空 anomalies，未用 `config.yaml` 阈值。
- 报告模板仅返回章节结构，不包含采集指标与 TOP 列表。
- HTML 输出是字符串替换，标签不稳定。

相关位置：

- [config.yaml:L88-L99](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/config.yaml#L88-L99)
- [analytics_collector.py:L258-L292](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L258-L292)
- [report_generator.py:L62-L367](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/report_generator.py#L62-L367)

---

## Decisions (Locked)

- 首个真实数据源：**GA4**
- 凭证方式：**Service Account JSON**，由 `GOOGLE_APPLICATION_CREDENTIALS` 指向非仓库目录的 JSON key 文件
- 报告权威输出：**结构化 JSON**
- 统一环境变量命名：后续实现 GA4/GSC 均读取 `GOOGLE_APPLICATION_CREDENTIALS`（不再读 `GA_CREDENTIALS/GSC_CREDENTIALS`）

---

## Proposed Changes

### A) 新增 DataAgent 主类与统一执行入口

**新增文件**

1) `agents/data_agent/__init__.py`
- 导出 `DataAgent`

2) `agents/data_agent/data_agent.py`
- `DataAgent(config_path="agents/data_agent/config.yaml")`
- `async def execute(report_type="daily", date_range=None, sources=None) -> Dict[str,Any]`
- 内部步骤：
  1. 读取 config（支持 `${ENV}` 变量替换）
  2. 解析 report_type 的日期范围（日报/周报/月报）
  3. 按 `data_sources.*.enabled` 选择 sources（允许参数 sources 覆盖）
  4. 调 `AnalyticsCollector.collect()` 拉取数据（GA4 真实，其他源返回 `not_implemented`/或 `missing_credentials`）
  5. 调 `AnalyticsCollector.compare_periods()` 做对比（日报=昨日、周报=上周、月报=上月）
  6. 按 `anomaly_detection.thresholds` 输出 anomalies（首期至少覆盖 GA4：sessions 下跌、bounce_rate 上升）
  7. 调 `ReportGenerator` 生成结构化报告对象（JSON），并可派生 Markdown/HTML

### B) 工作流入口接入 DataAgent，并按配置选择数据源

**修改文件**

- `workflows/crewai_workflow.py`
  - `run_data_workflow(report_type="daily", **_)` 改为：
    - `from agents.data_agent import DataAgent`
    - `return await DataAgent().execute(report_type=report_type)`
  - 保持返回结构：`workflow/report_type/data/report/timestamp`

### C) GA4 真实采集落地（Google Analytics Data API）

**修改文件**

- `agents/data_agent/tools/analytics_collector.py`
  1. 统一 env 读取
     - `GA_PROPERTY_ID`、`GOOGLE_APPLICATION_CREDENTIALS`
     - 删除/兼容旧变量（GA_CREDENTIALS/GSC_CREDENTIALS）但以新变量为准
  2. `_collect_ga(start_date,end_date)` 用 GA Data API 真实实现：
     - client：`google.analytics.data_v1beta.BetaAnalyticsDataClient`
     - auth：`google.oauth2.service_account.Credentials.from_service_account_file(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])`
     - 指标：sessions, screenPageViews(pageviews), totalUsers(users), bounceRate, averageSessionDuration
     - TOP pages：dimension `pagePath` + metric `screenPageViews`（取 Top 10）
     - TOP sources：dimension `sessionSource` + metric `sessions`（取 Top 10）
  3. `collect()` 按 sources 逐个执行：
     - GA4：真实
     - GSC/百度统计/百度指数：首期返回 `{success:false, error:"not_implemented"}` 或 `{success:false, error:"missing_credentials"}`（保持链路完整，不假装 0）
  4. 修复 `BAIDU_INDEX` 参数错位：
     - `collect()` 遇到 `BAIDU_INDEX` 时从 `dimensions` 取 keyword（或 sources 参数携带），或将 `_collect_baidu_index` 改为接收 date_range（择一并在测试覆盖）
  5. `analytics_collector_tool` 增加 `compare` action：
     - 复用 `compare_periods()`（允许传入 current/previous dates；或 DataAgent 统一计算后直接调用 compare_periods）

### D) 异常检测落地（按配置阈值）

**修改文件**

- `agents/data_agent/tools/analytics_collector.py` 或放到 `DataAgent` 中（推荐 DataAgent 负责阈值与业务语义）
  - 输入：`comparison.metrics`（sessions/pageviews/users/bounce_rate）
  - 输出 anomalies：
    - traffic_drop_percent：sessions 或 pageviews 的 change_percent <= -threshold
    - bounce_rate_increase：bounce_rate 的 change_percent >= threshold
    - ranking_drop_positions：首期若无真实排名数据则跳过，并在 anomalies 中写明 `skipped_reason`

### E) 报告生成重写（从模板结构 → 数据填充）

**修改文件**

- `agents/data_agent/tools/report_generator.py`
  1. `generate()` 输出结构化 JSON（权威）：
     - `data_snapshot`: 当前周期指标
     - `comparison`: 同比/环比对比指标
     - `top_pages`: 来自 GA4
     - `top_sources`: 来自 GA4
     - `anomalies`: 来自 DataAgent
     - `recommendations`: 基于 anomalies 与变化趋势给出可执行建议（规则模板 + 允许后续 LLM 增强）
  2. `format_markdown()` 使用上述结构填充表格/列表（不再只打印模板名）
  3. `format_html()` 改为正式 Markdown 渲染库（例如 `markdown`），不再做 replace

### F) 依赖补齐与示例环境变量

**修改文件**

- `requirements.txt` 增加：
  - `google-analytics-data`
  - `google-auth`
  - `google-auth-oauthlib`（可选，未来 OAuth 扩展）
  - `markdown`（用于 HTML 渲染）
- `.env.example`（若存在相关段落）补齐/统一：
  - `GOOGLE_APPLICATION_CREDENTIALS=...`
  - `GA_PROPERTY_ID=...`
  - `GSC_SITE_URL=...`（为后续 GSC 预留）

---

## Testing & Verification

1) 编译检查
- `venv\\Scripts\\python.exe -m compileall agents workflows tests`

2) 单元测试（新增）
- Mock GA4 client（不访问外网）
  - 验证 `_collect_ga()` 会构造正确的 RunReportRequest（metrics/dimensions/date_range）
  - 验证 DataAgent 会按 config.enabled 选择 sources
  - 验证 compare 计算：daily/weekly/monthly 的 current/previous 日期范围
  - 验证 anomaly_detection：触发 traffic_drop/bounce_increase 输出

3) 联调（需要真实 GA4 授权）
- 设置 `.env`：
  - `GOOGLE_APPLICATION_CREDENTIALS=D:\\secrets\\xxx.json`
  - `GA_PROPERTY_ID=xxxx`
- 运行 `run_data_workflow()` 或直接 `DataAgent.execute()`，确认：
  - 指标非 0（有数据时）
  - top_pages/top_sources 有返回
  - compare/anomalies/recommendations 字段完整

