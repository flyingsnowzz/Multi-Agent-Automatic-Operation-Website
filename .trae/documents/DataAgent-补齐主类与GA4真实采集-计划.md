## Summary

把 `agents/data_agent` 从“工具散落 + 占位采集 + 静态模板报告”补齐为可运行的最小闭环：

- 新增 `DataAgent.execute()`：统一读取配置、按启用状态选择数据源、计算周期与对比周期、采集数据、异常检测、生成结构化报告与建议
- 工作流入口 `run_data_workflow()` 改为调用真实 `DataAgent.execute()`（不再直连 `AnalyticsCollector/ReportGenerator`）
- 首个真实数据源落地：GA4（Google Analytics Data API），凭证采用 Service Account JSON，使用 `GOOGLE_APPLICATION_CREDENTIALS`（与现有 `config.yaml` 一致）
- 修复/补齐：`baidu_index` 参数错位、`analytics_collector` tool 的 `compare` action 缺失、异常检测占位、HTML 渲染不稳定
- 本次范围内：异常只“产出 anomalies/notifications 结构”，不发送 wechat/email（与用户确认一致）

---

## Current State Analysis (Grounded)

### 1) 缺少 DataAgent 主类

`agents/data_agent` 目录只有 config/prompt/tools，没有可导入的 `DataAgent` 主类统一执行入口。

- 目录：[agents/data_agent](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent)

### 2) 工作流入口不读取配置，只采 GA 且采集周期固定为“当天”

`run_data_workflow()` 直接调用 `AnalyticsCollector.collect([DataSource.GOOGLE_ANALYTICS])`，没有按 `agents/data_agent/config.yaml` 的 `data_sources.*.enabled` 选择来源。

