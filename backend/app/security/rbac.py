"""Role-based access control: which tables, columns and tools each role may use.

The same :class:`RolePolicy` is enforced in three places, so a mistake in one
layer is caught by the next:

1. **Retrieval:** hidden tables/columns are never shown to the LLM.
2. **SQL validation:** generated SQL touching anything outside the policy is blocked.
3. **Agent tools:** the model is only offered tools the role may call, and every
   call is re-checked before it executes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

ALL_BUSINESS_TABLES: Final[frozenset[str]] = frozenset(
    {
        "departments",
        "employees",
        "customers",
        "product_categories",
        "products",
        "orders",
        "order_items",
        "invoices",
        "payments",
    }
)


@dataclass(frozen=True)
class RolePolicy:
    name: str
    description: str
    tables: frozenset[str]
    hidden_columns: dict[str, frozenset[str]] = field(default_factory=dict)
    tools: frozenset[str] = frozenset()

    def can_read_table(self, table: str) -> bool:
        return table in self.tables

    def can_use_tool(self, tool: str) -> bool:
        return tool in self.tools


READ_TOOLS = frozenset({"query_database", "get_customer_details", "get_order_details"})

ROLES: Final[dict[str, RolePolicy]] = {
    "viewer": RolePolicy(
        "viewer",
        "Read-only sales dashboard user. No PII, no finance, no HR data.",
        frozenset({"products", "product_categories", "orders", "order_items", "customers"}),
        {"customers": frozenset({"email", "phone", "credit_limit", "contact_name"})},
        frozenset({"query_database", "get_order_details"}),
    ),
    "analyst": RolePolicy(
        "analyst",
        "Business analyst. All business data except salaries.",
        ALL_BUSINESS_TABLES,
        {"employees": frozenset({"salary"})},
        READ_TOOLS,
    ),
    "operations": RolePolicy(
        "operations",
        "Operations team. Orders and products; may update order status.",
        frozenset({"products", "product_categories", "orders", "order_items", "customers"}),
        {"customers": frozenset({"credit_limit"})},
        READ_TOOLS | {"update_order_status"},
    ),
    "finance": RolePolicy(
        "finance",
        "Finance team. All data including salaries; may create invoices.",
        ALL_BUSINESS_TABLES,
        {},
        READ_TOOLS | {"create_invoice"},
    ),
    "admin": RolePolicy(
        "admin",
        "Administrator. Everything.",
        ALL_BUSINESS_TABLES,
        {},
        READ_TOOLS | {"create_invoice", "update_order_status"},
    ),
}


class UnknownRoleError(KeyError):
    """Raised for a role name that has no policy (deny by default)."""


def get_policy(role: str) -> RolePolicy:
    try:
        return ROLES[role]
    except KeyError as exc:
        raise UnknownRoleError(role) from exc
