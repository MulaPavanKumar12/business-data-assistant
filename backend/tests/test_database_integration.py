"""Integration tests against the real PostgreSQL started by docker compose.

The most important ones are the *security* tests: they prove that the role
the AI uses cannot modify data or read the audit log, no matter what SQL an
LLM (or an attacker hiding inside a prompt) produces. This is the last line
of defence beneath the SQL validator built in Phase 3.
"""

from __future__ import annotations

import psycopg
import pytest

pytestmark = pytest.mark.integration

BUSINESS_TABLES = {
    "departments",
    "employees",
    "customers",
    "product_categories",
    "products",
    "orders",
    "order_items",
    "invoices",
    "payments",
}


# ----------------------------------------------------------------- schema ---
def test_all_business_tables_exist(readonly_conn: psycopg.Connection) -> None:
    rows = readonly_conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert BUSINESS_TABLES <= {r["table_name"] for r in rows}


def test_every_table_and_column_is_documented(admin_conn: psycopg.Connection) -> None:
    """Schema RAG (Phase 2) depends on these comments existing."""
    missing = admin_conn.execute(
        """
        SELECT cls.relname || '.' || att.attname AS col
        FROM pg_class cls
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace AND ns.nspname = 'public'
        JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum > 0 AND NOT att.attisdropped
        WHERE cls.relkind = 'r' AND col_description(cls.oid, att.attnum) IS NULL
        UNION ALL
        SELECT cls.relname
        FROM pg_class cls
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace AND ns.nspname = 'public'
        WHERE cls.relkind = 'r' AND obj_description(cls.oid, 'pg_class') IS NULL
        """
    ).fetchall()
    assert missing == [], f"undocumented: {[m['col'] for m in missing]}"


# ------------------------------------------------------------------- data ---
def test_database_is_seeded(readonly_conn: psycopg.Connection) -> None:
    for table in BUSINESS_TABLES:
        n = readonly_conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
        assert n > 0, f"{table} is empty - did the seed container run?"


def test_seeded_totals_are_consistent(readonly_conn: psycopg.Connection) -> None:
    bad = readonly_conn.execute(
        """
        SELECT COUNT(*) AS n FROM orders o
        JOIN (SELECT order_id, SUM(line_total) AS s FROM order_items GROUP BY order_id) i USING (order_id)
        WHERE o.subtotal <> i.s
           OR o.total_amount <> o.subtotal - o.discount_amount + o.tax_amount
        """
    ).fetchone()["n"]
    assert bad == 0


def test_sample_business_question_returns_rows(readonly_conn: psycopg.Connection) -> None:
    """The kind of SQL Phase 3 will generate for 'top 10 customers by revenue'."""
    rows = readonly_conn.execute(
        """
        SELECT c.company_name, SUM(o.total_amount) AS revenue
        FROM orders o JOIN customers c USING (customer_id)
        WHERE o.status <> 'cancelled'
        GROUP BY c.company_name ORDER BY revenue DESC LIMIT 10
        """
    ).fetchall()
    assert len(rows) == 10
    assert rows[0]["revenue"] >= rows[-1]["revenue"]


# --------------------------------------------------------------- security ---
@pytest.mark.parametrize(
    "malicious_sql",
    [
        "DELETE FROM orders",
        "UPDATE customers SET credit_limit = 0",
        "INSERT INTO departments (name, cost_center) VALUES ('x', 'CC-999')",
        "DROP TABLE customers",
        "TRUNCATE invoices",
        "ALTER TABLE products ADD COLUMN hacked int",
        "CREATE TABLE stolen AS SELECT * FROM customers",
        "GRANT ALL ON customers TO PUBLIC",
        "SET default_transaction_read_only = off; DELETE FROM payments",
    ],
)
def test_readonly_role_cannot_modify_data(readonly_conn: psycopg.Connection, malicious_sql: str) -> None:
    with pytest.raises(psycopg.errors.Error):
        readonly_conn.execute(malicious_sql)
    readonly_conn.rollback()


def test_readonly_role_cannot_read_audit_log(readonly_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        readonly_conn.execute("SELECT * FROM audit.events")


def test_readonly_session_is_read_only_with_timeout(readonly_conn: psycopg.Connection) -> None:
    assert (
        readonly_conn.execute("SHOW default_transaction_read_only").fetchone()["default_transaction_read_only"] == "on"
    )
    assert readonly_conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "5s"


def test_runaway_query_is_cancelled_by_statement_timeout(readonly_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.QueryCanceled):
        readonly_conn.execute("SELECT pg_sleep(10)")


def test_readonly_role_is_not_superuser(readonly_conn: psycopg.Connection) -> None:
    row = readonly_conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row == {"rolsuper": False, "rolcreaterole": False, "rolcreatedb": False}
