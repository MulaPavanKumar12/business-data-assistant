"""OpenAI-compatible implementation (OpenAI, Azure OpenAI, or a local Ollama server)."""

from __future__ import annotations

import json
import logging
from typing import Any

import openai

from app.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

logger = logging.getLogger(__name__)


class OpenAICompatibleClient:
    """Chat Completions with tool calling. Set ``base_url`` for Ollama (http://host:11434/v1)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 60.0,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.model = model
        self._client = client or openai.OpenAI(
            api_key=api_key or "not-needed-for-ollama", base_url=base_url, timeout=timeout_s, max_retries=2
        )

    @staticmethod
    def _to_api_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return out

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec] | None = None, max_tokens: int = 4000
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": self._to_api_messages(system, messages),
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise LLMError("LLM rate limit reached; please retry shortly") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"LLM API error {exc.status_code}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM API") from exc
        choice = resp.choices[0]
        calls = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        usage = resp.usage
        return LLMResponse(
            text=(choice.message.content or "").strip(),
            tool_calls=calls,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            stop_reason=choice.finish_reason,
        )
