"""Database table-name helpers for the LangGraph article pipeline.

The pipeline can run against the local demo schema or an existing CMS database.
Those environments often use different crawler table names, so table names are
configured through .env and validated here before being interpolated into SQL.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _env_int(name: str, default: int) -> int:
    """Read a positive integer from .env with a safe fallback."""

    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_optional_identifier(name: str, default: Optional[str]) -> Optional[str]:
    """Read an optional SQL identifier and reject unsafe characters."""

    value = os.environ.get(name)
    if value is None:
        value = default
    value = str(value or "").strip()
    if not value:
        return None
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(f"{name} must contain only letters, numbers, and underscores: {value!r}")
    return value


def quote_identifier(identifier: str) -> str:
    """Return a backtick-quoted MySQL identifier after validation."""

    if not _IDENTIFIER_RE.match(str(identifier or "")):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return f"`{identifier}`"


@dataclass(frozen=True)
class CrawlerTableConfig:
    """Resolved crawler/audit table names used by the LangGraph pipeline."""

    main_table: str
    shard_prefix: str
    shard_count: int
    audit_table: str
    usage_status_column: Optional[str]

    @property
    def main_sql(self) -> str:
        """Quoted main crawler metadata table for SQL snippets."""

        return quote_identifier(self.main_table)

    @property
    def audit_sql(self) -> str:
        """Quoted pipeline audit table for SQL snippets."""

        return quote_identifier(self.audit_table)

    @property
    def usage_status_sql(self) -> Optional[str]:
        """Quoted usage-status column, or None when the source table lacks it."""

        if not self.usage_status_column:
            return None
        return quote_identifier(self.usage_status_column)

    def shard_tables(self) -> List[str]:
        """Return configured shard table names in numeric order."""

        return [f"{self.shard_prefix}{idx}" for idx in range(self.shard_count)]

    def shard_sql(self, idx: int) -> str:
        """Return the quoted shard table for a zero-based shard index."""

        return quote_identifier(f"{self.shard_prefix}{idx}")


def crawler_table_config() -> CrawlerTableConfig:
    """Load crawler table configuration from environment variables."""

    main_table = _env_optional_identifier("CRAWLER_MAIN_TABLE", "crawler_news_main")
    shard_prefix = _env_optional_identifier("CRAWLER_SHARD_PREFIX", "crawler_news_")
    audit_table = _env_optional_identifier("PIPELINE_AUDIT_TABLE", "pipeline_audit")
    usage_status_column = _env_optional_identifier("CRAWLER_USAGE_STATUS_COLUMN", "article_usage_status")
    if not main_table or not shard_prefix or not audit_table:
        raise ValueError("crawler table configuration is incomplete")
    return CrawlerTableConfig(
        main_table=main_table,
        shard_prefix=shard_prefix,
        shard_count=_env_int("CRAWLER_SHARD_COUNT", 5),
        audit_table=audit_table,
        usage_status_column=usage_status_column,
    )
