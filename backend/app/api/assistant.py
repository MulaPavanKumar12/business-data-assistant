"""Public assistant endpoints: login, ask (Text-to-SQL), chat (agent), confirm/cancel actions."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import ServicesDep, UserDep
from app.config import get_settings
from app.security.audit import AuditEvent
from app.security.auth import DEMO_USERS, AuthError, authenticate, create_token
from app.security.injection import detect_injection
from app.security.rbac import get_policy

logger = logging.getLogger(__name__)
router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=200)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


@router.post("/auth/token", tags=["auth"])
def login(body: LoginRequest) -> dict[str, Any]:
    try:
        user = authenticate(body.username, body.password, get_settings())
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return {
        "access_token": create_token(user, get_settings()),
        "token_type": "bearer",
        "user": {"username": user.username, "role": user.role, "display_name": user.display_name},
    }


@router.get("/auth/demo-users", tags=["auth"])
def demo_users() -> list[dict[str, str]]:
    """Demo accounts (one per role) for the UI's login screen."""
    return [
        {"username": u, "role": r, "display_name": n, "description": get_policy(r).description}
        for u, (r, n) in DEMO_USERS.items()
    ]


@router.get("/api/v1/me", tags=["auth"])
def me(user: UserDep) -> dict[str, Any]:
    policy = get_policy(user.role)
    return {
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name,
        "tables": sorted(policy.tables),
        "tools": sorted(policy.tools),
        "hidden_columns": {t: sorted(c) for t, c in policy.hidden_columns.items()},
    }


@router.post("/api/v1/ask", tags=["assistant"])
def ask(body: AskRequest, user: UserDep, services: ServicesDep) -> dict[str, Any]:
    """Direct Text-to-SQL: question in, validated SQL + rows + chart out."""
    verdict = detect_injection(body.question)
    if verdict.blocked:
        services.audit.log(
            AuditEvent(
                "injection_blocked",
                "blocked",
                user.username,
                user.role,
                question=body.question,
                reason=", ".join(verdict.reasons),
            )
        )
        return {
            "question": body.question,
            "status": "blocked",
            "error": "Request blocked by security policy",
            "reasons": verdict.reasons,
        }
    result = services.pipeline.ask(body.question, get_policy(user.role))
    services.audit.log(
        AuditEvent(
            "nl_query",
            {"success": "success", "blocked": "blocked"}.get(result.status, "error"),
            user.username,
            user.role,
            question=body.question,
            generated_sql=result.sql,
            reason=result.error,
            row_count=result.row_count,
            latency_ms=int(result.latency_ms),
        )
    )
    return result.to_dict()


@router.post("/api/v1/chat", tags=["assistant"])
def chat(body: ChatRequest, user: UserDep, services: ServicesDep) -> dict[str, Any]:
    """Agent endpoint: answers questions and proposes actions (which need confirmation)."""
    history = [{"role": t.role, "content": t.content} for t in body.history]
    return services.agent.run(user, body.message, history).__dict__


def _decide(action_id: str, approve: bool, user: UserDep, services: ServicesDep) -> dict[str, Any]:
    try:
        return services.agent.confirm(user, action_id, approve)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/api/v1/actions/{action_id}/confirm", tags=["assistant"])
def confirm_action(action_id: str, user: UserDep, services: ServicesDep) -> dict[str, Any]:
    return _decide(action_id, True, user, services)


@router.post("/api/v1/actions/{action_id}/cancel", tags=["assistant"])
def cancel_action(action_id: str, user: UserDep, services: ServicesDep) -> dict[str, Any]:
    return _decide(action_id, False, user, services)
