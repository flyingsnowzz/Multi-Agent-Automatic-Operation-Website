# SEOAgent 说明

## 当前目标

SEOAgent 位于 ImageAgent 和 CMSAgent 之间，负责对文章进行搜索引擎优化。支持 V1（传统 Python，零 LLM 调用）和 V2（LLM 语义识别）双模式，用于对比成本和效果。

## 核心职责

1. **关键词分析** - V1 用 jieba + TF-IDF 计算密度和分布；V2 用 LLM 识别主/次/长尾/LSI 关键词
2. **Meta 标签生成** - 通过 LLM 生成 SEO 友好的 Meta Title（30-60 字符）和 Meta Description（120-160 字符）
3. **Schema 标记** - 生成 Article/FAQ/HowTo/BreadcrumbList JSON-LD 结构化数据
4. **内链建议** - 基于关键词匹配推荐站内锚文本和链接位置
5. **SEO 综合评分** - 关键词优化 40% + Meta 优化 30% + 技术 SEO 30%

## 输入

| 输入项 | 说明 | 来源 |
|--------|------|------|
| 文章标题 | 文章主标题 | WriterAgent |
| 文章内容 | Markdown 格式正文 | WriterAgent |
| 主关键词 | 目标 SEO 关键词 | TopicAgent |
| 页面信息 | URL、分类、标签 | CMSAgent |

## 输出

| 输出项 | 说明 |
|--------|------|
| 关键词分析结果 | 主/次/长尾/LSI 关键词、密度、分布、评估 |
| Meta Title | SEO 优化标题（含品牌名） |
| Meta Description | 吸引点击的摘要描述 |
| Schema JSON-LD | 结构化数据标记 |
| 内链建议 | 锚文本和链接位置 |
| SEO 综合报告 | 关键词 + Meta + 技术 SEO 评分和优化建议 |

## 双模式

| | V1 (传统 Python) | V2 (LLM) |
|---|---|---|
| 关键词识别 | jieba 分词 + TF-IDF | LLM 阅读全文 |
| Token 消耗 | 0 | ~800-1500 tokens/篇 |
| 次关键词 | 词频 Top5 | 语义级 3-5 个 |
| 长尾关键词 | 不支持 | 支持 |
| Meta 生成 | LLM（共用） | LLM（共用） |
| Schema 生成 | 规则 | 规则 |

切换方式：`config.yaml` 设置 `keyword_mode: v1` 或 `v2`，或调用时传 `keyword_mode` 参数。

## 工作流程

```
WriterAgent 产出文章
    ↓
关键词分析（V1 或 V2）
    ↓
LLM 生成 Meta Title + Description
    ↓
Schema 生成 + 校验
    ↓
内链建议
    ↓
SEO 综合报告 + 优化建议
    ↓
CMSAgent 发布
```

## 运行方式

```python
from agents.seo_agent import SEOAgent

# V1 模式（零 LLM 关键词，推荐默认）
agent = SEOAgent(mode="v1")
result = await agent.execute(
    article={"title": "...", "content_md": "..."},
    topic={"primary_keyword": "EMBA报考条件"},
    dry_run=True,
    keyword_mode="v1"
)

# V2 模式（LLM 关键词，语义更准）
result = await agent.execute(..., keyword_mode="v2")

# 从数据库批量执行
results = await agent.execute_from_db(limit=10, min_score=70)
```

## SEO 评分标准

| 维度 | 权重 | 评分说明 |
|------|------|---------|
| 关键词优化 | 40% | 密度、位置、LSI 词、分布 |
| Meta 优化 | 30% | Title 和 Description 长度和质量 |
| 技术 SEO | 30% | Schema 完整性、图片 Alt、内链 |

## 相关文件

| 文件 | 说明 |
|------|------|
| `agents/seo_agent/seo_agent.py` | SEOAgent 主类 |
| `agents/seo_agent/config.yaml` | 关键词密度、Meta 长度、Schema 配置 |
| `agents/seo_agent/SKILL.md` | Agent 概述和工具清单 |
| `agents/seo_agent/tools/keyword_analyzer_v1.py` | V1: jieba + TF-IDF |
| `agents/seo_agent/tools/keyword_analyzer_v2.py` | V2: LLM 关键词识别 |
| `agents/seo_agent/tools/meta_generator_llm.py` | LLM Meta 标签生成 |
| `agents/seo_agent/tools/schema_generator.py` | Schema 生成和校验 |
| `agents/seo_agent/tools/db_reader.py` | writer_article_outputs 表读取 |