- [crewai_workflow.py:L482-L512](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py#L482-L512)
- [config.yaml:L20-L42](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/config.yaml#L20-L42)

### 3) GA4 / GSC / 百度统计采集均为占位

`_collect_ga/_collect_gsc/_collect_baidu` 当前只做“凭证是否存在”的检查，然后返回全 0 数据。

- [analytics_collector.py:L69-L151](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L69-L151)

此外，环境变量读取与配置不一致：代码读 `GA_CREDENTIALS/GSC_CREDENTIALS`，而配置与目标统一为 `GOOGLE_APPLICATION_CREDENTIALS`。

- [analytics_collector.py:L30-L35](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L30-L35)
- [config.yaml:L21-L29](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/config.yaml#L21-L29)

### 4) Baidu index 参数错位 + compare action 文档与实现不一致

- `collect()` 里调用 `_collect_baidu_index(start_date, end_date)`，但函数签名是 `(keyword, days=30)`，导致日期被当成 keyword。
- tool doc 写了 `compare`，实现缺少对应分支。

- [analytics_collector.py:L36-L67](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L36-L67)
- [analytics_collector.py:L153-L165](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L153-L165)
- [analytics_collector.py:L299-L353](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L299-L353)

### 5) 异常检测占位、报告模板不填充、HTML 输出不可靠

- `detect_anomalies()` 直接返回空 anomalies，未使用 `config.yaml` 里的阈值
- 报告模板基本只有“章节名/指标名”，没有填充采集结果
- HTML 是字符串替换模拟 Markdown 渲染，结构容易错乱

- [config.yaml:L88-L99](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/config.yaml#L88-L99)
- [analytics_collector.py:L258-L292](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/analytics_collector.py#L258-L292)
- [report_generator.py:L23-L367](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/data_agent/tools/report_generator.py#L23-L367)

---

## Decisions (Locked)

- 周期定义：周报默认统计“上周”，月报默认统计“上月”（与调度器周一/每月1号触发更匹配）
- GA4 真实采集方式：REST + google-auth（service account 刷 access token，再用 httpx 调 GA Data API）
- 告警：本次只产出 anomalies/notifications，不发送 wechat/email

---

## Proposed Changes (Decision-Complete)

### A) 新增 DataAgent 主类与统一执行入口

**新增文件**

1) `agents/data_agent/__init__.py`
- 导出 `DataAgent`，保持与其他 Agent 一致的可导入体验

2) `agents/data_agent/data_agent.py`
- 参考 [CMSAgent](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/cms_agent/cms_agent.py) 与 [CompetitorAgent](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/agents/competitor_agent/competitor_agent.py) 的模式：
  - `_deep_env_resolve()`：支持 `${ENV}` 变量替换
  - `_load_config()`：读取 `agents/data_agent/config.yaml`
- `async def execute(self, report_type: str = "daily", date_range: Optional[Dict[str, str]] = None, sources: Optional[List[str]] = None) -> Dict[str, Any]`
  - `report_type`：daily/weekly/monthly
  - `date_range`：可选，`{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`（覆盖默认周期）
  - `sources`：可选，覆盖配置选择（例如 `["ga","gsc"]`），否则按 `config.data_sources.*.enabled`

**execute() 内部职责**

- 周期计算（当 `date_range` 未提供时）：
  - daily：start=end=today
  - weekly：统计上周（周一~周日），对比再上一周
  - monthly：统计上月（1号~月末），对比再上月
- 数据源选择：
  - 仅把 enabled=true 且有实现的源加入 `sources_to_collect`
  - 对 enabled=true 但未实现（例如 ahrefs/semrush）的源写入 `errors`，不 silent ignore
- 采集与对比：
  - 使用 `AnalyticsCollector.collect()` 获取 current period 数据
  - 再次调用 `collect()` 获取 previous period 数据
  - 计算 `comparison.metrics`（至少覆盖 GA4：sessions/pageviews/users/bounce_rate）
- 异常检测（按配置阈值）：
  - traffic_drop_percent：sessions 或 pageviews 的 change_percent <= -threshold
  - bounce_rate_increase：bounce_rate 的 change_percent >= threshold
  - ranking_drop_positions：若本次未实现 GSC，输出 `skipped` 的 anomaly item（可追踪但不误报）
- 报告生成：
  - 调 `ReportGenerator.generate_structured(...)`（见后续改造）生成权威 JSON 报告
  - 可选派生 `markdown/html`（HTML 用正式 Markdown 渲染库）

### B) 工作流入口接入 DataAgent

**修改文件**

- `workflows/crewai_workflow.py`
  - 将 `run_data_workflow()` 改为：
    - `from agents.data_agent import DataAgent`
    - `return await DataAgent().execute(report_type=report_type)`
  - 保持返回结构：`workflow/report_type/result/timestamp`（避免调度器记录字段变动过大）

参考当前实现位置：
- [crewai_workflow.py:L482-L512](file:///d:/多智能体自动操作网站/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py#L482-L512)

### C) GA4 真实采集落地（Google Analytics Data API）

**修改文件**

- `agents/data_agent/tools/analytics_collector.py`

1) 统一凭证读取
- 使用 `GOOGLE_APPLICATION_CREDENTIALS` 作为 Service Account JSON 文件路径
- 保留 `GA_CREDENTIALS/GSC_CREDENTIALS` 作为兼容读取（若存在且新变量为空），但对外文档与配置只推荐新变量

2) `_collect_ga(start_date, end_date)` 真实实现（REST + google-auth）
- 步骤：
  - 从 `GOOGLE_APPLICATION_CREDENTIALS` 加载 service account
  - 刷新得到 access token（scope `https://www.googleapis.com/auth/analytics.readonly`）
  - `POST https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport`
- 输出字段对齐现有结构（避免大面积改调用方）：
  - sessions
  - pageviews（GA4 对应 metric：`screenPageViews`）
  - users（`totalUsers`）
  - bounce_rate（`bounceRate`）
  - avg_session_duration（优先 `averageSessionDuration`，若 API 报 metric 不支持则降级为 `averageEngagementTime` 或置空并返回 warning）
  - top_pages：dimension `pagePath` + metric `screenPageViews`（Top 10）
  - top_sources：dimension `sessionSource` + metric `sessions`（Top 10）

3) `collect()` 的返回约定调整（避免“假 0”）
- 对未实现源（gsc/baidu/baidu_index/trends）：返回 `success: false` + `error: "not_implemented"` 或 `error: "missing_credentials"`，不再返回“success=true 但全 0”

4) 修复 Baidu index 参数错位
- 方案：让 `_collect_baidu_index()` 接收 `start_date/end_date`（与 `collect()` 调用一致），并允许从 `dimensions` 或额外参数获取 keyword
- 同步调整 `DataSource.BAIDU_INDEX` 的处理分支，确保不会把日期当 keyword

### D) compare action 补齐

**修改文件**

- `agents/data_agent/tools/analytics_collector.py`
  - `get_analytics_collector_tool()` 增加 `elif action == "compare": ...`
  - tool 参数扩展（向后兼容）：
    - 允许额外传 `previous_start_date/previous_end_date`
    - 若未传 previous，则按 current 区间长度自动回推一段作为 previous

### E) 异常检测从占位改为阈值化输出

**修改位置选择**

- 推荐把“阈值语义 + 业务异常类型”放在 `DataAgent`（而不是 `AnalyticsCollector.detect_anomalies()`），避免采集层绑定业务策略
- `AnalyticsCollector.detect_anomalies()` 保留为通用接口，但默认由 DataAgent 计算后直接返回结构（后续可删除或改为调用 DataAgent）

输出 schema（供 ReportGenerator 与上层消费）：

- `anomalies: List[Dict]`：
  - `type`: traffic_drop | bounce_rate_increase | ranking_drop | skipped
  - `severity`: low | medium | high
  - `metric`: sessions | pageviews | bounce_rate | position
  - `current/previous/change_percent`（可用时）
  - `description`
  - `suggestions: List[str]`

### F) 报告生成重写：以结构化 JSON 为权威输出

**修改文件**

- `agents/data_agent/tools/report_generator.py`

1) 保留 `ReportGenerator` 类，但新增结构化生成入口（避免把旧的模板结构继续当“报告”）
- `generate_structured(report_type, *, current, previous, comparison, anomalies, recommendations, meta) -> Dict[str, Any]`

