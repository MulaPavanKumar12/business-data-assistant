"""Provider adapters: request shape and response parsing, using mocked HTTP (no API key needed)."""

from __future__ import annotations

import json

import anthropic
import httpx
import httpx2  # the anthropic SDK 1.x transport package
import openai

from app.llm.anthropic_client import AnthropicClient
from app.llm.base import ToolCall, ToolSpec
from app.llm.openai_client import OpenAICompatibleClient

TOOL = ToolSpec(
    "get_order_details", "Fetch an order", {"type": "object", "properties": {"order_id": {"type": "integer"}}}
)


def test_anthropic_request_and_tool_use_parsing() -> None:
    captured: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = json.loads(request.content)
        captured["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [
                    {"type": "text", "text": "Let me look."},
                    {"type": "tool_use", "id": "toolu_1", "name": "get_order_details", "input": {"order_id": 7}},
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 120, "output_tokens": 30},
            },
        )

    sdk = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    client = AnthropicClient(client=sdk)
    history = [
        {"role": "user", "content": "order 6?"},
        {"role": "assistant", "content": "", "tool_calls": [ToolCall("toolu_0", "get_order_details", {"order_id": 6})]},
        {"role": "tool", "tool_call_id": "toolu_0", "name": "get_order_details", "content": "{}"},
    ]
    resp = client.complete("system prompt", history, tools=[TOOL])

    body = captured["body"]
    assert body["model"] == "claude-opus-5-5" and body["system"] == "system prompt"
    assert body["output_config"] == {"effort": "low"} and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in captured["beta"]
    assert body["tools"][0]["input_schema"]["properties"]["order_id"]["type"] == "integer"
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert resp.tool_calls == [ToolCall("toolu_1", "get_order_details", {"order_id": 7})]
    assert resp.text == "Let me look." and resp.input_tokens == 120
    assert resp.provider_content[1]["type"] == "tool_use"  # kept for replay


def test_anthropic_refusal_raises() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "x",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        )

    sdk = anthropic.Anthropic(api_key="t", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    try:
        AnthropicClient(client=sdk, enable_fallbacks=False).complete("s", [{"role": "user", "content": "x"}])
    except Exception as exc:  # noqa: BLE001
        assert "declined" in str(exc)
    else:
        raise AssertionError("expected LLMError")


def test_openai_compatible_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "system"
        assert body["tools"][0]["function"]["name"] == "get_order_details"
        return httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "get_order_details", "arguments": '{"order_id": 3}'},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    sdk = openai.OpenAI(api_key="t", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    resp = OpenAICompatibleClient(client=sdk).complete("sys", [{"role": "user", "content": "hi"}], tools=[TOOL])
    assert resp.tool_calls == [ToolCall("c1", "get_order_details", {"order_id": 3})]
    assert resp.input_tokens == 10
