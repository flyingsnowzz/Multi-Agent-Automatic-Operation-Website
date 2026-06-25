# SEO优化Agent (SEOAgent)

## Agent概述

SEOAgent是多Agent内容生产流水线中的搜索引擎优化环节，负责对文章进行关键词分析、Meta标签生成、Schema结构化数据标记和内链建议。支持V1（传统Python，零LLM调用）和V2（LLM语义识别）双模式。

## 核心职责

1. **关键词分析** - V1用jieba+TF-IDF分析密度和分布；V2用LLM识别主/次/长尾/LSI关键词
2. **Meta标签生成** - 通过LLM生成SEO友好的Meta Title（30-60字符）和Meta Description（120-160字符）
3. **Schema标记生成** - 生成Article/FAQ/HowTo/BreadcrumbList等JSON-LD结构化数据
4. **内链建议** - 基于关键词匹配推荐站内锚文本和链接位置
5. **SEO综合评分** - 输出关键词优化分、Meta标签分、技术SEO分的综合报告

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章标题 | 文章主标题 | WriterAgent |
| 文章内容 | Markdown格式正文 | WriterAgent |
| 主关键词 | 目标SEO关键词 | TopicAgent |
| 页面信息 | URL、分类、标签 | CMSAgent |

## 输出

| 输出项 | 说明 |
|--------|------|
| 关键词分析结果 | 主/次/长尾/LSI关键词、密度、分布、评估 |
| Meta Title | SEO优化标题（含品牌名） |
| Meta Description | 吸引点击的摘要描述 |
| Schema JSON-LD | 结构化数据标记 |
| 内链建议 | 锚文本和链接位置建议 |
| SEO综合报告 | 关键词+Meta+技术SEO评分和优化建议 |

## 双模式

| | V1 (传统Python) | V2 (LLM) |
|---|---|---|
| 关键词识别方式 | jieba分词 + TF-IDF | LLM阅读全文 |
| Token消耗 | 0 | ~800-1500 tokens/篇 |
| 次关键词 | 词频Top5 | 语义级3-5个 |
| 长尾关键词 | 不支持 | 支持2-3个 |
| Meta生成 | LLM | LLM |
| Schema生成 | 规则 | 规则 |

切换方式：`config.yaml` 设置 `keyword_mode: v1` 或 `v2`，或调用时传 `keyword_mode` 参数。

## 工具清单

| 工具 | 文件 | 功能 |
|------|------|------|
| `KeywordAnalyzerV1` | [[tools/keyword_analyzer_v1.py]] | V1: jieba+TF-IDF关键词分析 |
| `KeywordAnalyzerV2` | [[tools/keyword_analyzer_v2.py]] | V2: LLM关键词识别 |
| `MetaGeneratorLLM` | [[tools/meta_generator_llm.py]] | LLM Meta标签生成 |
| `SchemaGenerator` | [[tools/schema_generator.py]] | Schema生成和校验 |
| `ArticleDBReader` | [[tools/db_reader.py]] | 从writer_article_outputs表读文章 |

## 配置文件

- [[config.yaml]] - LLM模型、关键词密度阈值、Meta长度、Schema类型
- [[prompt.md]] - LLM提示词模板

## 运行方式

```python
from agents.seo_agent import SEOAgent

# V1模式（零LLM关键词，推荐默认）
agent = SEOAgent(mode="v1")
result = await agent.execute(
    article={"title": "...", "content_md": "..."},
    topic={"primary_keyword": "EMBA报考条件"},
    dry_run=True,
    keyword_mode="v1"
)

# V2模式（LLM关键词，语义更准）
result = await agent.execute(..., keyword_mode="v2")

# 从数据库批量执行
results = await agent.execute_from_db(limit=10, min_score=70)

# 同步调用
result = agent.execute_sync(article={...}, topic={...})
```

## SEO评分标准

| 维度 | 权重 | 评分说明 |
|------|------|---------|
| 关键词优化 | 40% | 密度、位置、LSI词、分布 |
| Meta优化 | 30% | Title/Description长度和质量 |
| 技术SEO | 30% | Schema完整性、图片Alt、内链 |

## 相关文档

- [[../writer_agent/]] - 上游写作Agent
- [[../image_agent/]] - 上游配图Agent
- [[../cms_agent/]] - 下游CMS Agent
- [[../../00-方案概述]]
