"""FastAPI application factory.

The modular monolith's composition root (AD-01). Domain routers are mounted here
as they arrive; M1.1 has only the health router.

Note what is *not* here: no authentication, no authorisation, no domain routes.
Those land in M1.3–M1.6. Every route added from M1.4 onwards must carry an
explicit permission dependency, enforced by the route-coverage test
(docs/IMPLEMENTATION_PLAN.md M1.4).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware
from app.db.session import dispose_engine
from app.health.router import router as health_router

logger = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "application_started",
        environment=settings.environment,
        demo_mode=settings.demo_mode,
    )
    yield
    await dispose_engine()
    logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_output=not settings.is_local)

    app = FastAPI(
        title="DevOps Control Plane API",
        version="0.1.0",
        # Interactive docs are local-only (docs/TECH_STACK.md §7). In a real
        # deployment they are disabled rather than merely unlinked.
        docs_url="/docs" if settings.is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_local else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Order matters: the outermost middleware added last runs first, so the
    # correlation id is bound before anything else can log.
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=not settings.is_local)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-Correlation-ID", "X-CSRF-Token"],
        )
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    # Probes live at the root, outside the versioned API, so an orchestrator
    # does not have to track API versions.
    app.include_router(health_router)

    return app


app = create_app()
