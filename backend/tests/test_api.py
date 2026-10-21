"""API tests. The 503 path is a unit test; the happy paths need the database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import health as health_module
from app.db import DatabaseUnavailableError
from app.main import app

client = TestClient(app)


def test_health_returns_503_when_database_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_connection(*_args: object, **_kwargs: object) -> None:
        raise DatabaseUnavailableError("down")

    monkeypatch.setattr(health_module, "readonly_connection", broken_connection)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


def test_openapi_docs_are_served() -> None:
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.integration
def test_health_ok_with_seeded_database(readonly_conn: object) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["customers"] > 0 and body["orders"] > 0


@pytest.mark.integration
def test_schema_endpoint_describes_tables(readonly_conn: object) -> None:
    tables = {t["name"]: t for t in client.get("/api/v1/schema").json()}
    assert "orders" in tables
    assert "revenue" in tables["orders"]["description"].lower()
    cols = {c["name"]: c for c in tables["orders"]["columns"]}
    assert cols["total_amount"]["description"]
