"""FastAPI application factory.

The modular monolith's composition root (AD-01). Domain routers are mounted here
as they arrive; M1.1 had only the health router.

As of M2.1 this mounts health, authentication, administration, the dashboard,
inventory and metrics.
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
from app.alerts.router import router as alerts_router
from app.alerts.webhook import router as alert_webhook_router
from app.approvals.policy_admin import router as approval_policies_router
from app.approvals.router import router as approvals_router
from app.audit.export_router import router as audit_export_router
from app.audit.router import router as audit_router
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.core.csrf import CsrfMiddleware
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware
from app.core.redis import dispose_redis
from app.dashboard.router import router as dashboard_router
from app.db.session import dispose_engine
from app.deployments.deployment_router import router as deployment_requests_router
from app.deployments.router import router as deployments_router
from app.health.router import router as health_router
from app.inventory.router_applications import router as applications_router
from app.inventory.router_servers import router as servers_router
from app.logs.router import router as logs_router
from app.metrics.router import router as metrics_router
from app.notifications.router import router as notifications_router
from app.operations.history_router import router as operations_history_router
from app.operations.router import router as operations_router
from app.providers.router import router as providers_router
from app.services.router import router as services_router

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
    # The one exemption: the Alertmanager webhook is not browser-originated and
    # authenticates itself with a shared secret (app/alerts/webhook.py). Exact path, so
    # nothing under it inherits the exemption; a test fails if another path appears.
    app.add_middleware(
        CsrfMiddleware,
        allowed_origins=settings.cors_origins,
        exempt_paths=frozenset({f"{settings.api_base_path}/webhooks/alerts"}),
    )
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
    # §15.4 and §26.1 (M2.1). Mounted even with no Prometheus configured: the
    # catalogue is static, and §26.1's job is to say that nothing is
    # configured rather than to disappear when nothing is.
    app.include_router(metrics_router, prefix=settings.api_base_path)
    app.include_router(services_router, prefix=settings.api_base_path)
    # §19.1 and §19.2 (M3.2). Mounted even with no Loki configured: they answer
    # 503 not_configured rather than disappearing.
    app.include_router(logs_router, prefix=settings.api_base_path)
    app.include_router(alerts_router, prefix=settings.api_base_path)
    app.include_router(notifications_router, prefix=settings.api_base_path)
    # §26.7 (M4.1). Mounted in every mode: with no adapter it answers 200 with
    # capabilities null rather than disappearing.
    app.include_router(providers_router, prefix=settings.api_base_path)
    app.include_router(operations_router, prefix=settings.api_base_path)
    # §23.1 and §23.2 (M4.6).
    app.include_router(operations_history_router, prefix=settings.api_base_path)
    # §24.1 and §24.2 (M4.6): reads of the trail, themselves audited.
    app.include_router(audit_router, prefix=settings.api_base_path)
    app.include_router(audit_export_router, prefix=settings.api_base_path)
    app.include_router(approvals_router, prefix=settings.api_base_path)
    app.include_router(deployments_router, prefix=settings.api_base_path)
    app.include_router(deployment_requests_router, prefix=settings.api_base_path)
    app.include_router(approval_policies_router, prefix=settings.api_base_path)
    # §20.5 (M3.3). Mounted even with no secret configured: it answers 503
    # not_configured rather than disappearing.
    app.include_router(alert_webhook_router, prefix=settings.api_base_path)

    return app


app = create_app()
