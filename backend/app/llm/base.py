"""Provider-neutral LLM interface.

The rest of the app talks to :class:`LLMClient` only, so switching between
Claude, OpenAI, a local Ollama model, or a scripted fake for tests is a config
change. Messages use a small neutral format:

* ``{"role": "user", "content": str}``
* ``{"role": "assistant", "content": str, "tool_calls": [ToolCall], "provider_content": Any}``
* ``{"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}``

``provider_content`` keeps the provider's raw assistant blocks (for Claude this
includes thinking blocks, which must be sent back unchanged in tool loops).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """Raised when the LLM provider fails or refuses."""


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None
    provider_content: Any = None

    def as_assistant_message(self) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": self.text,
            "tool_calls": self.tool_calls,
            "provider_content": self.provider_content,
        }


Message = dict[str, Any]


class LLMClient(Protocol):
    """Minimal surface every provider implements."""

    model: str

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec] | None = None, max_tokens: int = 4000
    ) -> LLMResponse: ...
