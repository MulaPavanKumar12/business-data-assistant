"""SQL validator: what gets through, what is blocked, and how LIMIT is enforced."""

from __future__ import annotations

import pytest

from app.security.rbac import get_policy
from app.sql.validator import SQLValidationError, SQLValidator


@pytest.fixture
def analyst() -> SQLValidator:
    return SQLValidator(get_policy("analyst"), max_rows=100)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT company_name FROM customers",
        "SELECT c.segment, SUM(o.total_amount) FROM orders o JOIN customers c USING (customer_id) GROUP BY 1",
        "WITH t AS (SELECT customer_id, COUNT(*) n FROM orders GROUP BY 1) SELECT * FROM t ORDER BY n DESC",
        "SELECT COUNT(*) FROM employees",
        "SELECT first_name, last_name FROM employees WHERE is_active",
        "SELECT * FROM orders",
        "SELECT name FROM products UNION SELECT name FROM product_categories",
        "select date_trunc('month', order_date) m, sum(total_amount) from orders group by m",
    ],
)
def test_safe_queries_pass(analyst: SQLValidator, sql: str) -> None:
    result = analyst.validate(sql)
    assert result.ok, result.errors
    assert "LIMIT" in result.sql.upper()


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM orders", "DELETE"),
        ("UPDATE customers SET status = 'churned'", "UPDATE"),
        ("INSERT INTO orders (status) VALUES ('pending')", "INSERT"),
        ("DROP TABLE customers", "DROP"),
        ("TRUNCATE orders", "TRUNCATE"),
        ("ALTER TABLE orders ADD COLUMN x int", "ALTER"),
        ("SELECT 1; DELETE FROM orders", "one SQL statement"),
        ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "DELETE"),
        ("SELECT * INTO backup FROM orders", "INTO"),
        ("SELECT * FROM orders FOR UPDATE", "locking"),
        ("SELECT * FROM audit.events", "audit"),
        ("SELECT * FROM pg_catalog.pg_roles", "pg_catalog"),
        ("SELECT * FROM pg_user", "system table"),
        ("SELECT * FROM information_schema.columns", "information_schema"),
        ("SELECT pg_sleep(5)", "pg_sleep"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
        ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
        ("SELECT salary FROM employees", "salary"),
        ("SELECT e.salary FROM employees AS e", "salary"),
        ("SELECT * FROM employees", "SELECT *"),
        ("SELECT e.* FROM employees e", "SELECT *"),
        ("SELECT AVG(salary) FROM employees", "salary"),
        ("this is not sql", "parsed"),
        ("", "Empty"),
    ],
)
def test_dangerous_queries_are_blocked(analyst: SQLValidator, sql: str, reason: str) -> None:
    result = analyst.validate(sql)
    assert not result.ok
    assert any(reason.lower() in e.lower() for e in result.errors), result.errors


def test_limit_is_added_and_capped(analyst: SQLValidator) -> None:
    assert analyst.validate("SELECT * FROM orders").sql.rstrip().endswith("LIMIT 100")
    assert analyst.validate("SELECT * FROM orders LIMIT 99999").sql.rstrip().endswith("LIMIT 100")
    assert analyst.validate("SELECT * FROM orders LIMIT 10").sql.rstrip().endswith("LIMIT 10")


def test_comments_are_stripped(analyst: SQLValidator) -> None:
    result = analyst.validate("SELECT 1 -- ; DROP TABLE orders")
    assert result.ok and "DROP" not in result.sql


def test_role_restricts_tables() -> None:
    viewer = SQLValidator(get_policy("viewer"))
    assert not viewer.validate("SELECT * FROM invoices").ok
    assert not viewer.validate("SELECT email FROM customers").ok
    assert viewer.validate("SELECT company_name FROM customers").ok


def test_finance_can_see_salary() -> None:
    assert SQLValidator(get_policy("finance")).validate("SELECT AVG(salary) FROM employees").ok


def test_validate_or_raise() -> None:
    with pytest.raises(SQLValidationError):
        SQLValidator(get_policy("admin")).validate_or_raise("DROP TABLE orders")
