"""Internal REST API ("company systems") that the agent calls as tools.

Protected by a shared secret header (``X-Internal-Key``); in production this
would sit on a private network behind service-to-service auth.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.business_api import service
from app.config import get_settings

OrderStatus = str


def require_internal_key(x_internal_key: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().internal_api_key
    if not x_internal_key or not hmac.compare_digest(x_internal_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid internal API key")


router = APIRouter(prefix="/internal/v1", tags=["internal business API"], dependencies=[Depends(require_internal_key)])


class StatusUpdate(BaseModel):
    status: str = Field(pattern="^(pending|processing|shipped|delivered|cancelled)$")


class InvoiceCreate(BaseModel):
    order_id: int = Field(gt=0)
    due_in_days: int = Field(default=30, ge=1, le=120)


def _handle(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except service.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.BusinessRuleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/customers/{customer_id}")
def get_customer(customer_id: int) -> dict[str, Any]:
    return _handle(service.get_customer, customer_id)


@router.get("/customers")
def search_customers(search: Annotated[str, Query(min_length=2, max_length=80)]) -> list[dict[str, Any]]:
    return _handle(service.search_customers, search)


@router.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, Any]:
    return _handle(service.get_order, order_id)


@router.patch("/orders/{order_id}/status")
def update_order_status(order_id: int, body: StatusUpdate) -> dict[str, Any]:
    return _handle(service.update_order_status, order_id, body.status)


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceCreate) -> dict[str, Any]:
    return _handle(service.create_invoice, body.order_id, body.due_in_days)
