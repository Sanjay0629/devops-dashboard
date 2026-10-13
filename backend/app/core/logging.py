"""Structured logging with correlation IDs (AD-18).

JSON to stdout in every environment except ``local``, where a human-readable
console renderer is used. The redaction processor sits at the end of the chain,
so everything on its way out is scrubbed — including values bound by callers
that never thought about secrets.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

from app.core.redaction import redaction_processor

#: Per-request / per-task correlation id. Read by the logging chain and by audit
#: writing later; never mutated outside the middleware or a task entrypoint.
correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def set_correlation_id(value: str) -> contextvars.Token[str | None]:
    return correlation_id_var.set(value)


def get_correlation_id() -> str | None:
    return correlation_id_var.get()


def _add_correlation_id(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    correlation_id = correlation_id_var.get()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and route stdlib logging through it.

    Idempotent: safe to call from both the app factory and the Celery worker.
    """
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        # Last before rendering: nothing is emitted without passing through this.
        redaction_processor,
    ]
    if json_output:
        # ConsoleRenderer formats exceptions itself and warns when this is also
        # present, so it is added only on the JSON path.
        shared_processors.insert(-1, structlog.processors.format_exc_info)

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Hand stdlib loggers (uvicorn, sqlalchemy, celery) to the same chain.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelNamesMapping()[level],
        force=True,
    )
    for noisy in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
