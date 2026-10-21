"""Execute validated SQL as the read-only role and return JSON-friendly results."""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql as pgsql

from app.config import Settings, get_settings
from app.db import DatabaseUnavailableError, readonly_connection

logger = logging.getLogger(__name__)


class QueryExecutionError(RuntimeError):
    """The database rejected or cancelled the query. Message is safe to show the LLM."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: float
    column_types: list[str] = field(default_factory=list)

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r, strict=True)) for r in self.rows]


def to_jsonable(value: Any) -> Any:
    """Convert DB values to JSON-safe Python types (Decimal -> float, dates -> ISO)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return value


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float | Decimal):
        return "number"
    if isinstance(value, dt.datetime | dt.date):
        return "date"
    return "text"


def execute_readonly(
    query: str, settings: Settings | None = None, max_rows: int | None = None, timeout_ms: int | None = None
) -> QueryResult:
    """Run ``query`` (already validated) inside a READ ONLY transaction with a timeout."""
    settings = settings or get_settings()
    max_rows = max_rows or settings.max_result_rows
    timeout_ms = timeout_ms or settings.query_timeout_ms
    start = time.perf_counter()
    try:
        with readonly_connection(settings) as conn:
            conn.autocommit = False
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(int(timeout_ms))))
                cur.execute(query, prepare=False)
                columns = [d.name for d in cur.description or []]
                raw = cur.fetchmany(max_rows + 1)
    except DatabaseUnavailableError:
        raise
    except psycopg.errors.QueryCanceled as exc:
        raise QueryExecutionError(f"Query took longer than {timeout_ms} ms and was cancelled") from exc
    except psycopg.Error as exc:
        message = (exc.diag.message_primary if exc.diag else None) or str(exc).splitlines()[0]
        raise QueryExecutionError(message) from exc

    truncated = len(raw) > max_rows
    raw = raw[:max_rows]
    rows = [[to_jsonable(r[c] if isinstance(r, dict) else r[i]) for i, c in enumerate(columns)] for r in raw]
    types: list[str] = []
    for i, c in enumerate(columns):
        sample = next(
            (r[c] if isinstance(r, dict) else r[i] for r in raw if (r[c] if isinstance(r, dict) else r[i]) is not None),
            None,
        )
        types.append(_type_name(sample))
    elapsed = (time.perf_counter() - start) * 1000
    logger.info("query ok rows=%d truncated=%s elapsed_ms=%.1f", len(rows), truncated, elapsed)
    return QueryResult(columns, rows, len(rows), truncated, round(elapsed, 1), types)
