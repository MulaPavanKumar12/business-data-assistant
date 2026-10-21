"""Schema retrieval (unit + integration recall) and automatic chart selection."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
import yaml

from app.schema_rag.introspect import ColumnDoc, TableDoc, introspect_schema
from app.schema_rag.retriever import SchemaRetriever, build_retriever, tokenize
from app.viz.chart_spec import choose_chart


def _t(name: str, desc: str, cols: list[tuple[str, str | None]]) -> TableDoc:
    return TableDoc(name, desc, [ColumnDoc(c, "integer", False, f"{c} column", references=ref) for c, ref in cols])


TOY = [
    _t("customers", "Business customers", [("customer_id", None), ("segment", None)]),
    _t("orders", "Sales orders with revenue", [("order_id", None), ("customer_id", "customers.customer_id")]),
    _t("order_items", "Order lines", [("order_id", "orders.order_id"), ("product_id", "products.product_id")]),
    _t("products", "Products we sell", [("product_id", None), ("unit_price", None)]),
    _t("employees", "Staff", [("employee_id", None), ("salary", None)]),
]


def test_tokenize_splits_snake_case_and_drops_stopwords() -> None:
    assert tokenize("What is the total_amount of orders?") == ["total", "amount", "orders"]


def test_synonyms_and_bridge_tables() -> None:
    r = SchemaRetriever(TOY)
    res = r.retrieve("revenue from clients for each product", k=3)
    assert {"customers", "products"} <= set(res.tables)
    assert {"orders", "order_items"} <= set(res.tables)  # join path added automatically


def test_rbac_filter_hides_tables() -> None:
    res = SchemaRetriever(TOY).retrieve("average salary of staff", allowed_tables={"customers", "orders"})
    assert "employees" not in res.tables


def test_render_schema_hides_columns() -> None:
    text = SchemaRetriever(TOY).render_schema(["employees"], {"employees": {"salary"}})
    assert "employee_id" in text and "salary" not in text


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["keyword", "hybrid"])
def test_retrieval_recall_on_eval_set(readonly_conn: psycopg.Connection, backend: str) -> None:
    """Phase 2 'done' criterion: the tables needed by gold SQL are retrieved for >= 95% of questions."""
    from eval.run_eval import tables_in

    tables = introspect_schema(readonly_conn)
    retriever = build_retriever(tables, backend)
    questions = yaml.safe_load((Path(__file__).resolve().parents[1] / "eval" / "questions.yaml").read_text())[
        "questions"
    ]
    hits = sum(tables_in(q["sql"]) <= set(retriever.retrieve(q["question"]).tables) for q in questions)
    assert hits / len(questions) >= 0.95, f"{backend}: {hits}/{len(questions)}"


@pytest.mark.integration
def test_introspection_reads_comments_keys_and_enums(readonly_conn: psycopg.Connection) -> None:
    tables = {t.name: t for t in introspect_schema(readonly_conn)}
    status = next(c for c in tables["orders"].columns if c.name == "status")
    assert "cancelled" in status.sample_values
    assert ("customer_id", "customers.customer_id") in tables["orders"].foreign_keys
    assert not any(c.sample_values for c in tables["customers"].columns if c.name in ("email", "phone"))


# ------------------------------------------------------------------ charts
def test_chart_metric_for_single_number() -> None:
    assert choose_chart(["total"], ["number"], [[42.0]]).type == "metric"


def test_chart_line_for_time_series() -> None:
    spec = choose_chart(["month", "revenue"], ["date", "number"], [["2026-01-01", 1.0], ["2026-02-01", 2.0]])
    assert (spec.type, spec.x, spec.y) == ("line", "month", ["revenue"])


def test_chart_bar_for_ranking_and_pie_for_share() -> None:
    rows = [["Acme", 10.0], ["Globex", 5.0], ["Initech", 2.0]]
    assert choose_chart(["company", "revenue"], ["text", "number"], rows, "Top customers").type == "bar"
    assert choose_chart(["segment", "revenue"], ["text", "number"], rows, "Revenue share by segment").type == "pie"


def test_chart_ignores_id_columns_and_falls_back_to_table() -> None:
    assert choose_chart(["customer_id", "name"], ["number", "text"], [[1, "a"], [2, "b"]]).type == "table"
    assert choose_chart([], [], []).type == "table"
