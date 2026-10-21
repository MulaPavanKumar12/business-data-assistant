"""Static validation and rewriting of LLM-generated SQL with sqlglot.

This is the *second* line of defence (the read-only DB role is the last). It
works on the parsed syntax tree, never on regexes over the SQL text, so
comments, casing, quoting and nesting tricks do not get past it.

Rules
-----
* exactly one statement, and it must be a query (SELECT / UNION / WITH ... SELECT)
* no DML/DDL/DCL node anywhere in the tree (e.g. a data-modifying CTE)
* no ``SELECT ... INTO`` and no row locks (``FOR UPDATE``)
* only tables allowed by the user's role; no system schemas (pg_catalog, audit, ...)
* no hidden columns (e.g. ``employees.salary`` for analysts), including via ``*``
* no dangerous functions (pg_sleep, pg_read_file, dblink, set_config, ...)
* a LIMIT is always present and never above ``max_rows``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.security.rbac import RolePolicy

logger = logging.getLogger(__name__)

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "AlterTable",
        "TruncateTable",
        "Grant",
        "Revoke",
        "Command",
        "Copy",
        "Set",
        "Use",
        "Transaction",
        "Commit",
        "Rollback",
        "LoadData",
        "Pragma",
        "Analyze",
        "Comment",
        "Kill",
        "Describe",
    )
    if hasattr(exp, name)
)

DENIED_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "set_config",
        "current_setting",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "query_to_xml",
        "table_to_xml",
        "database_to_xml",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "txid_current",
        "pg_current_xact_id",
        "version",
        "inet_server_addr",
        "pg_postmaster_start_time",
        "current_user",
        "session_user",
        "pg_backend_pid",
        "generate_series",
    }
)
DENIED_SCHEMAS = frozenset({"pg_catalog", "information_schema", "audit", "pg_toast"})


class SQLValidationError(ValueError):
    """Raised by :meth:`SQLValidator.validate_or_raise`."""


@dataclass
class ValidationResult:
    ok: bool
    sql: str = ""
    errors: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


class SQLValidator:
    """Validate one SQL string against a role policy and rewrite it safely."""

    def __init__(self, policy: RolePolicy, max_rows: int = 200, dialect: str = "postgres") -> None:
        self.policy = policy
        self.max_rows = max_rows
        self.dialect = dialect

    def validate(self, sql: str) -> ValidationResult:
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            return ValidationResult(False, errors=["Empty SQL"])
        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except ParseError as exc:
            return ValidationResult(False, errors=[f"SQL could not be parsed: {str(exc).splitlines()[0]}"])
        if len(statements) != 1:
            return ValidationResult(False, errors=["Exactly one SQL statement is allowed"])
        tree = statements[0]

        errors: list[str] = []
        if not isinstance(tree, exp.Query):
            errors.append(f"Only SELECT queries are allowed (got {tree.key.upper()})")
        for node in tree.walk():
            if isinstance(node, FORBIDDEN_NODES):
                errors.append(f"Forbidden operation: {node.key.upper()}")
            elif isinstance(node, exp.Into):
                errors.append("SELECT ... INTO is not allowed")
            elif isinstance(node, exp.Lock):
                errors.append("Row locking (FOR UPDATE/SHARE) is not allowed")
        errors.extend(self._check_functions(tree))
        tables, table_errors = self._check_tables(tree)
        errors.extend(table_errors)
        errors.extend(self._check_columns(tree, tables))
        if errors:
            unique = list(dict.fromkeys(errors))
            logger.info("sql blocked: %s", unique)
            return ValidationResult(False, errors=unique, tables=sorted(tables))

        safe = self._enforce_limit(tree)
        return ValidationResult(
            True, sql=safe.sql(dialect=self.dialect, pretty=True, comments=False), tables=sorted(tables)
        )

    def validate_or_raise(self, sql: str) -> ValidationResult:
        result = self.validate(sql)
        if not result.ok:
            raise SQLValidationError("; ".join(result.errors))
        return result

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _check_functions(tree: exp.Expression) -> list[str]:
        errors = []
        for fn in tree.find_all(exp.Func):
            name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
            if name in DENIED_FUNCTIONS:
                errors.append(f"Function not allowed: {name}")
        for col in tree.find_all(exp.Column):  # current_user etc. parse as bare identifiers
            if not col.table and col.name.lower() in DENIED_FUNCTIONS:
                errors.append(f"Function not allowed: {col.name.lower()}")
        return errors

    def _check_tables(self, tree: exp.Expression) -> tuple[set[str], list[str]]:
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        tables: set[str] = set()
        errors: list[str] = []
        for t in tree.find_all(exp.Table):
            name = t.name.lower()
            schema = (t.db or "").lower()
            if not name:  # e.g. table functions
                errors.append("Table functions are not allowed")
                continue
            if schema and (schema in DENIED_SCHEMAS or schema != "public"):
                errors.append(f"Access to schema '{schema}' is not allowed")
                continue
            if name.startswith("pg_"):
                errors.append(f"Access to system table '{name}' is not allowed")
                continue
            if name in cte_names and not schema:
                continue
            if not self.policy.can_read_table(name):
                errors.append(f"Table '{name}' is not available for role '{self.policy.name}'")
                continue
            tables.add(name)
        return tables, errors

    def _check_columns(self, tree: exp.Expression, tables: set[str]) -> list[str]:
        hidden = {t: cols for t, cols in self.policy.hidden_columns.items() if t in tables and cols}
        if not hidden:
            return []
        errors = []
        alias_to_table = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
        for col in tree.find_all(exp.Column):
            name = col.name.lower()
            qualifier = col.table.lower()
            owner = alias_to_table.get(qualifier) if qualifier else None
            candidates = [owner] if owner else list(hidden)
            for table in candidates:
                if table in hidden and name in hidden[table]:
                    errors.append(f"Column '{table}.{name}' is restricted for role '{self.policy.name}'")
        for star in tree.find_all(exp.Star):
            parent = star.parent
            qualifier = parent.table.lower() if isinstance(parent, exp.Column) and parent.table else ""
            star_tables = [alias_to_table.get(qualifier, qualifier)] if qualifier else list(hidden)
            if isinstance(star.parent, exp.Count) or (
                isinstance(parent, exp.Column) and isinstance(parent.parent, exp.Count)
            ):
                continue  # COUNT(*) is harmless
            if any(t in hidden for t in star_tables):
                errors.append("SELECT * is not allowed on tables with restricted columns; list the columns")
        return errors

    def _enforce_limit(self, tree: exp.Expression) -> exp.Expression:
        limit = tree.args.get("limit")
        if limit is not None:
            try:
                value = int(limit.expression.this) if isinstance(limit.expression, exp.Literal) else None
            except (TypeError, ValueError):
                value = None
            if value is not None and value <= self.max_rows:
                return tree
        return tree.limit(self.max_rows, copy=True) if hasattr(tree, "limit") else tree
