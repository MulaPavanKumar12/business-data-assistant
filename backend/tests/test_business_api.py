"""Internal business API against the real database (creates and removes its own test order)."""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture
def api(settings) -> TestClient:  # noqa: ANN001
    return TestClient(app, headers={"X-Internal-Key": settings.internal_api_key})


@pytest.fixture
def test_order(admin_conn: psycopg.Connection) -> Iterator[int]:
    row = admin_conn.execute(
        "INSERT INTO orders (customer_id, order_date, status, shipping_country, subtotal, total_amount) "
        "VALUES (1, now(), 'pending', 'Germany', 100, 119) RETURNING order_id"
    ).fetchone()
    admin_conn.commit()
    yield row["order_id"]
    admin_conn.execute("DELETE FROM invoices WHERE order_id = %s", (row["order_id"],))
    admin_conn.execute("DELETE FROM orders WHERE order_id = %s", (row["order_id"],))
    admin_conn.commit()


def test_requires_internal_key() -> None:
    assert TestClient(app).get("/internal/v1/orders/1").status_code == 401
    assert TestClient(app, headers={"X-Internal-Key": "wrong"}).get("/internal/v1/orders/1").status_code == 401


def test_get_customer_and_search(api: TestClient) -> None:
    c = api.get("/internal/v1/customers/1").json()
    assert c["customer_id"] == 1 and "lifetime_revenue" in c and "outstanding_balance" in c
    name = c["company_name"].split()[0]
    assert any(m["customer_id"] == 1 for m in api.get("/internal/v1/customers", params={"search": name}).json())
    assert api.get("/internal/v1/customers/999999").status_code == 404


def test_order_status_state_machine(api: TestClient, test_order: int) -> None:
    url = f"/internal/v1/orders/{test_order}/status"
    assert api.patch(url, json={"status": "delivered"}).status_code == 409  # pending -> delivered not allowed
    r = api.patch(url, json={"status": "processing"}).json()
    assert r == {"order_id": test_order, "old_status": "pending", "new_status": "processing"}
    assert api.patch(url, json={"status": "bogus"}).status_code == 422


def test_create_invoice_rules(api: TestClient, test_order: int) -> None:
    assert api.post("/internal/v1/invoices", json={"order_id": test_order}).status_code == 409  # still pending
    api.patch(f"/internal/v1/orders/{test_order}/status", json={"status": "processing"})
    created = api.post("/internal/v1/invoices", json={"order_id": test_order, "due_in_days": 45})
    assert created.status_code == 201
    body = created.json()
    assert body["invoice_number"].startswith("INV-") and body["amount"] == 119.0 and body["status"] == "sent"
    assert api.post("/internal/v1/invoices", json={"order_id": test_order}).status_code == 409  # no duplicates
    assert api.get(f"/internal/v1/orders/{test_order}").json()["invoice"]["invoice_number"] == body["invoice_number"]
