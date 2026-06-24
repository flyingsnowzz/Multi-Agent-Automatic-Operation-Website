#!/usr/bin/env python3
"""Generate WriterAgent articles from research_article_candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.writer_agent.tools.article_generation_writer import generate_articles_from_research_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WriterAgent generation from research DB")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="research_article_data")
    parser.add_argument("--candidate-table", default="research_article_candidates")
    parser.add_argument("--output-table", default="writer_article_outputs")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate even when a generated output already exists",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await generate_articles_from_research_db(
        {
            "host": args.host,
            "port": args.port,
            "user": args.user,
            "password": args.password,
            "database": args.database,
            "candidate_table": args.candidate_table,
            "output_table": args.output_table,
        },
        limit=args.limit,
        concurrency=args.concurrency,
        only_missing_outputs=not args.regenerate,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
