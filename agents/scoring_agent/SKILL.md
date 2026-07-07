# ScoringAgent

## What This Module Does

ScoringAgent is the first AI gate in the Redis pipeline.

It answers one question:

> Is this source article valuable enough to spend QualityAgent / rewrite / SEO / image / CMS cost on?

It does not publish, rewrite, edit, or generate images.

## Production Entry

The production Redis worker is:

```bash
scripts/worker_scoring.py
```

That worker:

1. reads crawler payloads from `pipeline:scoring`
2. normalizes the article body into `source_content`
3. calls `summarize_crawler_topics(...)`
4. writes compact audit fields to `pipeline_audit`
5. writes large reasons/breakdowns to JSONL prompt logs
6. sends only articles with `ai_score >= AI_SCORE_THRESHOLD` to `pipeline:quality`

## Important Threshold

```env
AI_SCORE_THRESHOLD=75
```

Articles below this score stop after scoring, so their `quality_score` will stay
empty. That is expected and does not mean QualityAgent is stuck.

## Main Code

- `agents/scoring_agent/scoring_summary.py`
- `scripts/worker_scoring.py`

Old keyword-research tools are not part of the Redis production pipeline.
