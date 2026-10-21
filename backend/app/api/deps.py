"""Dependency wiring: builds the long-lived services once and exposes them to routes.

Services are created lazily on first use (not at import), so the API starts even
while the database is still booting, and tests can swap in fakes via
``app.dependency_overrides``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.agent.business_client import BusinessAPIClient
from app.agent.confirmations import PostgresActionStore
from app.agent.loop import AgentRunner
from app.config import get_settings
from app.db import readonly_connection
from app.llm.factory import get_llm_client
from app.schema_rag.introspect import introspect_schema
from app.schema_rag.retriever import build_retriever
from app.security.audit import AuditSink, PostgresAuditLogger
from app.security.auth import AuthError, CurrentUser, decode_token
from app.sql.pipeline import TextToSQLPipeline

logger = logging.getLogger(__name__)


@dataclass
class Services:
    pipeline: TextToSQLPipeline
    agent: AgentRunner
    audit: AuditSink


_services: Services | None = None
_lock = threading.Lock()


def build_services() -> Services:
    settings = get_settings()
    with readonly_connection(settings) as conn:
        tables = introspect_schema(conn)
    retriever = build_retriever(tables, settings.rag_backend, settings.chroma_path or None)
    llm = get_llm_client(settings)
    pipeline = TextToSQLPipeline(
        llm,
        retriever,
        max_rows=settings.max_result_rows,
        repair_attempts=settings.sql_repair_attempts,
        top_k=settings.rag_top_k,
    )
    audit = PostgresAuditLogger(settings)
    agent = AgentRunner(llm, pipeline, BusinessAPIClient.from_settings(settings), PostgresActionStore(settings), audit)
    logger.info(
        "services ready: llm=%s model=%s rag=%s tables=%d",
        settings.llm_provider,
        llm.model,
        settings.rag_backend,
        len(tables),
    )
    return Services(pipeline, agent, audit)


def get_services() -> Services:
    global _services
    if _services is None:
        with _lock:
            if _services is None:
                _services = build_services()
    return _services


def get_current_user(authorization: Annotated[str | None, Header()] = None) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        return decode_token(authorization.split(" ", 1)[1], get_settings())
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


UserDep = Annotated[CurrentUser, Depends(get_current_user)]
ServicesDep = Annotated[Services, Depends(get_services)]
