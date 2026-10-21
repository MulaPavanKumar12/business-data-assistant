"""Pending actions: data-changing tool calls wait here until the user confirms.

Stored in ``audit.pending_actions`` so a confirmation survives restarts and
works across API workers. Each action is bound to the user who requested it
and expires after a short TTL.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.db import admin_connection
from app.sql.executor import to_jsonable


@dataclass
class PendingAction:
    action_id: str
    user_id: str
    user_role: str
    tool_name: str
    tool_args: dict[str, Any]
    summary: str
    status: str  # pending | executed | cancelled | failed | expired
    created_at: dt.datetime
    expires_at: dt.datetime
    result: dict[str, Any] | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "summary": self.summary,
            "status": self.status,
            "expires_at": self.expires_at.isoformat(),
            "result": self.result,
        }


class ActionStore(Protocol):
    def create(
        self, user_id: str, user_role: str, tool_name: str, tool_args: dict[str, Any], summary: str
    ) -> PendingAction: ...
    def get(self, action_id: str) -> PendingAction | None: ...
    def finish(self, action_id: str, status: str, result: dict[str, Any] | None = None) -> None: ...


class InMemoryActionStore:
    def __init__(self, ttl_minutes: int = 15) -> None:
        self.ttl = dt.timedelta(minutes=ttl_minutes)
        self.items: dict[str, PendingAction] = {}

    def create(
        self, user_id: str, user_role: str, tool_name: str, tool_args: dict[str, Any], summary: str
    ) -> PendingAction:
        now = dt.datetime.now(dt.UTC)
        action = PendingAction(
            str(uuid.uuid4()), user_id, user_role, tool_name, tool_args, summary, "pending", now, now + self.ttl
        )
        self.items[action.action_id] = action
        return action

    def get(self, action_id: str) -> PendingAction | None:
        return self.items.get(action_id)

    def finish(self, action_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        if action_id in self.items:
            self.items[action_id].status = status
            self.items[action_id].result = result


class PostgresActionStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def create(
        self, user_id: str, user_role: str, tool_name: str, tool_args: dict[str, Any], summary: str
    ) -> PendingAction:
        with admin_connection(self.settings) as conn:
            row = conn.execute(
                """INSERT INTO audit.pending_actions (user_id, user_role, tool_name, tool_args, summary, expires_at)
                   VALUES (%s, %s, %s, %s, %s, now() + make_interval(mins => %s)) RETURNING *""",
                (
                    user_id,
                    user_role,
                    tool_name,
                    json.dumps(tool_args),
                    summary,
                    self.settings.pending_action_ttl_minutes,
                ),
            ).fetchone()
            conn.commit()
        return self._from_row(row)

    def get(self, action_id: str) -> PendingAction | None:
        try:
            uuid.UUID(action_id)
        except ValueError:
            return None
        with admin_connection(self.settings) as conn:
            row = conn.execute("SELECT * FROM audit.pending_actions WHERE action_id = %s", (action_id,)).fetchone()
        return self._from_row(row) if row else None

    def finish(self, action_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        with admin_connection(self.settings) as conn:
            conn.execute(
                "UPDATE audit.pending_actions SET status = %s, result = %s, decided_at = now() "
                "WHERE action_id = %s AND status = 'pending'",
                (status, json.dumps(result, default=str) if result is not None else None, action_id),
            )
            conn.commit()

    @staticmethod
    def _from_row(row: dict[str, Any]) -> PendingAction:
        return PendingAction(
            str(row["action_id"]),
            row["user_id"],
            row["user_role"],
            row["tool_name"],
            row["tool_args"],
            row["summary"],
            row["status"],
            row["created_at"],
            row["expires_at"],
            {k: to_jsonable(v) for k, v in row["result"].items()} if row.get("result") else None,
        )
