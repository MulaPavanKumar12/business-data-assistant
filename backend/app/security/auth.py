"""Authentication with signed JWTs and a small set of demo users (one per role).

In production you would plug in the company's identity provider (OIDC / SSO)
and map its groups to roles; everything downstream only needs ``CurrentUser``.
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
from dataclasses import dataclass

import jwt

from app.config import Settings
from app.security.rbac import ROLES

logger = logging.getLogger(__name__)
ALGORITHM = "HS256"

# username -> (role, display name)
DEMO_USERS: dict[str, tuple[str, str]] = {
    "vera": ("viewer", "Vera (Sales Viewer)"),
    "alex": ("analyst", "Alex (Business Analyst)"),
    "omar": ("operations", "Omar (Operations)"),
    "fiona": ("finance", "Fiona (Finance)"),
    "ada": ("admin", "Ada (Admin)"),
}


class AuthError(Exception):
    """Invalid credentials or token."""


@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: str
    display_name: str


def authenticate(username: str, password: str, settings: Settings) -> CurrentUser:
    entry = DEMO_USERS.get(username.lower().strip())
    if entry is None or not hmac.compare_digest(password.encode(), settings.demo_user_password.encode()):
        raise AuthError("Invalid username or password")
    role, name = entry
    return CurrentUser(username.lower().strip(), role, name)


def create_token(user: CurrentUser, settings: Settings) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": user.username,
        "role": user.role,
        "name": user.display_name,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expiry_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, settings: Settings) -> CurrentUser:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired token") from exc
    if payload.get("role") not in ROLES:
        raise AuthError("Unknown role")
    return CurrentUser(payload["sub"], payload["role"], payload.get("name", payload["sub"]))
