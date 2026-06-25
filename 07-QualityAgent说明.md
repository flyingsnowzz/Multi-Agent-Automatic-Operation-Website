# QualityAgent 说明

QualityAgent 用来评价文章整体写作质量，而不是评价选题价值。

文章评分 Agent 回答：

- 这件事重要吗？
- 这篇素材值不值得做？
- 是否有时效性、传播价值和内容重要性？

QualityAgent 回答：

- 字数是否合适？
- 文字是否流畅？
- 结构是否清楚？
- 标题、开头和正文是否有吸引力？
- 普通人粗看是否能感觉这像 AI 写的？

## 流程位置

```text
文章评分 Agent
  ↓
article_score > 75
  ↓
QualityAgent 首次质量评分
  ↓
quality_score < 70
  ↓
ResearchAgent 接收扣分点和 rewrite_feedback_prompt
  ↓
WriterAgent 重写
  ↓
QualityAgent 再评分，if_ai_generated=true
  ↓
quality_score < 85 时继续重写，最多重写1次
  ↓
quality_score >= 85 或超过重写次数
  ↓
进入后续发布候选库
```

## 两种评分语境

### 首次考核 crawler 原文

`if_ai_generated=false`

原文通常来自 crawler 或已有站点，不默认认为是 AI 生成。此时 AI 味只作为较小权重参考。

| 维度 | 权重 | 来源 |
| --- | ---: | --- |
| `word_count_score` | 20% | 代码计算 |
| `fluency_score` | 25% | 大模型 |
| `structure_score` | 20% | 大模型 |
| `attractiveness_score` | 25% | 大模型 |
| `ai_feel_score` | 10% | 大模型返回 AI 概率后反向折算 |

### WriterAgent 生成稿/重写稿

`if_ai_generated=true`

生成稿最需要防止“普通人粗看就觉得像 AI 写的”。因此 AI 味权重更高。

| 维度 | 权重 | 来源 |
| --- | ---: | --- |
| `word_count_score` | 10% | 代码计算 |
| `fluency_score` | 20% | 大模型 |
| `structure_score` | 20% | 大模型 |
| `attractiveness_score` | 20% | 大模型 |
| `ai_feel_score` | 30% | 大模型返回 AI 概率后反向折算 |

## 维度说明

| 字段 | 说明 |
| --- | --- |
| `word_count_score` | 字数分，代码规则计算；目标通常为 900-1200 字 |
| `fluency_score` | 文字是否自然流畅 |
| `structure_score` | 开头、展开、背景、重点、结尾是否清楚 |
| `attractiveness_score` | 标题、开头和内容是否有阅读吸引力 |
| `ai_generated_probability` | 普通人不仔细读时发现 AI 味的概率，越高越糟糕 |
| `ai_feel_score` | `100 - ai_generated_probability`，越高越不像 AI |
| `quality_score` | 按当前语境权重计算后的综合质量分 |
| `rewrite_feedback_prompt` | 给 ResearchAgent/WriterAgent 的扣分点和改进提示 |

除 `word_count_score` 外，其他质量维度都调用大模型判断。

## 路由规则

| 条件 | 下一步 |
| --- | --- |
| `article_score <= 75` | 不进入 QualityAgent |
| `article_score > 75` | 进入 QualityAgent |
| 原文 `quality_score < 70` | 进入 ResearchAgent + WriterAgent |
| 原文 `70 <= quality_score < 85` | 人工审核或轻改 |
| 原文 `quality_score >= 85` | 可直接进入发布候选或人工终审 |
| Writer 输出 `quality_score < 85` | 继续重写/重跑 WriterAgent |
| Writer 输出 `quality_score >= 85` | 通过质量门槛 |

## 扣分反馈

QualityAgent 会把低分维度转成 `rewrite_feedback_prompt`。

例如：

```text
请把以下 QualityAgent 扣分点作为下一轮 ResearchAgent/WriterAgent 的硬约束：
- 结构组织不够清楚：62分
- AI味较明显：55分
主要原因：段落推进较机械；结尾有空泛升华
修改建议：减少路标句；把背景压缩到第二段；增加具体事实支撑
```

ResearchAgent 后续应读取这个字段，把扣分点写进 `writer_prompt`，让 WriterAgent 有针对性地改，而不是盲目重写。

## 数据库

建表文件：

```bash
mysql -uroot < sql/create_article_quality_scores.sql
```

评分结果写入：

```text
research_article_data.article_quality_scores
```

关键字段：

| 字段 | 说明 |
| --- | --- |
| `source_kind` | `original`、`writer` 或 `writer_plain` |
| `candidate_id` | 对应 `research_article_candidates.id` |
| `writer_output_id` | Writer 输出表 ID |
| `article_score` | 文章评分 Agent 的选题价值分 |
| `if_ai_generated` | 是否来自 WriterAgent/重写链路 |
| `quality_score` | QualityAgent 综合质量分 |
| `word_count_score` | 代码计算的字数质量分 |
| `ai_generated_probability` | 普通人粗看发现 AI 味的概率 |
| `ai_feel_score` | 低 AI 味质量分 |
| `rewrite_feedback_prompt` | 交给 ResearchAgent/WriterAgent 的改进提示 |
| `route` | `needs_research_writer` / `manual_review` / `ready_to_store` |
| `quality_payload` | 完整 JSON 评分结果 |

## 运行

给原文评分：

```bash
export QUALITY_AGENT_API_KEY="你的 API Key"
export QUALITY_AGENT_MODEL="deepseek-chat"
export QUALITY_AGENT_BASE_URL="https://api.deepseek.com"

python3 scripts/run_quality_agent_from_db.py \
  --source-kind original \
  --limit 5 \
  --concurrency 1 \
  --loop
```

给 WriterAgent 生成稿评分：

```bash
python3 scripts/run_quality_agent_from_db.py \
  --source-kind writer \
  --limit 5 \
  --concurrency 1 \
  --loop
```

让 WriterAgent 在质量低于 85 时继续重写，最多重写1次：

```bash
export WRITER_AGENT_API_KEY="你的 API Key"
export WRITER_AGENT_MODEL="deepseek-chat"
export WRITER_AGENT_BASE_URL="https://api.deepseek.com"

python3 scripts/run_writer_quality_retry_loop.py \
  --target-quality 85 \
  --max-attempts 5 \
  --limit 10 \
  --concurrency 2
```

如果想看同一批 WriterAgent 生成稿在“不按 AI 生成稿惩罚”的纯质量分，可以用 `writer_plain` 重新评分。它会写入同一张表，但 `source_kind='writer_plain'`，不会覆盖正常的 `writer` 评分。

```bash
python3 scripts/run_quality_agent_from_db.py \
  --source-kind writer_plain \
  --limit 5 \
  --concurrency 1 \
  --loop
```

查看需要进入 ResearchAgent + WriterAgent 的原文：

```sql
SELECT candidate_id, title, article_score, quality_score, rewrite_feedback_prompt
FROM research_article_data.article_quality_scores
WHERE source_kind = 'original'
  AND article_score > 75
  AND quality_score < 70
ORDER BY quality_score ASC;
```

查看通过质量门槛的生成稿：

```sql
SELECT candidate_id, title, quality_score, ai_generated_probability, rewrite_feedback_prompt
FROM research_article_data.article_quality_scores
WHERE source_kind = 'writer'
  AND quality_score >= 85
ORDER BY quality_score DESC;
```
