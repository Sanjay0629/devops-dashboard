"""FastAPI application factory.

The modular monolith's composition root (AD-01). Domain routers are mounted here
as they arrive; M1.1 had only the health router.

As of M1.6 this mounts health, authentication, administration, the dashboard
and inventory.
Every route those routers add carries a ``require_permission`` dependency, and
``tests/authz/test_route_coverage.py`` walks this application's routing table
to prove that no route anywhere escapes one — so a future router added here
cannot quietly ship unauthorised. The exceptions are explicit in that test: the
health probes and ``/auth/login`` + ``/auth/callback`` are public
(docs/SECURITY.md §20), and ``/auth/logout`` + ``/auth/session`` carry
``require_session`` but no permission, because identity is all they need.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.core.csrf import CsrfMiddleware
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware
from app.core.redis import dispose_redis
from app.dashboard.router import router as dashboard_router
from app.db.session import dispose_engine
from app.health.router import router as health_router
from app.inventory.router_applications import router as applications_router
from app.inventory.router_servers import router as servers_router

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
    await dispose_redis()
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
    # Inside CORS, outside the routes: a state-changing request is rejected
    # before any handler or database session is created (docs/SECURITY.md §11).
    app.add_middleware(CsrfMiddleware, allowed_origins=settings.cors_origins)
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    # Probes live at the root, outside the versioned API, so an orchestrator
    # does not have to track API versions.
    app.include_router(health_router)
    app.include_router(auth_router, prefix=settings.api_base_path)
    app.include_router(admin_router, prefix=settings.api_base_path)
    app.include_router(dashboard_router, prefix=settings.api_base_path)
    app.include_router(servers_router, prefix=settings.api_base_path)
    app.include_router(applications_router, prefix=settings.api_base_path)

    return app


app = create_app()
