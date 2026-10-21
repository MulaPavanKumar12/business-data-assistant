"""Append-only audit trail in the ``audit.events`` table.

Written with the admin identity, never readable by the AI's read-only role.
Audit failures are logged but never break the user's request (availability),
while still being visible in the application logs.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg

from app.config import Settings, get_settings
from app.db import DatabaseUnavailableError, admin_connection

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    event_type: str  # nl_query | sql_blocked | injection_blocked | tool_call | action_pending | action_confirmed ...
    outcome: str  # success | blocked | error | pending_confirmation | cancelled
    user_id: str | None = None
    user_role: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    question: str | None = None
    generated_sql: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    reason: str | None = None
    row_count: int | None = None
    latency_ms: int | None = None


class AuditSink(Protocol):
    def log(self, event: AuditEvent) -> None: ...


class PostgresAuditLogger:
    """Writes events to audit.events."""

    INSERT = """
        INSERT INTO audit.events (request_id, user_id, user_role, event_type, question, generated_sql,
                                  tool_name, tool_args, outcome, reason, row_count, latency_ms)
        VALUES (%(request_id)s, %(user_id)s, %(user_role)s, %(event_type)s, %(question)s, %(generated_sql)s,
                %(tool_name)s, %(tool_args)s, %(outcome)s, %(reason)s, %(row_count)s, %(latency_ms)s)
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def log(self, event: AuditEvent) -> None:
        params = event.__dict__ | {
            "tool_args": json.dumps(event.tool_args, default=str) if event.tool_args else None,
            "reason": (event.reason or "")[:2000] or None,
        }
        try:
            with admin_connection(self.settings) as conn:
                conn.execute(self.INSERT, params)
                conn.commit()
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.error("AUDIT WRITE FAILED event=%s outcome=%s err=%s", event.event_type, event.outcome, exc)


class MemoryAuditLogger:
    """In-memory sink for tests."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def log(self, event: AuditEvent) -> None:
        self.events.append(event)

    def of_type(self, event_type: str) -> list[AuditEvent]:
        return [e for e in self.events if e.event_type == event_type]
