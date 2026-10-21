"""Claude implementation of :class:`LLMClient` using the official ``anthropic`` SDK."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from app.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

logger = logging.getLogger(__name__)


class AnthropicClient:
    """Claude via the Messages API with native tool use.

    * ``effort`` controls thinking depth / latency (``low`` suits SQL generation).
    * Server-side refusal fallbacks are on by default: if a request is declined by a
      safety classifier, the API retries it on a fallback model inside the same call.
    """

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        api_key: str | None = None,
        effort: str = "low",
        enable_fallbacks: bool = True,
        timeout_s: float = 60.0,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.enable_fallbacks = enable_fallbacks
        self._client = client or anthropic.Anthropic(api_key=api_key or None, timeout=timeout_s, max_retries=2)

    @staticmethod
    def _to_api_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                if m.get("provider_content") is not None:
                    content = m["provider_content"]  # replay unchanged (keeps thinking blocks valid)
                else:
                    content = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                    content += [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                        for c in m.get("tool_calls", [])
                    ]
                out.append({"role": "assistant", "content": content})
            elif m["role"] == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                    "is_error": bool(m.get("is_error")),
                }
                # All results of one assistant turn go back in ONE user message.
                if (
                    out
                    and out[-1]["role"] == "user"
                    and isinstance(out[-1]["content"], list)
                    and out[-1]["content"]
                    and out[-1]["content"][0].get("type") == "tool_result"
                ):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec] | None = None, max_tokens: int = 4000
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self._to_api_messages(messages),
            "output_config": {"effort": self.effort},
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        try:
            if self.enable_fallbacks:
                resp = self._client.beta.messages.create(
                    betas=[self.FALLBACK_BETA], extra_body={"fallbacks": "default"}, **kwargs
                )
            else:
                resp = self._client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise LLMError("LLM rate limit reached; please retry shortly") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"LLM API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM API") from exc

        if resp.stop_reason == "refusal":
            raise LLMError("The model declined this request")
        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else json.loads(block.input or "{}")
                calls.append(ToolCall(block.id, block.name, args))
        provider_content = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=resp.stop_reason,
            provider_content=provider_content,
        )
