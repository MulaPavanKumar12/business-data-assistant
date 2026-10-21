"""Shared pytest fixtures.

Unit tests need nothing. Integration tests (marked ``integration``) need the
Phase 1 stack running; they are skipped automatically if PostgreSQL is
unreachable, so ``pytest`` is always green on a laptop without Docker.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import Settings, get_settings
from app.seed.generator import Dataset, SeedConfig, generate_dataset

# A small, fixed configuration keeps unit tests fast and fully reproducible.
SMALL_CONFIG = SeedConfig(
    seed=7,
    num_customers=60,
    num_employees=30,
    num_products=40,
    num_orders=600,
    end_date=date(2026, 6, 30),
)


@pytest.fixture(scope="session")
def small_dataset() -> Dataset:
    return generate_dataset(SMALL_CONFIG)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


def _connect_or_skip(dsn: str) -> psycopg.Connection:
    try:
        return psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable ({exc.__class__.__name__}); start it with `docker compose up -d`")


@pytest.fixture
def admin_conn(settings: Settings) -> Iterator[psycopg.Connection]:
    conn = _connect_or_skip(settings.admin_dsn)
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def readonly_conn(settings: Settings) -> Iterator[psycopg.Connection]:
    conn = _connect_or_skip(settings.readonly_dsn)
    yield conn
    conn.rollback()
    conn.close()
