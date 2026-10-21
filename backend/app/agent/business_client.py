"""HTTP client for the internal business API (what the agent's tools call)."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class BusinessAPIError(RuntimeError):
    """A business API call failed. ``message`` is safe to show the model and the user."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class BusinessAPIClient:
    """Thin typed wrapper. Accepts any ``httpx.Client`` (FastAPI's TestClient in tests)."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    @classmethod
    def from_settings(cls, settings: Settings) -> BusinessAPIClient:
        return cls(
            httpx.Client(
                base_url=settings.business_api_url, timeout=10.0, headers={"X-Internal-Key": settings.internal_api_key}
            )
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self.http.request(method, path.lstrip("/"), **kwargs)
        except httpx.HTTPError as exc:
            raise BusinessAPIError(503, "Business API unreachable") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text
            raise BusinessAPIError(resp.status_code, str(detail)[:300])
        return resp.json()

    def get_customer(self, customer_id: int) -> dict[str, Any]:
        return self._request("GET", f"customers/{customer_id}")

    def search_customers(self, name: str) -> list[dict[str, Any]]:
        return self._request("GET", "customers", params={"search": name})

    def get_order(self, order_id: int) -> dict[str, Any]:
        return self._request("GET", f"orders/{order_id}")

    def update_order_status(self, order_id: int, status: str) -> dict[str, Any]:
        return self._request("PATCH", f"orders/{order_id}/status", json={"status": status})

    def create_invoice(self, order_id: int, due_in_days: int = 30) -> dict[str, Any]:
        return self._request("POST", "invoices", json={"order_id": order_id, "due_in_days": due_in_days})
