"""FastAPI application factory.

Run locally:   uvicorn app.main:app --reload       (from the backend/ folder)
Docs:          http://localhost:8000/docs
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app import __version__
from app.api.assistant import router as assistant_router
from app.api.health import router as health_router
from app.business_api.router import router as business_router
from app.config import get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build the FastAPI app with ops, assistant and internal business API routers."""
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Ask business questions in plain English; get safe SQL, tables and charts.",
    )
    app.include_router(health_router)
    app.include_router(assistant_router)
    app.include_router(business_router)
    logger.info("app started version=%s db_host=%s", __version__, settings.db_host)
    return app


app = create_app()
