"""Dump the OpenAPI schema, for the TypeScript type generator.

docs/TECH_STACK.md §11 keeps the frontend and backend contract aligned
mechanically: FastAPI derives the schema from the Pydantic models, a generator
turns it into a committed TypeScript file, and CI fails when the two drift.
This module is the first link — ``python -m app.openapi`` on stdout, consumed
by ``scripts/generate-api-types.sh``.

Output is sorted and newline-terminated so two runs of the same code produce
byte-identical bytes. Without that the drift gate would fail on dictionary
ordering rather than on a real contract change, and a gate that cries wolf
gets switched off.

**Nothing here starts the application.** ``create_app`` builds the routing
table and ``.openapi()`` reads it; the lifespan never runs, so no database or
Redis connection is opened. That is why the placeholder credentials below are
safe — they are never dialled. They exist because ``app.main`` builds the
application at import time and configuration fails closed, so a developer
regenerating types would otherwise need a full ``.env`` to print a schema.
``setdefault`` leaves a real environment untouched.
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("DCP_ENVIRONMENT", "local")
os.environ.setdefault("DCP_DATABASE_URL", "postgresql+psycopg://schema:schema@127.0.0.1:1/schema")
os.environ.setdefault("DCP_REDIS_URL", "redis://127.0.0.1:1/0")

# Imported last on purpose: it must follow the placeholders above.
from app.main import create_app


def schema_json() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(schema_json())
