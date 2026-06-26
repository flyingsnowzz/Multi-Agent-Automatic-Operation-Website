# 多Agent自动运营网站

## 工作流

```
                         ┌─────────────┐
                         │  爬虫数据库   │
                         │ publish_date │ ← 全部改为今天(避免时效惩罚)
                         └──────┬──────┘
                                │
                    ┌───────────▼───────────┐
                    │  📊 文章评分 Agent      │
                    │  summarize_crawler_topics │
                    │  (DeepSeek AI 评分)      │
                    │  权重: 标题25% + 通知5%   │
                    │  + 内容重要性60% + 时效10% │
                    └───────────┬───────────┘
                                │
                        overall_score > 75?
                     ┌──────────┴──────────┐
                     │ No                  │ Yes (攒够20篇)
                     ▼                     ▼
                  ❌ discard      ┌─────────────────┐
                                 │  📋 QualityAgent  │
                                 │  写作质量评分      │
                                 └────────┬────────┘
                                          │
                                   quality > 70?
                               ┌──────────┴──────────┐
                               │ Yes                  │ No
                               ▼                      ▼
                          ✅ 通过              ┌──────────────┐
                                              │ 🔍 ResearchAgent │
                                              │ ✍️ WriterAgent   │
                                              └───────┬──────────┘
                                                      │
                                              ┌───────▼──────────┐
                                              │ QualityAgent 复评  │
                                              │ 最多重写 2 次       │
                                              │ 取最高质量分        │
                                              └───────┬──────────┘
                                                      │
                                  ┌────────────────────┘
                                  ▼
                         ┌────────────────┐
                         │  📝 EditorAgent  │
                         │  错别字修正 + 审校 │
                         └───────┬────────┘
                                 │
                        ┌───────▼───────┐
                        │  🔍 SEO Agent  │
                        │       ∥ (并行)  │
                        │  🎨 ImageAgent │
                        └───────────────┘

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 API Key
```

必需配置:
- `DEEPSEEK_API_KEY` / `ARTICLE_SCORING_API_KEY` - DeepSeek API 密钥
- `ARTICLE_SCORING_MODEL=deepseek-chat`
- `ARTICLE_SCORING_BASE_URL=https://api.deepseek.com`

### 2. 准备测试数据库

```bash
# 解析原始 SQL dump，提取文章，修改日期为今天
python3 scripts/parse_sql.py
```

输出: `output/pipeline_batch/articles.json` (1983 篇文章)
测试数据库: `output/pipeline_batch/crawler_data_test_today.sql` (日期已改为今天)

### 3. 运行 Pipeline

```bash
# 全部流程: AI评分 → 质量 → 编辑 → SEO+配图(并行)
python3 scripts/run_pipeline.py

# 或分步: 读取已有评分, 只跑 Phase 2-4
python3 scripts/run_phase2.py
```

### 结果

- `output/pipeline_batch/01_ai_scoring.json` - AI 评分结果
- `output/pipeline_batch/03_final_results.json` - 最终处理结果
- 每篇文章含: `ai_score`, `quality_score`, `rewrite_count`, `image_seo`

## 项目结构

```
agents/          - 各 Agent 实现
  topic_agent/   - 文章评分 Agent (AI)
  quality_agent/ - 写作质量评分 Agent
  research_agent/- 调研 Agent
  writer_agent/  - 写作 Agent
  image_agent/   - 配图 Agent
  seo_agent/     - SEO Agent
  editor_agent/  - 编辑 Agent (错别字修正 + 安全审校)
workflows/       - 工作流编排
scripts/         - 运行脚本
  run_pipeline.py  - Phase 1-4 全流程: AI评分 → 质量 → 编辑 → SEO+配图(并行)
  run_phase2.py    - 读取已有评分结果, 执行 Phase 2-4
config/          - 品牌配置
output/          - 运行输出
sql/             - 数据库迁移 SQL
```
