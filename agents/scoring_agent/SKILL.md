# ScoringAgent

## What This Module Does

ScoringAgent is the first AI gate in the formal LangGraph pipeline.

It answers one question:

> Is this source article valuable enough to spend QualityAgent / rewrite / SEO / image / CMS cost on?

It does not publish, rewrite, edit, or generate images.

## Production Entry

The production runner is:

```bash
scripts/run_langgraph_batch.py --production
```

That runner:

1. reads crawler rows from MySQL
2. normalizes the article body into `source_content`
3. calls `summarize_crawler_topics(...)` for batch-normalized scoring
4. writes compact audit fields to `pipeline_audit`
5. writes large reasons/breakdowns to JSONL prompt logs
6. sends only articles with `ai_score >= AI_SCORE_THRESHOLD` into the quality node

## Important Threshold

```env
AI_SCORE_THRESHOLD=75
```

Articles below this score stop after scoring, so their `quality_score` will stay
empty. That is expected and does not mean QualityAgent is stuck.

## Main Code

- `agents/scoring_agent/scoring_summary.py`
- `scripts/run_langgraph_batch.py`
- `workflows/langgraph_article_pipeline.py`

Old keyword-research tools are not part of the production LangGraph pipeline.
