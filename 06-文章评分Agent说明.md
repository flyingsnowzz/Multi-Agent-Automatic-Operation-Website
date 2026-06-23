# 文章评分 Agent 说明

## 当前目标

文章评分 Agent 用于遍历 crawler 文章，判断每篇文章是否值得进入后续内容生产流程。

当前只做文章评分，不做 topic 排名，也不再给主题打分。

## 输出结果

主输出字段：

| 字段 | 说明 |
| --- | --- |
| `article_scores` | 每篇文章的评分明细列表 |
| `summary` | 总文章数、评分数、AI 参与数量 |

单篇文章评分字段：

| 字段 | 说明 |
| --- | --- |
| `overall_score` | 综合分 |
| `title_style_score` | 标题风格分 |
| `is_notice` | AI 判断是否为通知/公告类内容 |
| `notice_score` | 通知分；通知/公告为 0，新闻/动态/解读类为 100 |
| `length_score` | 文章长度分 |
| `raw_content_importance_score` | AI 阅读全文后的原始内容重要性分 |
| `content_importance_score` | 乘以时效系数后的内容重要性分 |
| `freshness_score` | 时效性分 |
| `freshness_factor` | 时效惩罚系数，用来折算内容重要性 |
| `freshness_weight_active` | 时效分是否参与综合分；两个月内为 `false` |
| `topics` | 解释性主题命中，只用于辅助理解，不参与评分 |
| `ai_used` | AI 是否参与本篇评分 |
| `ai_reason` | AI 给出的原因；未参与时为 `null` |

## 综合分权重

| 维度 | 权重 | 评分来源 |
| --- | ---: | --- |
| `title_style_score` | 25% | AI 必须参与；无 API 时为 `null` |
| `notice_score` | 5% | AI 判断是否通知；通知为 0，非通知为 100 |
| `length_score` | 10% | 代码规则，温和惩罚；短文约 60 分，理想正文约 70-80 分 |
| `content_importance_score` | 50% | AI 阅读全文后的原始重要性乘以时效系数 |
| `freshness_score` | 10% | 代码规则；两个月内不参与综合分 |

`content_importance_score` 不只看招生/考试/政策信息，也看内容传播价值。教授、学生、校友的人物故事，科研突破背后的故事，成长经历、心路历程、团队奋斗过程、项目发展故事等，只要具备叙事性、人物性、情绪价值或用户愿意阅读的传播性，也可以获得较高重要性分。

两个月内的文章不计算时效分权重，其他维度会自动归一化，重要性系数为 `1.0`。2-6 个月时效分从 100 线性降到 80，重要性系数为 `0.8`；6-12 个月时效分从 80 线性降到 60，重要性系数为 `0.5`；12-24 个月时效分从 60 线性降到 30，重要性系数为 `0.5`；24-36 个月时效分从 30 线性降到 0，36 个月后时效分为 0，24 个月后重要性系数统一为 `0.1`。最终重要性 = AI 原始重要性 × `freshness_factor`。

长度维度只占 10%，不做重惩罚。当前 crawler 数据多数文章只有很短的 `description`，因此短文会保留约 60 分基线；后续如果接入正文，200-1800 字正文会落在约 70-80 分区间，超长文章再缓慢降分。

## AI 参与规则

AI 当前只参与语义类判断：

- `title_style_score`
- `content_importance_score`
- `is_notice`
- `reason`

无 API Key 或 AI 返回不完整时，语义维度和综合分不再用代码规则兜底：

```json
{
  "overall_score": null,
  "title_style_score": null,
  "content_importance_score": null,
  "is_notice": null,
  "notice_score": null,
  "ai_used": false,
  "ai_reason": null
}
```

不会默认给 AI 满分，也不会用代码规则替代 AI 语义评分。

### DeepSeek 接入方式

DeepSeek 使用 OpenAI-compatible 接口时，在本地 `.env` 中配置：

```env
ARTICLE_SCORING_API_KEY=你的_deepseek_key
ARTICLE_SCORING_MODEL=deepseek-chat
ARTICLE_SCORING_BASE_URL=https://api.deepseek.com
```

也可以只配置：

```env
DEEPSEEK_API_KEY=你的_deepseek_key
ARTICLE_SCORING_MODEL=deepseek-chat
ARTICLE_SCORING_BASE_URL=https://api.deepseek.com
```

运行评分时传入 `use_ai=True`，返回中 `ai_used=true` 代表 AI 已介入。

大批量跑分时可以传入并发参数，例如：

```python
summarize_crawler_topics(
    articles,
    use_ai=True,
    ai_config={"concurrency": 6},
)
```

建议从 4-8 并发开始，根据 API 限速和失败率调整。评分结果仍可用同一套 `article_*` 字段写回数据库。

## 数据库写回

不直接修改原始 `crawler_data.sql`。如果需要把评分写回 MySQL，先执行独立迁移文件：

```sql
source sql/alter_crawler_article_scores.sql;
source sql/alter_crawler_article_scores_v2_notice_freshness.sql;
```

然后使用 `agents.topic_agent.tools.article_score_writer.write_article_scores_to_db` 将 `article_scores` 写回 `crawler_news_main` 的 `article_*` 字段。

## 相关文件

| 文件 | 说明 |
| --- | --- |
| `agents/topic_agent/topic_summary.py` | 文章评分主实现 |
| `agents/topic_agent/tools/article_score_writer.py` | 文章评分数据库写回工具 |
| `agents/topic_agent/prompt.md` | AI 文章评分提示词 |
| `agents/topic_agent/config.yaml` | 评分配置与 topic 解释规则 |
| `sql/alter_crawler_article_scores.sql` | 文章评分字段迁移SQL |
| `sql/alter_crawler_article_scores_v2_notice_freshness.sql` | 通知判断与时效惩罚字段迁移SQL |
| `tests/test_topic_summary.py` | 核心单元测试 |
| `tests/test_topic_summary_dummy_data.py` | dummy 数据测试 |
