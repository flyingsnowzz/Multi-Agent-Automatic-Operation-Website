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
| `length_score` | 文章长度分 |
| `content_importance_score` | 内容重要性分 |
| `freshness_score` | 时效性分 |
| `topics` | 解释性主题命中，只用于辅助理解，不参与评分 |
| `ai_used` | AI 是否参与本篇评分 |
| `ai_reason` | AI 给出的原因；未参与时为 `null` |

## 综合分权重

| 维度 | 权重 | 评分来源 |
| --- | ---: | --- |
| `title_style_score` | 25% | AI 可参与；无 API 时走代码规则 |
| `length_score` | 20% | 代码规则 |
| `content_importance_score` | 35% | AI 可参与；无 API 时走代码规则 |
| `freshness_score` | 20% | 代码规则 |

## AI 参与规则

AI 当前只参与语义类判断：

- `title_style_score`
- `content_importance_score`
- `reason`

无 API Key 时，AI 不参与评分：

```json
{
  "ai_used": false,
  "ai_reason": null
}
```

不会默认给 AI 满分。

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

## 相关文件

| 文件 | 说明 |
| --- | --- |
| `agents/topic_agent/topic_summary.py` | 文章评分主实现 |
| `agents/topic_agent/prompt.md` | AI 文章评分提示词 |
| `agents/topic_agent/config.yaml` | 评分配置与 topic 解释规则 |
| `tests/test_topic_summary.py` | 核心单元测试 |
| `tests/test_topic_summary_dummy_data.py` | dummy 数据测试 |
| `tmp/article_scoring_crawler_result.json` | crawler 数据评分结果样例 |
