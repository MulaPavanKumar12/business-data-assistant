"""Unit tests for the deterministic data generator (no database needed).

These tests protect the *business invariants* the evaluation set relies on in
Phase 4: if order totals or invoice amounts drift, "expected results" break.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from app.seed.generator import TABLE_COLUMNS, Dataset, SeedConfig, generate_dataset, money, slugify
from tests.conftest import SMALL_CONFIG


def test_generation_is_deterministic() -> None:
    a = generate_dataset(SMALL_CONFIG)
    b = generate_dataset(SMALL_CONFIG)
    assert a.tables == b.tables


def test_different_seed_gives_different_data() -> None:
    other = SeedConfig(**{**SMALL_CONFIG.__dict__, "seed": 8})
    assert generate_dataset(other)["customers"] != generate_dataset(SMALL_CONFIG)["customers"]


def test_row_counts_match_config(small_dataset: Dataset) -> None:
    assert len(small_dataset["customers"]) == SMALL_CONFIG.num_customers
    assert len(small_dataset["products"]) == SMALL_CONFIG.num_products
    assert len(small_dataset["orders"]) == SMALL_CONFIG.num_orders
    assert len(small_dataset["employees"]) == SMALL_CONFIG.num_employees
    assert len(small_dataset["order_items"]) >= SMALL_CONFIG.num_orders


def test_every_row_has_exactly_the_table_columns(small_dataset: Dataset) -> None:
    for table, columns in TABLE_COLUMNS.items():
        for row in small_dataset[table]:
            assert tuple(row) == columns, f"{table} row keys differ from TABLE_COLUMNS"


def test_primary_keys_are_unique_and_sequential(small_dataset: Dataset) -> None:
    for table, columns in TABLE_COLUMNS.items():
        ids = [row[columns[0]] for row in small_dataset[table]]
        assert ids == list(range(1, len(ids) + 1)), table


def test_foreign_keys_resolve(small_dataset: Dataset) -> None:
    ids = {t: {r[c[0]] for r in small_dataset[t]} for t, c in TABLE_COLUMNS.items()}
    for e in small_dataset["employees"]:
        assert e["department_id"] in ids["departments"]
        assert e["manager_id"] is None or e["manager_id"] in ids["employees"]
    for c in small_dataset["customers"]:
        assert c["account_manager_id"] in ids["employees"]
    for o in small_dataset["orders"]:
        assert o["customer_id"] in ids["customers"]
        assert o["sales_rep_id"] in ids["employees"]
    for i in small_dataset["order_items"]:
        assert i["order_id"] in ids["orders"] and i["product_id"] in ids["products"]
    for inv in small_dataset["invoices"]:
        assert inv["order_id"] in ids["orders"] and inv["customer_id"] in ids["customers"]
    for p in small_dataset["payments"]:
        assert p["invoice_id"] in ids["invoices"]


def test_unique_business_keys(small_dataset: Dataset) -> None:
    for table, col in [
        ("customers", "email"),
        ("employees", "email"),
        ("products", "sku"),
        ("invoices", "invoice_number"),
        ("invoices", "order_id"),
        ("payments", "reference"),
    ]:
        values = [r[col] for r in small_dataset[table]]
        assert len(values) == len(set(values)), f"duplicate {table}.{col}"


def test_order_money_math_is_consistent(small_dataset: Dataset) -> None:
    lines = defaultdict(Decimal)
    for item in small_dataset["order_items"]:
        assert item["line_total"] == money(item["unit_price"] * item["quantity"])
        lines[item["order_id"]] += item["line_total"]
    for o in small_dataset["orders"]:
        assert o["subtotal"] == lines[o["order_id"]]
        assert o["total_amount"] == o["subtotal"] - o["discount_amount"] + o["tax_amount"]
        assert o["total_amount"] > 0


def test_products_are_priced_above_cost(small_dataset: Dataset) -> None:
    for p in small_dataset["products"]:
        assert Decimal("0") < p["unit_cost"] <= p["unit_price"]


def test_invoices_follow_orders(small_dataset: Dataset) -> None:
    orders = {o["order_id"]: o for o in small_dataset["orders"]}
    for inv in small_dataset["invoices"]:
        order = orders[inv["order_id"]]
        assert order["status"] in ("shipped", "delivered")
        assert inv["amount"] == order["total_amount"]
        assert inv["customer_id"] == order["customer_id"]
        assert inv["due_date"] >= inv["issue_date"] >= order["order_date"].date()
        assert (inv["status"] == "paid") == (inv["paid_date"] is not None)


def test_paid_invoices_are_fully_paid(small_dataset: Dataset) -> None:
    paid = defaultdict(Decimal)
    for p in small_dataset["payments"]:
        paid[p["invoice_id"]] += p["amount"]
    for inv in small_dataset["invoices"]:
        expected = inv["amount"] if inv["status"] == "paid" else Decimal("0")
        assert paid[inv["invoice_id"]] == expected, inv["invoice_number"]


def test_dates_stay_inside_history_window(small_dataset: Dataset) -> None:
    for o in small_dataset["orders"]:
        assert SMALL_CONFIG.start_date <= o["order_date"].date() <= SMALL_CONFIG.end_date
    for p in small_dataset["payments"]:
        assert p["payment_date"] <= SMALL_CONFIG.end_date


def test_order_ids_increase_with_order_date(small_dataset: Dataset) -> None:
    dates = [o["order_date"] for o in small_dataset["orders"]]
    assert dates == sorted(dates)


def test_prospects_have_no_orders_and_churned_stopped_buying(small_dataset: Dataset) -> None:
    customers = {c["customer_id"]: c for c in small_dataset["customers"]}
    for o in small_dataset["orders"]:
        cust = customers[o["customer_id"]]
        assert cust["status"] != "prospect"
        if cust["status"] == "churned":
            assert (SMALL_CONFIG.end_date - o["order_date"].date()).days >= 180 or o["order_date"].date() <= cust[
                "created_at"
            ].date()


def test_enterprise_orders_are_bigger_than_smb() -> None:
    data = generate_dataset(SeedConfig(seed=1, num_orders=3000, end_date=date(2026, 6, 30)))
    segment = {c["customer_id"]: c["segment"] for c in data["customers"]}
    totals: dict[str, list[Decimal]] = defaultdict(list)
    for o in data["orders"]:
        totals[segment[o["customer_id"]]].append(o["total_amount"])
    avg = {s: sum(v) / len(v) for s, v in totals.items()}
    assert avg["Enterprise"] > avg["SMB"]


def test_some_invoices_are_overdue_for_realistic_ar(small_dataset: Dataset) -> None:
    statuses = {inv["status"] for inv in small_dataset["invoices"]}
    assert {"paid", "overdue"} <= statuses


def test_q4_is_busier_than_q1_on_average() -> None:
    data = generate_dataset(SeedConfig(seed=3, num_orders=6000, end_date=date(2026, 6, 30)))
    by_q: dict[int, int] = defaultdict(int)
    for o in data["orders"]:
        by_q[(o["order_date"].month - 1) // 3 + 1] += 1
    assert by_q[4] > by_q[1]


@pytest.mark.parametrize("bad", [{"num_customers": 1}, {"num_orders": 0}, {"history_years": 0}])
def test_invalid_config_is_rejected(bad: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        SeedConfig(**bad)


@pytest.mark.parametrize(("raw", "expected"), [("Müller & Söhne GmbH", "mullersohnegmbh"), ("!!!", "company")])
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected
