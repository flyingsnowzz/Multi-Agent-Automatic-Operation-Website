



# EditorAgent 说明

## 当前目标

EditorAgent 位于 WriterAgent 和 ImageAgent 之间，与 SEOAgent 并行运行。负责文章的语法修复、LLM 审校（错别字修正 + 政治审查）、去AI痕迹（可选）、Markdown 转 HTML 分段、图片占位符插入和敏感词安全过滤。

## 核心职责

1. **语法修复** — 规则驱动的标点重复、英文拼写错误、中文重复字词自动修正，非 LLM
2. **错别字修正** — LLM 驱动的错别字检测与自动修正，不改变原意
3. **政治审查** — LLM 审查文章是否包含反党反华内容，输出 clean / flagged / blocked 三档
4. **去AI痕迹** — LLM 驱动的中文AI写作痕迹检测与改写，去除意义膨胀、宣传腔、公式化结构、填充词等（可选，`de_ai.enabled: true` 启用）
5. **Markdown → HTML** — 将 Markdown 转为分段 HTML（`<p>`、`<h1-h6>`、列表、代码块），CMS 直接可用
6. **图片占位** — `{IMG: 标记}` 转为 `<figure>` 标签，无图则留 slot 待 ImageAgent 或 CMS 阶段填充
7. **敏感词过滤** — 基于 Sensitive-lexicon 2005 词全量扫描后置过滤，命中则整篇拦截丢弃

## 管线位置

```
RESEARCH → WRITE → (EDIT ∥ SEO) → IMAGE → CMS → EVOLVE
```

EDIT 与 SEO 并行，互不依赖。

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章 Markdown | Writer 产出的初稿 | WriterAgent |
| 图片数据 | 配图 URL/alt/position（可选） | ImageAgent |

## 输出

| 输出项 | 说明 |
|--------|------|
| content_md | 修正后的 Markdown 原文 |
| content_html | 分段 HTML，CMS 直接可用 |
| llm_review.typos_found | 发现的错别字及修正 |
| llm_review.political_review | 政治审查结果（clean / flagged / blocked） |
| safety_check.passed | 敏感词过滤是否通过 |
| safety_check.matched | 命中的敏感词列表 |

## 处理流程

```
WriterAgent 产出文章 (Markdown)
    ↓
语法自动修复（标点/拼写规则）
    ↓
图片占位符 → HTML figure
    ↓
敏感词安全过滤
    ↓
LLM 审校：错别字修正 + 政治审查
    ↓
去AI痕迹（可选，de_ai.enabled=true 时启用）
    ↓
Markdown → HTML 分段
    ↓
HTML 定稿 → ImageAgent → CMSAgent 发布
```

## LLM 配置

| | 说明 |
|---|---|
| 模型 | 默认 gpt-4o，可通过环境变量 `EDITOR_LLM_MODEL` 覆盖 |
| API | OpenAI 兼容接口，`EDITOR_LLM_BASE_URL` 可切 DeepSeek |
| 温度 | 0.2（审校需要低创造性） |
| 开关 | `config.yaml` 中 `llm.enabled` 控制，`dry_run=True` 时跳过 |

## 运行方式

```python
from agents.editor_agent import EditorAgent

agent = EditorAgent()
result = await agent.execute(
    article={
        "title": "EMBA报考指南",
        "content_md": "## 报考条件\n\n...",
    },
    images=[{"position": "chart_1", "url": "https://...", "alt": "对比表"}],
    dry_run=True,  # True: 跳过 LLM，仅做语法修复和敏感词过滤
)
```

## 图片占位符语法

Writer 在 Markdown 中用 `{IMG: 位置标记}` 标注配图位置：

```markdown
## 报考条件

{IMG: requirements_comparison}

报考EMBA需要满足以下条件...
```

EDIT 将其转为 HTML figure，有图片 URL 时填入，无则保留占位 slot。

## 安全规则

基于 [Sensitive-lexicon](https://github.com/konsheng/Sensitive-lexicon)，加载 5 个词库：

| 词库 | 词数 |
|------|------|
| 政治类型 | 326 |
| 反动词库 | 556 |
| 暴恐词库 | 177 |
| 色情词库 | 928 |
| 涉枪涉爆 | 437 |

命中任意词 → `safety_check.passed = false` → workflow 路由到 ERROR，整篇丢弃。

## 与旧版 (v1.0) 的区别

| | v1.0（旧） | v2.0（新） |
|---|---|---|
| 质量打分 | ✅ 5 维度规则评分 | ❌ QualityAgent 负责 |
| 品牌禁用词 | ✅ | ❌ CMS 发布前处理 |
| SEO 检查 | ✅ | ❌ SEOAgent 负责 |
| LLM 审校 | 仅敏感词命中时 | 始终开启，做错别字+政治审查 |
| 敏感词过滤 | ❌ | ✅ 2005 词全量扫描 |
| MD→HTML | ❌ | ✅ 自动分段 |
| 图片占位 | ❌ | ✅ figure 模板 |
| 管线位置 | WRITE 之后串行 | 与 SEO 并行 |

## 相关文件

| 文件 | 说明 |
|------|------|
| `agents/editor_agent/editor_agent.py` | EditorAgent 主类 |
| `agents/editor_agent/config.yaml` | LLM、语法、安全参数配置 |
| `agents/editor_agent/prompt.md` | LLM 提示词模板（错别字 + 政治审查） |
| `agents/editor_agent/SKILL.md` | Agent 概述和工具清单 |
| `agents/editor_agent/tools/grammar_checker.py` | 语法规则检查（中文/英文） |
| `agents/editor_agent/tools/sensitive_filter.py` | 敏感词过滤（正则扫描） |
| `agents/editor_agent/data/sensitive_lexicon/` | 敏感词库（5 文件，2005 词） |
