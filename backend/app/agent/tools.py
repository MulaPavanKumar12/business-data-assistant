"""Tool registry: JSON-schema definitions the LLM sees + the Python that runs them.

``mutates=True`` marks tools that change data. The agent loop never executes
those directly; it creates a pending action that the user must confirm.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.business_client import BusinessAPIClient
from app.llm.base import ToolSpec
from app.security.rbac import RolePolicy
from app.sql.pipeline import AskResult, TextToSQLPipeline


@dataclass
class ToolContext:
    policy: RolePolicy
    pipeline: TextToSQLPipeline
    business: BusinessAPIClient
    ask_results: list[AskResult] = field(default_factory=list)


Handler = Callable[[dict[str, Any], ToolContext], dict[str, Any]]
Previewer = Callable[[dict[str, Any], ToolContext], str]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: Handler
    mutates: bool = False
    preview: Previewer | None = None


class ToolArgumentError(ValueError):
    pass


def validate_args(spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Minimal JSON-schema check (required keys, types, enums, ranges) before running a tool."""
    schema = spec.parameters
    props = schema.get("properties", {})
    unknown = set(args) - set(props)
    if unknown:
        raise ToolArgumentError(f"Unknown arguments: {sorted(unknown)}")
    for name in schema.get("required", []):
        if name not in args:
            raise ToolArgumentError(f"Missing required argument '{name}'")
    clean: dict[str, Any] = {}
    for name, value in args.items():
        p = props[name]
        if p.get("type") == "integer":
            if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).lstrip("-").isdigit():
                raise ToolArgumentError(f"'{name}' must be an integer")
            value = int(value)
            if "minimum" in p and value < p["minimum"] or "maximum" in p and value > p["maximum"]:
                raise ToolArgumentError(f"'{name}' is out of range")
        elif p.get("type") == "string":
            if not isinstance(value, str) or not value.strip():
                raise ToolArgumentError(f"'{name}' must be a non-empty string")
            if len(value) > p.get("maxLength", 500):
                raise ToolArgumentError(f"'{name}' is too long")
        if "enum" in p and value not in p["enum"]:
            raise ToolArgumentError(f"'{name}' must be one of {p['enum']}")
        clean[name] = value
    return clean


# ----------------------------------------------------------------- handlers
def _query_database(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = ctx.pipeline.ask(args["question"], ctx.policy)
    ctx.ask_results.append(result)
    payload: dict[str, Any] = {"status": result.status, "sql": result.sql, "row_count": result.row_count}
    if result.status == "success":
        payload["columns"] = result.columns
        payload["rows"] = result.rows[:25]  # keep the model's context small; the UI shows the full table
        payload["truncated_for_model"] = result.row_count > 25
    else:
        payload["error"] = result.error
    return payload


def _get_customer(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if "customer_id" in args:
        data = ctx.business.get_customer(args["customer_id"])
    elif "company_name" in args:
        matches = ctx.business.search_customers(args["company_name"])
        if len(matches) != 1:
            return {"matches": matches, "note": "Ask the user which customer they mean." if matches else "No match."}
        data = ctx.business.get_customer(matches[0]["customer_id"])
    else:
        raise ToolArgumentError("Provide customer_id or company_name")
    for col in ctx.policy.hidden_columns.get("customers", ()):  # RBAC also applies to API data
        data.pop(col, None)
    return data


def _get_order(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return ctx.business.get_order(args["order_id"])


def _update_order_status(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return ctx.business.update_order_status(args["order_id"], args["status"])


def _create_invoice(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return ctx.business.create_invoice(args["order_id"], args.get("due_in_days", 30))


def _preview_status(args: dict[str, Any], ctx: ToolContext) -> str:
    order = ctx.business.get_order(args["order_id"])
    return (
        f"Change order #{order['order_id']} for {order['company_name']} "
        f"(${order['total_amount']:,.2f}) from '{order['status']}' to '{args['status']}'."
    )


def _preview_invoice(args: dict[str, Any], ctx: ToolContext) -> str:
    order = ctx.business.get_order(args["order_id"])
    if order.get("invoice"):
        return f"Order #{order['order_id']} already has invoice {order['invoice']['invoice_number']} (this will fail)."
    return (
        f"Create an invoice for order #{order['order_id']} ({order['company_name']}) for "
        f"${order['total_amount']:,.2f}, due in {args.get('due_in_days', 30)} days."
    )


TOOLS: dict[str, Tool] = {
    "query_database": Tool(
        ToolSpec(
            "query_database",
            "Answer an analytical question from the company database (revenue, top customers, trends, counts, "
            "overdue invoices...). Pass a complete, standalone question in plain English; the system writes and runs "
            "safe SQL and returns the rows.",
            {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "maxLength": 500, "description": "Standalone business question"}
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        ),
        _query_database,
    ),
    "get_customer_details": Tool(
        ToolSpec(
            "get_customer_details",
            "Fetch one customer's profile and account summary (lifetime revenue, outstanding balance, last order) "
            "by customer_id or by company_name.",
            {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer", "minimum": 1},
                    "company_name": {"type": "string", "maxLength": 120},
                },
                "additionalProperties": False,
            },
        ),
        _get_customer,
    ),
    "get_order_details": Tool(
        ToolSpec(
            "get_order_details",
            "Fetch one order with its line items and invoice by order_id.",
            {
                "type": "object",
                "properties": {"order_id": {"type": "integer", "minimum": 1}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        _get_order,
    ),
    "update_order_status": Tool(
        ToolSpec(
            "update_order_status",
            "Change an order's status. Allowed transitions: pending->processing|cancelled, processing->shipped|cancelled, "
            "shipped->delivered. Requires user confirmation.",
            {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "minimum": 1},
                    "status": {"type": "string", "enum": ["processing", "shipped", "delivered", "cancelled"]},
                },
                "required": ["order_id", "status"],
                "additionalProperties": False,
            },
        ),
        _update_order_status,
        mutates=True,
        preview=_preview_status,
    ),
    "create_invoice": Tool(
        ToolSpec(
            "create_invoice",
            "Create and send an invoice for an order that does not have one yet (order must be processing, shipped or "
            "delivered). Requires user confirmation.",
            {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "minimum": 1},
                    "due_in_days": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        _create_invoice,
        mutates=True,
        preview=_preview_invoice,
    ),
}


def tools_for(policy: RolePolicy) -> list[Tool]:
    """Only the tools this role may use are ever shown to the model."""
    return [t for name, t in TOOLS.items() if policy.can_use_tool(name)]


def to_tool_message_content(payload: dict[str, Any]) -> str:
    """Serialise a tool result for the model, marking it as data (indirect-injection defence)."""
    return "<tool_result_data>\n" + json.dumps(payload, default=str)[:12000] + "\n</tool_result_data>"
