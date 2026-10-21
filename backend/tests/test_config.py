"""Unit tests for settings and DSN construction."""

from __future__ import annotations

from app.config import Settings


def test_dsns_use_separate_identities() -> None:
    s = Settings(
        POSTGRES_USER="owner",
        POSTGRES_PASSWORD="pw1",
        READONLY_DB_USER="reader",
        READONLY_DB_PASSWORD="pw2",
        DB_HOST="dbhost",
        POSTGRES_PORT=6543,
        POSTGRES_DB="biz",
    )
    assert s.admin_dsn == "postgresql://owner:pw1@dbhost:6543/biz"
    assert s.readonly_dsn == "postgresql://reader:pw2@dbhost:6543/biz"


def test_special_characters_in_password_are_escaped() -> None:
    s = Settings(READONLY_DB_PASSWORD="p@ss:w/rd#1")
    assert "p%40ss%3Aw%2Frd%231" in s.readonly_dsn
