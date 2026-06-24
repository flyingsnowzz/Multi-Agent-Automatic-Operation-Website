#!/usr/bin/env python3
"""Run QualityAgent scoring from MySQL."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.quality_agent.tools.article_quality_scorer import score_articles_to_quality_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run QualityAgent scoring from DB")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="research_article_data")
    parser.add_argument("--quality-table", default="article_quality_scores")
    parser.add_argument("--candidate-table", default="research_article_candidates")
    parser.add_argument("--writer-output-table", default="writer_article_outputs")
    parser.add_argument("--source-kind", choices=["original", "writer", "writer_plain"], default="original")
    parser.add_argument("--min-article-score", type=float, default=75.0)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--loop", action="store_true", help="Run until no missing quality rows remain")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate existing scored rows")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    db_config = {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "database": args.database,
        "quality_table": args.quality_table,
        "candidate_table": args.candidate_table,
        "writer_output_table": args.writer_output_table,
    }
    total = {"read": 0, "scored": 0, "failed": 0, "batches": 0}
    while True:
        result = await score_articles_to_quality_db(
            db_config,
            source_kind=args.source_kind,
            limit=args.limit,
            concurrency=args.concurrency,
            min_article_score=args.min_article_score,
            only_missing_quality=False if args.regenerate else True,
        )
        total["batches"] += 1
        total["read"] += int(result.get("read") or 0)
        total["scored"] += int(result.get("scored") or 0)
        total["failed"] += int(result.get("failed") or 0)
        print(json.dumps({"batch": total["batches"], **result}, ensure_ascii=False, indent=2))
        if args.regenerate or not args.loop or int(result.get("read") or 0) == 0:
            break
        if int(result.get("scored") or 0) == 0 and int(result.get("failed") or 0) > 0:
            break
    if args.loop:
        print(json.dumps({"summary": total}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
