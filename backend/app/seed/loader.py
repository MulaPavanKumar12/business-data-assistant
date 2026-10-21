"""Load a generated :class:`Dataset` into PostgreSQL using fast COPY.

The whole load runs in ONE transaction: either every table is seeded, or
nothing is, so a failed seed never leaves a half-populated database.
"""

from __future__ import annotations

import logging
import time

import psycopg
from psycopg import sql

from app.seed.generator import LOAD_ORDER, TABLE_COLUMNS, Dataset

logger = logging.getLogger(__name__)

# Primary-key column per table, used to move SERIAL sequences past the
# explicit ids we inserted (otherwise the next INSERT from the API would clash).
PRIMARY_KEYS: dict[str, str] = {table: columns[0] for table, columns in TABLE_COLUMNS.items()}


class SeedError(RuntimeError):
    """Raised when the database cannot be seeded."""


def database_has_data(conn: psycopg.Connection) -> bool:
    """Return True if the business tables already contain rows."""
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM customers) AS has_data")
        row = cur.fetchone()
    return bool(row and (row["has_data"] if isinstance(row, dict) else row[0]))


def truncate_all(conn: psycopg.Connection) -> None:
    """Remove all business data and reset id sequences (audit log is kept)."""
    tables = sql.SQL(", ").join(sql.Identifier(t) for t in reversed(LOAD_ORDER))
    with conn.cursor() as cur:
        cur.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(tables))
    logger.info("truncated %d tables", len(LOAD_ORDER))


def _copy_table(conn: psycopg.Connection, table: str, rows: list[dict]) -> None:
    columns = TABLE_COLUMNS[table]
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    )
    with conn.cursor() as cur, cur.copy(stmt) as copy:
        for row in rows:
            copy.write_row(tuple(row[c] for c in columns))


def _reset_sequences(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for table, pk in PRIMARY_KEYS.items():
            cur.execute(
                sql.SQL(
                    "SELECT setval(pg_get_serial_sequence(%s, %s), "
                    "COALESCE((SELECT MAX({pk}) FROM {table}), 0) + 1, false)"
                ).format(pk=sql.Identifier(pk), table=sql.Identifier(table)),
                (table, pk),
            )


def load_dataset(conn: psycopg.Connection, dataset: Dataset, *, reset: bool = False) -> dict[str, int]:
    """Insert every table of ``dataset`` inside a single transaction.

    Args:
        conn: A connection as the schema owner (NOT autocommit).
        dataset: Rows produced by the generator.
        reset: If True, existing business data is truncated first.

    Returns:
        Row counts per table that were inserted.

    Raises:
        SeedError: if data already exists and ``reset`` is False, or the load fails.
    """
    start = time.perf_counter()
    try:
        with conn.transaction():
            if database_has_data(conn):
                if not reset:
                    raise SeedError("Database already contains data. Re-run with --reset to replace it.")
                truncate_all(conn)
            for table in LOAD_ORDER:
                _copy_table(conn, table, dataset[table])
                logger.info("loaded table=%s rows=%d", table, len(dataset[table]))
            _reset_sequences(conn)
    except psycopg.Error as exc:
        raise SeedError(f"Seeding failed: {exc}") from exc

    # ANALYZE outside the transaction so the planner has fresh statistics.
    conn.execute("ANALYZE")
    conn.commit()
    elapsed = time.perf_counter() - start
    logger.info("seed complete in %.2fs", elapsed)
    return dataset.counts()
