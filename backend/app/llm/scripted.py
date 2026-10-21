"""Deterministic fake LLM for unit tests, red-team tests and offline demos.

It lets us test the *system around the model* (validation, RBAC, confirmation,
audit) exactly, including simulating a compromised model that emits malicious
SQL or tool calls.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.llm.base import LLMResponse, Message, ToolSpec

Responder = Callable[[str, list[Message], list[ToolSpec] | None], LLMResponse]


class ScriptedLLM:
    """Returns queued responses in order, or delegates to a responder function."""

    model = "scripted"

    def __init__(self, responses: list[LLMResponse | str] | None = None, responder: Responder | None = None) -> None:
        self._queue: list[LLMResponse] = [
            r if isinstance(r, LLMResponse) else LLMResponse(text=r) for r in (responses or [])
        ]
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec] | None = None, max_tokens: int = 4000
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if self._queue:
            return self._queue.pop(0)
        if self._responder:
            return self._responder(system, messages, tools)
        return LLMResponse(text="I don't have a scripted answer for that.")
