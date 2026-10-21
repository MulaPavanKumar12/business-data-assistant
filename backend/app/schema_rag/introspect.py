"""Read the live database schema (tables, columns, comments, keys, sample values).

The database is the single source of truth for documentation: every table and
column carries a ``COMMENT``. This module turns that metadata into
:class:`TableDoc` objects that feed both the RAG index and the SQL prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)

# Text columns with at most this many distinct values get their values listed in
# the prompt (e.g. orders.status), so the LLM never guesses 'Cancelled' vs 'cancelled'.
MAX_ENUM_VALUES = 12

_COLUMNS_SQL = """
SELECT cls.relname                              AS table_name,
       obj_description(cls.oid, 'pg_class')     AS table_comment,
       att.attname                              AS column_name,
       format_type(att.atttypid, att.atttypmod) AS data_type,
       NOT att.attnotnull                       AS nullable,
       col_description(cls.oid, att.attnum)     AS column_comment,
       COALESCE(pk.is_pk, FALSE)                AS is_pk
FROM pg_class cls
JOIN pg_namespace ns ON ns.oid = cls.relnamespace AND ns.nspname = %(schema)s
JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum > 0 AND NOT att.attisdropped
LEFT JOIN LATERAL (
    SELECT TRUE AS is_pk FROM pg_constraint con
    WHERE con.conrelid = cls.oid AND con.contype = 'p' AND att.attnum = ANY (con.conkey)
) pk ON TRUE
WHERE cls.relkind = 'r' AND has_table_privilege(cls.oid, 'SELECT')
ORDER BY cls.relname, att.attnum
"""

_FKS_SQL = """
SELECT src.relname AS table_name, a.attname AS column_name,
       dst.relname AS ref_table, b.attname AS ref_column
FROM pg_constraint con
JOIN pg_class src ON src.oid = con.conrelid
JOIN pg_class dst ON dst.oid = con.confrelid
JOIN pg_namespace ns ON ns.oid = src.relnamespace AND ns.nspname = %(schema)s
JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1]
JOIN pg_attribute b ON b.attrelid = con.confrelid AND b.attnum = con.confkey[1]
WHERE con.contype = 'f'
ORDER BY 1, 2
"""


@dataclass
class ColumnDoc:
    """One documented column."""

    name: str
    data_type: str
    nullable: bool
    description: str | None
    is_primary_key: bool = False
    references: str | None = None  # "table.column" for foreign keys
    sample_values: list[str] = field(default_factory=list)


@dataclass
class TableDoc:
    """One documented table, ready to be embedded or rendered into a prompt."""

    name: str
    description: str | None
    columns: list[ColumnDoc]

    @property
    def foreign_keys(self) -> list[tuple[str, str]]:
        """(column, "ref_table.ref_column") pairs."""
        return [(c.name, c.references) for c in self.columns if c.references]

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def to_prompt(self, hidden_columns: set[str] | None = None) -> str:
        """Render as compact pseudo-DDL with comments for the LLM prompt.

        Args:
            hidden_columns: columns the current user may not see (RBAC). They are
                left out of the prompt entirely so the model never learns they exist.
        """
        hidden = hidden_columns or set()
        lines = [f"TABLE {self.name}  -- {self.description or ''}".rstrip()]
        for c in self.columns:
            if c.name in hidden:
                continue
            extras = []
            if c.is_primary_key:
                extras.append("PK")
            if c.references:
                extras.append(f"FK -> {c.references}")
            if c.sample_values:
                extras.append("values: " + ", ".join(repr(v) for v in c.sample_values))
            meta = f" [{'; '.join(extras)}]" if extras else ""
            lines.append(f"  {c.name} {c.data_type}{meta}  -- {c.description or ''}".rstrip())
        return "\n".join(lines)

    def to_documents(self) -> list[tuple[str, str]]:
        """(doc_id, text) pairs for the vector index: one per table + one per column."""
        docs = [
            (
                f"table:{self.name}",
                f"Table {self.name}: {self.description or ''} Columns: {', '.join(self.column_names())}.",
            )
        ]
        for c in self.columns:
            text = f"{self.name}.{c.name} ({c.data_type}): {c.description or ''}"
            if c.sample_values:
                text += f" Possible values: {', '.join(c.sample_values)}."
            docs.append((f"column:{self.name}.{c.name}", text))
        return docs


def introspect_schema(conn: psycopg.Connection, schema: str = "public", *, with_samples: bool = True) -> list[TableDoc]:
    """Return every readable table in ``schema`` with comments, keys and enum-like values."""
    tables: dict[str, TableDoc] = {}
    with conn.cursor() as cur:
        cur.execute(_COLUMNS_SQL, {"schema": schema})
        for r in cur.fetchall():
            r = r if isinstance(r, dict) else dict(zip([d.name for d in cur.description], r, strict=True))
            t = tables.setdefault(r["table_name"], TableDoc(r["table_name"], r["table_comment"], []))
            t.columns.append(
                ColumnDoc(r["column_name"], r["data_type"], r["nullable"], r["column_comment"], bool(r["is_pk"]))
            )
        cur.execute(_FKS_SQL, {"schema": schema})
        fks = cur.fetchall()
    for fk in fks:
        fk = (
            fk
            if isinstance(fk, dict)
            else dict(zip(("table_name", "column_name", "ref_table", "ref_column"), fk, strict=True))
        )
        table = tables.get(fk["table_name"])
        if table:
            for c in table.columns:
                if c.name == fk["column_name"]:
                    c.references = f"{fk['ref_table']}.{fk['ref_column']}"
    if with_samples:
        _add_sample_values(conn, list(tables.values()))
    logger.info("introspected %d tables", len(tables))
    return sorted(tables.values(), key=lambda t: t.name)


def _add_sample_values(conn: psycopg.Connection, tables: list[TableDoc]) -> None:
    for t in tables:
        for c in t.columns:
            if not c.data_type.startswith(("character varying", "text")) or c.references:
                continue
            if any(k in c.name for k in ("email", "phone", "reference", "number", "sku", "description", "contact")):
                continue  # free text / PII: never sample (names are kept only if low-cardinality, e.g. categories)
            query = sql.SQL(
                "SELECT DISTINCT {col}::text AS v FROM {tbl} WHERE {col} IS NOT NULL ORDER BY 1 LIMIT {n}"
            ).format(col=sql.Identifier(c.name), tbl=sql.Identifier(t.name), n=sql.Literal(MAX_ENUM_VALUES + 1))
            try:
                rows = conn.execute(query).fetchall()
            except psycopg.Error as exc:  # pragma: no cover - permissions vary by role
                logger.debug("sampling %s.%s failed: %s", t.name, c.name, exc)
                conn.rollback()
                continue
            values = [r["v"] if isinstance(r, dict) else r[0] for r in rows]
            if 0 < len(values) <= MAX_ENUM_VALUES:
                c.sample_values = values
