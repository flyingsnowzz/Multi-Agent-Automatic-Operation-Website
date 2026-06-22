# SEOAgent 工具接入与契约统一（计划）

## Summary

目标：修复 `agents/seo_agent` 的核心缺陷，让 SEO 阶段在 CrewAI / LangGraph / Hybrid 工作流中真正产出稳定的 `seo_result`（包含 meta_title/meta_description/schema_json/internal_links 等），并修复关键词分析、Meta 生成、Schema 生成/校验的逻辑与安全问题。

成功标准：
- CrewAI 工作流的 SEOAgent 挂载并使用关键词分析 / Meta 生成 / Schema 生成工具，SEO 任务输出为稳定 JSON 契约。
- LangGraph 工作流的 `_seo_node()` 不再是占位实现：写入 `state["seo_result"]`，且下游可消费。
- Hybrid 工作流的 `_seo_node()` 输出字段与统一契约一致（或可映射到统一契约）。
- `keyword_analyzer`：
  - 中文保留 Markdown 标题结构，H2/H3 能正确识别；
  - 中文关键词提取接入 `jieba`，输出可用关键词/LSI；
  - 英文支持 phrase matching（多词关键词密度统计正确）；
  - issues/warnings/passed_checks 语义正确（“密度适中”不算问题）。
- `meta_generator`：
  - 必然包含主关键词（不依赖 secondary 是否为空）；
  - 修复 value_proposition 提取逻辑；
  - HTML meta 输出做转义，避免注入。
- `schema_generator`：
  - `news/blog` 等类型映射生效；
  - 校验严格：日期/URL/嵌套必填字段不合法则 `valid=false` 并给出 errors，不再仅 warning。
- 覆盖关键单测：中文 H2 识别、多词英文关键词统计、Meta HTML 注入防护、Schema 类型与严格校验、工作流 SEO 节点会产出 seo_result。

约束/决策（已确认）：
- 中文分词方案：引入 `jieba`（会更新 requirements.txt）。
- 输出契约：采用新统一契约（见下文）。
- Schema 校验策略：严格失败（格式/必填不合规 → `valid=false`）。

## Current State Analysis

