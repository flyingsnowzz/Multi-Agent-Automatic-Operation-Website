#!/usr/bin/env python3
"""Run one article through the formal LangGraph article pipeline.

This is the debugging entrypoint. Production uses run_langgraph_batch.py because
that runner preserves batch scoring and feeder behavior. This file is for
answering questions such as "what happens to article 1961 if I run only this
one article through the graph?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
# Make `python3 scripts/run_langgraph_pipeline.py ...` work from the repo root
# without installing the project as a package.
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from workflows.langgraph_article_pipeline import run_article_graph, summarize_graph_result
from scripts.publish_common import preflight_publish_config


def parse_args() -> argparse.Namespace:
    """Parse single-article debug runner arguments."""

    parser = argparse.ArgumentParser(description="Standalone LangGraph article pipeline runner.")
    # Exactly one input source is allowed. This keeps the initial graph state
    # deterministic when debugging a single article.
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--article-id", type=int, help="从 crawler_news_main 读取这篇文章并运行 graph")
    source.add_argument("--input-json", type=str, help="直接传入 ArticleGraphState JSON 字符串")
    source.add_argument("--input-file", type=Path, help="从 JSON 文件读取 ArticleGraphState")
    parser.add_argument(
        "--no-late-stages",
        action="store_true",
        help="只跑 scoring/quality/rewrite，不跑 SEO/image/CMS，适合先测核心 Agent 流程",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="表达真实发布意图；CMSAgent 仍需 CMS_ENABLE_REAL_PUBLISH=true 才会真发",
    )
    parser.add_argument(
        "--persist-audit",
        action="store_true",
        help="把本次 graph 结果写回 pipeline_audit；不加这个参数时只打印结果，不改数据库",
    )
    parser.add_argument("--full-output", action="store_true", help="输出完整 graph state，而不是摘要")
    return parser.parse_args()


def _load_initial_state(args: argparse.Namespace) -> Dict[str, Any]:
    """Build the initial ArticleGraphState from CLI input."""

    # article-id is the normal path: the graph will hydrate state from MySQL.
    # JSON input/file paths are useful for replaying a saved state without
    # touching crawler_news_main.
    if args.article_id is not None:
        return {"article_id": args.article_id}
    if args.input_json:
        return json.loads(args.input_json)
    if args.input_file:
        return json.loads(args.input_file.read_text(encoding="utf-8"))
    raise RuntimeError("missing_input")


async def main() -> int:
    """Run one graph invocation and print either summary or full state."""

    args = parse_args()
    # Reuse the same publish preflight as production. If --publish is absent,
    # this intentionally stays dry-run and skips CMS credential checks.
    preflight_publish_config(dry_run=not args.publish)
    state = _load_initial_state(args)
    # setdefault lets a JSON replay file override these flags explicitly while
    # keeping safe defaults for article-id runs.
    state.setdefault("publish_dry_run", not args.publish)
    state.setdefault("run_late_stages", not args.no_late_stages)
    # Keep standalone graph safe by default. Only --persist-audit writes the
    # production audit table; normal runs only print the graph result.
    state.setdefault("persist_audit", args.persist_audit)
    result = await run_article_graph(state)
    payload = result if args.full_output else summarize_graph_result(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
