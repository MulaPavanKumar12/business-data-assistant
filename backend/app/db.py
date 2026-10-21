"""Database connection helpers.

Phase 1 keeps this intentionally small: a context manager per identity.
A connection pool (``psycopg_pool``) is introduced in Phase 3, when the
query endpoint starts serving concurrent requests.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """Raised when PostgreSQL cannot be reached."""


@contextmanager
def _connect(dsn: str, *, autocommit: bool) -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(dsn, autocommit=autocommit, connect_timeout=5, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        logger.error("database connection failed: %s", exc.__class__.__name__)
        raise DatabaseUnavailableError("Could not connect to PostgreSQL") from exc
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def admin_connection(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
    """Connection as the schema owner. Use only for seeding and audit writes."""
    settings = settings or get_settings()
    with _connect(settings.admin_dsn, autocommit=False) as conn:
        yield conn


@contextmanager
def readonly_connection(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
    """Connection as the least-privilege read-only role. Use for all generated SQL."""
    settings = settings or get_settings()
    with _connect(settings.readonly_dsn, autocommit=True) as conn:
        yield conn
