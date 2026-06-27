



# 发布前编辑器 (EditorAgent)

## Agent概述

EditorAgent 是内容生产流水线中 WRITE 之后的发布前处理环节，与 SEO Agent 并行运行。负责语法修复、LLM 审校（错别字修正 + 政治审查）、去AI痕迹、Markdown 转 HTML 分段、图片占位符插入和敏感词安全过滤。

## 核心职责

1. **语法修复** — 规则驱动的标点重复、英文拼写错误、中文重复字词自动修正
2. **错别字修正** — LLM 驱动的错别字检测与自动修正，不改变原意
3. **政治审查** — LLM 审查文章是否包含反党反华内容，分 clean/flagged/blocked 三档
4. **去AI痕迹** — LLM 驱动的中文AI写作痕迹检测与改写，去除意义膨胀、宣传腔、公式化结构等痕迹（可选，默认关闭）
5. **MD→HTML** — 将 Markdown 转为分段 HTML（`<p>`、`<h1-h6>`、列表、代码块），CMS 直接可用
6. **图片占位** — `{IMG: 标记}` 转为 `<figure>` 标签，无图则留 slot 待后续填充
7. **敏感词过滤** — 基于 Sensitive-lexicon 2005 词全量扫描，命中则整篇拦截丢弃

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
| llm_review.political_review | 政治审查结果（clean/flagged/blocked） |
| safety_check.passed | 敏感词过滤是否通过 |
| safety_check.matched | 命中的敏感词列表 |

## 处理流程

```
Writer 初稿 (Markdown)
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
HTML 定稿 → IMAGE → CMS 发布
```

## 配置文件

- [[config.yaml]] — LLM 配置、语法开关、安全参数
- [[prompt.md]] — LLM 提示词模板（错别字 + 政治审查）
- [[tools/]] — grammar_checker、sensitive_filter

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
    dry_run=True,  # True: 跳过 LLM，仅做规则处理和语法修复
)
```

## 图片占位符语法

Writer 在 Markdown 中用 `{IMG: 位置标记}` 标注配图位置：

```markdown
## 报考条件

{IMG: requirements_comparison}

报考EMBA需要满足以下条件...
```

EDIT 将其转为：

```html
<figure class="article-image" data-position="requirements_comparison">
  <img src="https://..." alt="报考条件对比表" loading="lazy">
  <figcaption>各校EMBA报考条件一览</figcaption>
</figure>
```

暂无图片时保留为占位 slot，ImageAgent 或 CMS 阶段可后续填充。

## 安全规则

基于 [Sensitive-lexicon](https://github.com/konsheng/Sensitive-lexicon)，加载 5 个词库（政治类型、反动词库、暴恐词库、色情词库、涉枪涉爆），共 2005 词。

敏感词过滤不通过（`safety_check.passed = False`）→ workflow 路由到 ERROR，文章整篇丢弃，不进下游。

## 质量标准

| 维度 | 标准 |
|------|------|
| 错别字 | 0 残留 |
| 政治审查 | clean（允许 flagged 人工复核，blocked 直接拦截） |
| 敏感词 | 0 命中 |
| HTML 格式 | 段落正确分段，标题层级完整，图片占位无遗漏 |

## 与旧版 EditorAgent (v1.0) 的区别

| | v1.0（旧） | v2.0（新） |
|---|---|---|
| 质量打分 | ✅ 5 维度规则评分 | ❌ QualityAgent 负责 |
| 品牌禁用词 | ✅ 检查绝对化表述 | ❌ CMS 发布前处理 |
| SEO 检查 | ✅ 关键词密度/Meta | ❌ SEO Agent 负责 |
| LLM 审校 | 可选（默认关闭） | 默认开启，只做错别字+政治审查 |
| 敏感词过滤 | ❌ | ✅ 2005 词全量扫描 |
| MD→HTML | ❌ | ✅ 自动分段输出 |
| 图片占位 | ❌ | ✅ figure 模板 |
| 管线位置 | WRITE 之后串行 | 与 SEO 并行 |

## 相关文档

- [[../../00-方案概述]]
- [[../../01-Agent架构图]]
- [[../../03-工作流编排]]
- [[../writer_agent/]] — 上游 WriterAgent
- [[../seo_agent/]] — 并列 SEO Agent
- [[../image_agent/]] — 下游 ImageAgent
- [[../cms_agent/]] — 下游 CMSAgent