2) Markdown 生成改为“真实填充”
- `format_markdown(structured_report)`：
  - 核心指标表格（sessions/pageviews/users/bounce_rate）
  - Top pages / Top sources 列表
  - anomalies 列表（含建议）

3) HTML 渲染改为正式 Markdown 渲染库
- `format_html()` 使用 `markdown` 库把 `format_markdown()` 的结果渲染为 HTML（不再做字符串 replace）

### G) 依赖与示例环境变量补齐

**修改文件**

1) `requirements.txt`
- 增加（最小集）：
  - `google-auth>=2.0.0`
  - `markdown>=3.6`

2) `.env.example`
- 增加 data 侧示例（不放真实路径/密钥）：
  - `GOOGLE_APPLICATION_CREDENTIALS=path_to_service_account.json`
  - `GA_PROPERTY_ID=your_ga4_property_id`
  - `GSC_SITE_URL=https://example.com/`（为后续预留）
  - `BAIDU_TOKEN=...`、`BAIDU_SITE_ID=...`（为后续预留）

---

## Testing & Verification

### 1) 静态检查

- `venv\\Scripts\\python.exe -m py_compile agents\\data_agent\\tools\\analytics_collector.py agents\\data_agent\\tools\\report_generator.py`
- `venv\\Scripts\\python.exe -m compileall agents workflows tests`

### 2) 单元测试（新增，默认不访问外网）

- `tests/test_data_agent_date_ranges.py`
  - daily/weekly(月报=上月) 的默认日期范围与 previous 范围计算
- `tests/test_data_agent_source_selection.py`
  - 按 `config.yaml` 的 enabled 选择 sources；对 enabled 但未实现源进入 errors
- `tests/test_analytics_collector_tool_compare.py`
  - `action="compare"` 分支存在，且 previous 默认回推逻辑正确
- `tests/test_report_generator_structured.py`
  - 结构化报告字段齐全，markdown/html 渲染不崩溃

### 3) 手工联调（需要真实 GA4 授权）

- 设置环境变量：
  - `GOOGLE_APPLICATION_CREDENTIALS=D:\\secrets\\service_account.json`
  - `GA_PROPERTY_ID=xxxx`
- 运行：
  - 调度器：触发 `daily_data_collection` 或调用 `run_data_workflow(report_type="daily")`
  - 预期：GA4 指标与 top_pages/top_sources 非空（有数据时），comparison/anomalies/recommendations 字段齐全

