# Tasks
- [x] Task 1: 梳理当前 mock 输出问题与目标样例
  - [x] 复查 ResearchAgent 当前 `_mock_outline_sections` 与 `_mock_citations` 的实现，定位机械拼接来源与 citations 混入 sources 的路径
  - [x] 明确要保留/替换的章节模板、主题词提取规则与 citation schema

- [x] Task 2: 优化 mock outline 章节生成（内容质量）
  - [x] 实现“主题词提取”函数：从 `primary_keyword`/`title` 提取自然主题词（EMBA报考条件→EMBA、EMBA和MBA的区别→EMBA和MBA、EMBA院校怎么选→EMBA院校选择）
  - [x] 重写章节模板：避免将主题词/关键词硬拼进所有章节；按角度输出自然标题
  - [x] 确保每个 section 的 `key_points` 非空且语义明确（3–5条），`notes` 固定为 `mock`
  - [x] 保证 `outline.sections` 至少 3 个章节

- [x] Task 3: 统一 citations schema（结构稳定）
  - [x] 修改 mock citations 生成逻辑：不再将 sources 原样拼进 citations
  - [x] citations 每项输出固定字段 `title/url/source/authority/citation/note`
  - [x] 确保 citations 永远是 list，且 item 为 dict 且字段齐全

- [x] Task 4: 补充/修改测试
  - [x] 修改 tests/test_research_agent_contract.py：新增断言章节标题不包含 “EMBA报考条件报考条件”“读EMBA报”
  - [x] 新增断言：citations item schema 统一、字段齐全、无混入 sources 结构

- [x] Task 5: 验证
  - [x] 运行 `.\.venv\Scripts\python.exe -m py_compile agents\research_agent\research_agent.py`
  - [x] 运行 `.\.venv\Scripts\python.exe -m pytest tests\test_research_agent_contract.py -q`
  - [x] 运行 `.\.venv\Scripts\python.exe -m pytest -q tests`（回归）

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2 and Task 3
- Task 5 depends on Task 4
