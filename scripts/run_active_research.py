#!/usr/bin/env python3
"""CLI entry for the active research pipeline v1.

Current implementation supports the first safe feature: discover keyword-scoped
online topics and score them without generating or publishing articles.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agents.active_research_agent import ActiveResearchAgent


LOG = logging.getLogger("active_research.runner")
DEFAULT_OUTPUT_PATH = ROOT / "output" / "active_research_candidates.json"


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment setting from common .env spellings."""

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an integer environment setting with a safe fallback."""

    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _configure_logging() -> None:
    """Configure readable runtime logging for manual tests."""

    level = os.environ.get("ACTIVE_RESEARCH_LOG_LEVEL", "INFO").upper()
    third_party_level = os.environ.get("ACTIVE_RESEARCH_THIRD_PARTY_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for noisy_logger in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(third_party_level)


def _parse_args() -> argparse.Namespace:
    """Parse active research command-line flags."""

    parser = argparse.ArgumentParser(description="Discover keyword-scoped hot topics for the active research pipeline.")
    parser.add_argument("--discover-only", action="store_true", help="Only discover and score candidates; do not generate articles.")
    parser.add_argument("--dry-run", action="store_true", help="Alias for discover-only in v1.")
    parser.add_argument("--production", action="store_true", help="Require ACTIVE_RESEARCH_ENABLED=true before running.")
    parser.add_argument("--limit", type=int, default=_env_int("ACTIVE_RESEARCH_TOPICS_PER_RUN", 20), help="Maximum candidates to output.")
    parser.add_argument("--include-below-threshold", action="store_true", help="Keep candidates below ACTIVE_RESEARCH_MIN_TOPIC_SCORE for debugging.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON output path for discovered candidates.")
    return parser.parse_args()


def _write_output(path: Path, payload: Dict[str, Any]) -> None:
    """Write discovery output as readable JSON for review."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _main() -> int:
    """Run the v1 discover-only active research command."""

    _configure_logging()
    args = _parse_args()
    if args.production and not _env_bool("ACTIVE_RESEARCH_ENABLED", False):
        LOG.error("active_research_disabled set ACTIVE_RESEARCH_ENABLED=true to run production mode")
        return 2

    # v1 deliberately has no downstream generation. These flags make the current
    # safety boundary explicit while keeping the future CLI shape stable.
    if not (args.discover_only or args.dry_run or args.production):
        LOG.info("defaulting_to_discover_only")

    agent = ActiveResearchAgent()
    candidates = await agent.discover(limit=args.limit, include_below_threshold=args.include_below_threshold)
    research_briefs = agent.build_research_briefs(candidates)
    payload = {
        "mode": "discover_only",
        "keyword_config_path": str(agent.keyword_config_path),
        "lookback_hours": agent.lookback_hours,
        "min_topic_score": agent.min_topic_score,
        "candidate_count": len(candidates),
        "research_ready_count": sum(1 for item in research_briefs if item.get("research_ready")),
        "candidates": ActiveResearchAgent.to_jsonable(candidates),
        "research_briefs": research_briefs,
    }
    _write_output(args.output, payload)
    LOG.info("active_research_discovered count=%s output=%s", len(candidates), args.output)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
