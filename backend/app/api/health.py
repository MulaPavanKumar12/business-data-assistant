"""Health and schema-inspection endpoints (Phase 1).

``/api/v1/schema`` already reads table/column descriptions through the
read-only role. Phase 2 turns exactly this metadata into schema-RAG documents.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app import __version__
from app.db import DatabaseUnavailableError, readonly_connection

logger = logging.getLogger(__name__)
router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    customers: int | None = None
    orders: int | None = None


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    nullable: bool
    description: str | None


class TableInfo(BaseModel):
    name: str
    description: str | None
    columns: list[ColumnInfo]


SCHEMA_SQL = """
SELECT cls.relname                          AS table_name,
       obj_description(cls.oid, 'pg_class') AS table_comment,
       att.attname                          AS column_name,
       format_type(att.atttypid, att.atttypmod) AS data_type,
       NOT att.attnotnull                   AS nullable,
       col_description(cls.oid, att.attnum) AS column_comment
FROM pg_class AS cls
JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace AND ns.nspname = 'public'
JOIN pg_attribute AS att ON att.attrelid = cls.oid AND att.attnum > 0 AND NOT att.attisdropped
WHERE cls.relkind = 'r'
  AND has_table_privilege(cls.oid, 'SELECT')
ORDER BY cls.relname, att.attnum
"""


def get_readonly_conn() -> Iterator[psycopg.Connection]:
    """FastAPI dependency yielding a read-only connection (overridable in tests)."""
    with readonly_connection() as conn:
        yield conn


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness + database readiness check. Returns 503 if PostgreSQL is down."""
    try:
        with readonly_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT (SELECT COUNT(*) FROM customers) AS c, (SELECT COUNT(*) FROM orders) AS o")
            row = cur.fetchone() or {}
    except (DatabaseUnavailableError, psycopg.Error) as exc:
        logger.warning("health check failed: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable") from exc
    return HealthResponse(status="ok", version=__version__, database="ok", customers=row.get("c"), orders=row.get("o"))


@router.get("/api/v1/schema", response_model=list[TableInfo], tags=["schema"])
def describe_schema(conn: Annotated[psycopg.Connection, Depends(get_readonly_conn)]) -> list[TableInfo]:
    """List every business table and column with its human description."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        rows = cur.fetchall()
    tables: dict[str, TableInfo] = {}
    for r in rows:
        table = tables.setdefault(
            r["table_name"], TableInfo(name=r["table_name"], description=r["table_comment"], columns=[])
        )
        table.columns.append(
            ColumnInfo(
                name=r["column_name"], data_type=r["data_type"], nullable=r["nullable"], description=r["column_comment"]
            )
        )
    return list(tables.values())
