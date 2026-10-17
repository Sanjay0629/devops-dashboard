#!/usr/bin/env bash
# Regenerate the committed TypeScript API types from the backend's OpenAPI
# schema (docs/TECH_STACK.md §11, CLAUDE.md §5).
#
# Run it after any change to a Pydantic response model or a route, in the same
# commit. The `contracts` CI job runs it and fails if the working tree moves,
# so a backend schema change that was not propagated breaks the build rather
# than the dashboard.
#
# Needs both toolchains — `uv` to read the schema out of the application and
# `npm` to run the generator — because the contract has two ends.
set -euo pipefail

cd "$(dirname "$0")/.."

schema="$(mktemp)"
trap 'rm -f "$schema"' EXIT

# Deliberately not a running server: `python -m app.openapi` builds the app and
# reads its routing table without opening a socket, so this works offline and
# in CI with placeholder credentials.
(cd backend && uv run python -m app.openapi) >"$schema"

(cd frontend && npx openapi-typescript "$schema" --output src/api/schema.ts)

# Formatted here, or `prettier --check` and this gate reformat each other's
# output on alternate runs and one of the two always fails.
(cd frontend && npx prettier --write --log-level warn src/api/schema.ts)

printf 'Wrote frontend/src/api/schema.ts\n'
