"""RBAC policies and JWT authentication."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.security.auth import AuthError, authenticate, create_token, decode_token
from app.security.rbac import ROLES, UnknownRoleError, get_policy

SETTINGS = Settings(JWT_SECRET="test-secret-test-secret-test-secret", DEMO_USER_PASSWORD="pw")


def test_roles_have_least_privilege() -> None:
    assert not get_policy("viewer").can_read_table("employees")
    assert not get_policy("viewer").can_use_tool("create_invoice")
    assert "salary" in get_policy("analyst").hidden_columns["employees"]
    assert get_policy("finance").can_use_tool("create_invoice")
    assert not get_policy("finance").can_use_tool("update_order_status")
    assert get_policy("operations").can_use_tool("update_order_status")
    assert all(p.can_use_tool("query_database") for p in ROLES.values())


def test_unknown_role_is_denied() -> None:
    with pytest.raises(UnknownRoleError):
        get_policy("superuser")


def test_login_and_token_round_trip() -> None:
    user = authenticate("fiona", "pw", SETTINGS)
    assert user.role == "finance"
    assert decode_token(create_token(user, SETTINGS), SETTINGS) == user


@pytest.mark.parametrize(("username", "password"), [("fiona", "wrong"), ("nobody", "pw"), ("", "")])
def test_bad_credentials(username: str, password: str) -> None:
    with pytest.raises(AuthError):
        authenticate(username, password, SETTINGS)


def test_tampered_or_foreign_token_is_rejected() -> None:
    token = create_token(authenticate("vera", "pw", SETTINGS), SETTINGS)
    header, payload, sig = token.split(".")
    with pytest.raises(AuthError):
        decode_token(f"{header}.{payload}.{sig[:-2]}xx", SETTINGS)
    other = Settings(JWT_SECRET="another-secret-another-secret-another", DEMO_USER_PASSWORD="pw")
    with pytest.raises(AuthError):
        decode_token(token, other)


def test_expired_token_is_rejected() -> None:
    expired = Settings(JWT_SECRET=SETTINGS.jwt_secret, DEMO_USER_PASSWORD="pw", JWT_EXPIRY_MINUTES=-1)
    token = create_token(authenticate("ada", "pw", expired), expired)
    with pytest.raises(AuthError):
        decode_token(token, SETTINGS)
