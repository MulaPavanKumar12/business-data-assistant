"""Business operations behind the internal REST API (the "company systems" the agent calls).

These functions use the admin identity because they legitimately change data,
but they are only reachable through the internal API (shared-secret header) and,
from the assistant, only after explicit user confirmation.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import psycopg

from app.config import Settings, get_settings
from app.db import admin_connection
from app.sql.executor import to_jsonable

logger = logging.getLogger(__name__)

# Allowed order status transitions (a tiny state machine; prevents nonsense like delivered -> pending).
ORDER_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"processing", "cancelled"},
    "processing": {"shipped", "cancelled"},
    "shipped": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}
INVOICEABLE_STATUSES = {"processing", "shipped", "delivered"}


class NotFoundError(LookupError):
    pass


class BusinessRuleError(ValueError):
    pass


def _clean(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: to_jsonable(v) for k, v in row.items()} if row else None


def get_customer(customer_id: int, settings: Settings | None = None) -> dict[str, Any]:
    with admin_connection(settings or get_settings()) as conn:
        row = conn.execute(
            """
            SELECT c.customer_id, c.company_name, c.contact_name, c.email, c.phone, c.industry, c.segment,
                   c.country, c.city, c.region, c.status, c.credit_limit, c.created_at::date AS customer_since,
                   e.first_name || ' ' || e.last_name AS account_manager,
                   (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.customer_id) AS order_count,
                   (SELECT COALESCE(SUM(total_amount), 0) FROM orders o
                     WHERE o.customer_id = c.customer_id AND o.status <> 'cancelled') AS lifetime_revenue,
                   (SELECT COALESCE(SUM(amount), 0) FROM invoices i
                     WHERE i.customer_id = c.customer_id AND i.status IN ('sent', 'overdue')) AS outstanding_balance,
                   (SELECT MAX(order_date)::date FROM orders o WHERE o.customer_id = c.customer_id) AS last_order_date
            FROM customers c LEFT JOIN employees e ON e.employee_id = c.account_manager_id
            WHERE c.customer_id = %s
            """,
            (customer_id,),
        ).fetchone()
    if not row:
        raise NotFoundError(f"Customer {customer_id} not found")
    return _clean(row)  # type: ignore[return-value]


def search_customers(name: str, limit: int = 10, settings: Settings | None = None) -> list[dict[str, Any]]:
    pattern = "%" + name.replace("%", "").replace("_", "").strip() + "%"
    with admin_connection(settings or get_settings()) as conn:
        rows = conn.execute(
            "SELECT customer_id, company_name, segment, country, status FROM customers "
            "WHERE company_name ILIKE %s ORDER BY company_name LIMIT %s",
            (pattern, limit),
        ).fetchall()
    return [_clean(r) for r in rows]  # type: ignore[misc]


def get_order(order_id: int, settings: Settings | None = None) -> dict[str, Any]:
    with admin_connection(settings or get_settings()) as conn:
        order = conn.execute(
            """
            SELECT o.order_id, o.order_date, o.status, o.subtotal, o.discount_amount, o.tax_amount, o.total_amount,
                   o.shipping_country, o.updated_at, c.customer_id, c.company_name
            FROM orders o JOIN customers c USING (customer_id) WHERE o.order_id = %s
            """,
            (order_id,),
        ).fetchone()
        if not order:
            raise NotFoundError(f"Order {order_id} not found")
        items = conn.execute(
            "SELECT p.name AS product, oi.quantity, oi.unit_price, oi.line_total FROM order_items oi "
            "JOIN products p USING (product_id) WHERE oi.order_id = %s ORDER BY oi.order_item_id",
            (order_id,),
        ).fetchall()
        invoice = conn.execute(
            "SELECT invoice_id, invoice_number, status, amount, due_date FROM invoices WHERE order_id = %s", (order_id,)
        ).fetchone()
    result = _clean(order) or {}
    result["items"] = [_clean(i) for i in items]
    result["invoice"] = _clean(invoice)
    return result


def update_order_status(order_id: int, new_status: str, settings: Settings | None = None) -> dict[str, Any]:
    if new_status not in ORDER_TRANSITIONS:
        raise BusinessRuleError(f"Unknown status '{new_status}'")
    with admin_connection(settings or get_settings()) as conn, conn.transaction():
        row = conn.execute("SELECT status FROM orders WHERE order_id = %s FOR UPDATE", (order_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Order {order_id} not found")
        old = row["status"]
        if new_status not in ORDER_TRANSITIONS[old]:
            allowed = ", ".join(sorted(ORDER_TRANSITIONS[old])) or "none"
            raise BusinessRuleError(
                f"Cannot change order {order_id} from '{old}' to '{new_status}' (allowed: {allowed})"
            )
        conn.execute("UPDATE orders SET status = %s, updated_at = now() WHERE order_id = %s", (new_status, order_id))
    logger.info("order status changed order_id=%s %s->%s", order_id, old, new_status)
    return {"order_id": order_id, "old_status": old, "new_status": new_status}


def create_invoice(order_id: int, due_in_days: int = 30, settings: Settings | None = None) -> dict[str, Any]:
    if not 1 <= due_in_days <= 120:
        raise BusinessRuleError("due_in_days must be between 1 and 120")
    try:
        with admin_connection(settings or get_settings()) as conn, conn.transaction():
            order = conn.execute(
                "SELECT order_id, customer_id, status, total_amount FROM orders WHERE order_id = %s FOR UPDATE",
                (order_id,),
            ).fetchone()
            if not order:
                raise NotFoundError(f"Order {order_id} not found")
            if order["status"] not in INVOICEABLE_STATUSES:
                raise BusinessRuleError(f"Order {order_id} is '{order['status']}' and cannot be invoiced")
            if conn.execute("SELECT 1 FROM invoices WHERE order_id = %s", (order_id,)).fetchone():
                raise BusinessRuleError(f"Order {order_id} already has an invoice")
            inv = conn.execute(
                """
                INSERT INTO invoices (invoice_number, order_id, customer_id, issue_date, due_date, amount, status)
                VALUES ('PENDING-' || %(o)s, %(o)s, %(c)s, CURRENT_DATE, CURRENT_DATE + %(d)s, %(a)s, 'sent')
                RETURNING invoice_id
                """,
                {"o": order_id, "c": order["customer_id"], "d": due_in_days, "a": order["total_amount"]},
            ).fetchone()
            number = f"INV-{dt.date.today().year}-{inv['invoice_id']:06d}"
            row = conn.execute(
                "UPDATE invoices SET invoice_number = %s WHERE invoice_id = %s RETURNING invoice_id, "
                "invoice_number, order_id, customer_id, issue_date, due_date, amount, status",
                (number, inv["invoice_id"]),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise BusinessRuleError(f"Order {order_id} already has an invoice") from exc
    logger.info("invoice created %s for order %s", number, order_id)
    return _clean(row)  # type: ignore[return-value]