### 工作流接入缺失
- CrewAI：SEOAgent 未挂载 tools（当前只有 llm，无 tools），见 [crewai_workflow.py:L151-L163](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py#L151-L163)
- LangGraph：SEO 节点是占位实现，仅更新 stage，不产出 `seo_result`，见 [langgraph_workflow.py:L335-L350](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py#L335-L350)
- Hybrid：目前 `_seo_node()` 仍是 LLM 生成（字段与 prompt/config 不一致），见 [hybrid_workflow.py:L338-L368](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L338-L368)

### 工具逻辑与配置脱节
- `agents/seo_agent/config.yaml` 声明大量阈值与行为，但 keyword/meta/schema 工具均使用硬编码规则，未消费 config。
  - 配置范围：[seo_agent/config.yaml:L23-L220](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/config.yaml#L23-L220)

### keyword_analyzer 关键缺陷
- 中文标题结构丢失：`_clean_text()` 会移除 `#`（正则过滤），导致后续标题提取基本找不到，见 [keyword_analyzer.py:L131-L176](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/keyword_analyzer.py#L131-L176)
- 中文“提取关键词”不可用：当前把连续中文字符当一个词，无法分词，见 [keyword_analyzer.py:L247-L268](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/keyword_analyzer.py#L247-L268)
- 英文多词关键词统计错误：主/次关键词按单 token 等值匹配（`w == primary_lower`），见 [keyword_analyzer.py:L96-L105](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/keyword_analyzer.py#L96-L105)
- “关键词密度适中”被放进 issues：语义错误，见 [keyword_analyzer.py:L297-L334](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/keyword_analyzer.py#L297-L334)

### meta_generator 关键缺陷
- value_proposition 提取逻辑错误：循环 `[title] + secondary` 会先把整个标题删除，见 [meta_generator.py:L139-L151](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/meta_generator.py#L139-L151)
- description 不保证包含主关键词：仅在 `secondary` 非空时才补关键词，见 [meta_generator.py:L125-L129](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/meta_generator.py#L125-L129)
- HTML 输出存在注入风险：`generate_html()` 直接拼接 title/meta content，见 [meta_generator.py:L210-L233](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/meta_generator.py#L210-L233)

### schema_generator 关键缺陷
- schema_type 映射未生效：定义了 `news/blog`，但 `generate()` 只处理 article/faq/howto/breadcrumb，其余退回 article，见 [schema_generator.py:L16-L52](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/schema_generator.py#L16-L52)
- 校验过宽：valid 只看 errors 为空；日期/URL/嵌套字段只 warning，见 [schema_generator.py:L198-L247](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/schema_generator.py#L198-L247)

### 输出契约不统一
- prompt.md 输出 `meta_tags.title/description` 与 `schema_markup`（字符串），见 [seo_agent/prompt.md:L92-L121](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/prompt.md#L92-L121)
- meta_generator 输出 `meta_title/meta_description`，见 [meta_generator.py:L63-L71](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/meta_generator.py#L63-L71)
- Hybrid 工作流期望 `meta_title/meta_description/schema_json/internal_links`，见 [hybrid_workflow.py:L348-L354](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py#L348-L354)

## Proposed Changes

### A) 统一输出契约（跨 prompt/config/tools/workflow）

统一 SEO 输出 JSON 结构（最终对外产物 `seo_result`）：

```json
{
  "optimized_article": {"title": "...", "content": "..."},
  "meta_title": "...",
  "meta_description": "...",
  "og_tags": {},
  "twitter_tags": {},
  "schema_json": {},
  "internal_links": [],
  "seo_report": {},
  "improvement_suggestions": []
}
```

修改点：
- [agents/seo_agent/prompt.md](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/prompt.md)
  - 输出示例改为上述字段；
  - 不再输出 `schema_markup` 字符串，改为 `schema_json`（对象），避免下游重复解析/注入风险。
- [agents/seo_agent/config.yaml](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/config.yaml)
  - `output.output_fields` 与统一契约对齐；
  - 作为工具默认参数来源（keyword_density/meta/schema/internal_links/scoring 等）。

### B) 工作流接入（让 SEO 阶段真实产出）

1) CrewAI 工作流
- 文件：[workflows/crewai_workflow.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/crewai_workflow.py)
- 改动：
  - SEOAgent 创建时注入 tools：`get_keyword_analyzer_tool()`、`get_meta_generator_tool()`、`get_schema_generator_tool()`；
  - SEO 任务描述明确要求必须调用这些工具，最终输出统一契约 JSON。

2) LangGraph 工作流
- 文件：[workflows/langgraph_workflow.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/langgraph_workflow.py)
- 改动：
  - 把 `_seo_node()` 从占位实现升级为：读取 `agents/seo_agent/prompt.md` → 注入文章/关键词 → LLM → 解析 JSON → 写入 `state["seo_result"]`；
  - 引入与 research 节点一致的 JSON 容错提取（避免 ```json 包裹导致解析失败）；
  - 保证 `state["current_stage"]` 与 `state["error"]` 逻辑不变。

3) Hybrid 工作流
- 文件：[workflows/hybrid_workflow.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/workflows/hybrid_workflow.py)
- 改动：
  - `_seo_node()` prompt 改为要求输出统一契约；
  - CMS 节点消费 `meta_title/meta_description/schema_json/internal_links` 时不再依赖旧字段名。

### C) keyword_analyzer：结构保留 + jieba 分词 + phrase matching + 正确评估语义

文件：[keyword_analyzer.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/keyword_analyzer.py)

改动：
- 配置读取：
  - 新增读取 `agents/seo_agent/config.yaml`（沿用 CMSAgent/ImageGenerator 的 yaml 读取风格：safe_load + 环境变量解析）；
  - 密度阈值、H2 要求等来自配置（`seo.keyword_density`、`seo.keyword_placement`）。
- 中文结构保留：
  - 将“标题识别”放在清理前（或在清理函数中保留 `#` 与换行），保证 `re.findall(r'#{1,6}...)` 可命中；
  - 分布分析改为同时检查 title/h1/h2/h3 的命中数量与位置。
- 中文关键词提取：
  - 接入 `jieba.lcut` 得到词序列；
  - 过滤停用词、长度、数字等；
  - top_n 输出为真正词项，而不是整句。
- 英文 phrase matching：
  - 对 primary/secondary 的密度统计改为：在 lower content 上用边界安全的短语匹配（例如 regex 或滑窗 n-gram）；
  - 保留 `primary_count`（短语出现次数）与 token 总数。
- LSI 关键词：
  - 第一阶段实现为：基于分词后的词频 + 与主关键词的共现窗口统计（而不是简单“高频排除包含主关键词”）；
  - 输出结构应与 config `lsi_words` 目标一致（每个主关键词至少若干个候选）。
- 评估字段语义：
  - `issues` 只放问题；
  - 新增/或复用 `passed_checks` 表示通过项；
  - “关键词密度适中”应进入 passed_checks 或不输出。

并更新 tool 输出 JSON，确保下游可用于 seo_report。

### D) meta_generator：配置化 + 修复 value_proposition + 强制包含主关键词 + HTML 安全

文件：[meta_generator.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/meta_generator.py)

改动：
- 从 `seo_agent/config.yaml` 读取：
  - title/description 长度阈值；
  - 品牌名/品牌拼接位置与格式；
  - og.enabled/og.type 等。
- 修复 `_extract_value_proposition()`：
  - 不再把整个标题从自身删除；
  - 改为：从标题里移除 keyword（主关键词）与次关键词（secondary），提取剩余的中文短语/数字卖点（如“2026”“5个维度”“最全解读”）。
- description 必含主关键词：
  - 无论 secondary 是否为空，只要 description 不含主关键词，就在开头或前半段自然插入。
- `generate_html()` 做转义：
  - 对 `<title>` 文本与 `<meta content="">` 做 `html.escape(..., quote=True)`；
  - 保证任何引号/尖括号不会污染 HTML。

### E) schema_generator：类型映射生效 + 严格校验 + 输出 schema_json（对象）

文件：[schema_generator.py](file:///d:/%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E8%87%AA%E5%8A%A8%E6%93%8D%E4%BD%9C%E7%BD%91%E7%AB%99/Multi-Agent-Automatic-Operation-Website/agents/seo_agent/tools/schema_generator.py)

改动：
- `generate(schema_type)` 支持：article/blog/news/faq/howto/breadcrumb（blog/news 不再 fallback 到 Article）。
- `@type` 输出使用 schema_types 映射（BlogPosting/NewsArticle）。
- `validate_schema()` 严格化：
  - Article/BlogPosting/NewsArticle：headline、author.name、publisher.name、publisher.logo.url、datePublished 必填；
  - datePublished/dateModified 必须符合 ISO 8601（不可解析 → error）；
  - image/url/mainEntityOfPage.@id 必须为绝对 URL（非 http/https → error）；
  - headline/description 长度按 meta 配置阈值或合理上限校验。
- `get_schema_generator_tool()` 输出调整：
  - 输出 `schema_json`（dict）与 `validation`（严格 errors）；
  - 如需 HTML 片段，输出 `html` 但必须基于 `json.dumps` 的安全序列化（不拼接用户文本）。

### F) 新增/调整依赖

- `requirements.txt` 增加 `jieba`。
- 不新增其它依赖；英文短语匹配与 RSS/JSON 处理使用标准库。

### G) 补测试

新增测试文件（示例命名）：
- `tests/test_seo_agent_keyword_analyzer.py`
  - 中文 H2 识别不丢失；
  - jieba 分词后关键词列表非整句；
  - 英文多词关键词（phrase）计数正确；
  - issues/passed_checks 语义正确。
- `tests/test_seo_agent_meta_generator.py`
  - description 必含主关键词；
  - generate_html 转义可防注入。
- `tests/test_seo_agent_schema_generator.py`
  - blog/news 类型输出正确；
  - 非法日期/相对 URL → valid=false 且 errors 非空。
- `tests/test_workflow_seo_result_presence.py`
  - LangGraph SEO 节点写入 `seo_result`；
  - CrewAI SEOAgent tools 挂载（可用 import + Agent 构造层面验证）。

## Verification Steps

本地验证（使用项目 venv）：
- 语法检查：`.\venv\Scripts\python.exe -m py_compile` 覆盖 seo_agent/tools 与 workflows 修改文件。
- 单测：`.\venv\Scripts\python.exe -m unittest` 跑新增测试文件。
- 轻量运行验证：
  - 运行 HybridWorkflow（不触发真实 CMS 发布）确保 seo_result 结构完整；
  - 运行 LangGraph workflow 观察 research→write→edit→seo 阶段 state 中 seo_result 不是 None。

