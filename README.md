# DevOps Control Plane

Centralized monitoring and management portal for servers and applications across AWS, Azure and on-premises environments.

**Current state: milestone M1.1 — repository foundation.** The application does not monitor anything, has no authentication, and has no cloud, Jenkins, Prometheus or Loki integration. What exists is the toolchain, the health endpoints, and the verification loop every later milestone depends on. [`PROGRESS.md`](PROGRESS.md) is the authoritative statement of what works.

## Documentation

| Document | Purpose |
|---|---|
| [`PRD.md`](PRD.md) | Product requirements — the source of truth |
| [`CLAUDE.md`](CLAUDE.md) | Operating rules for AI coding agents |
| [`PROGRESS.md`](PROGRESS.md) | Current state, test results, what is not configured |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, flows, decisions (`AD-nn`), open questions (`OPEN-nn`) |
| [`docs/TECH_STACK.md`](docs/TECH_STACK.md) | Technology choices and dependency rules |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Mandatory security rules |
| [`docs/DATABASE.md`](docs/DATABASE.md) | PostgreSQL data model |
| [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) | REST contract |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | Milestones M1.1 – M6.6 |
| [`docs/TESTING.md`](docs/TESTING.md) | Test strategy and commands |

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12.x | Pinned to `>=3.12,<3.13` |
| [uv](https://docs.astral.sh/uv/) | 0.5+ | Package manager (`TS-03`) |
| Node.js | 22+ | |
| Docker + Compose | v2 | Only needed for the full stack |

## Quick start — the full stack

```bash
cp .env.example .env
docker compose -f infra/compose/docker-compose.yml --profile core up --build
```

Then: frontend on <http://localhost:5173>, API on <http://localhost:8000>, API docs on <http://localhost:8000/docs> (local only).

> **The Compose stack has never been started.** The Docker daemon was unavailable when M1.1 was implemented, so `docker compose up` and [`scripts/smoke-test.sh`](scripts/smoke-test.sh) are unexecuted. Expect to fix something the first time you run them, and please record the outcome in `PROGRESS.md`.

## Quick start — without Docker

Both halves run natively; the backend needs PostgreSQL and Redis only for `/readyz` to go green, and the test suite needs neither.

```bash
cd backend && uv sync --all-groups && uv run pytest
```

```bash
cd frontend && npm ci && npm run test && npm run dev
```

To serve the API, export the variables from `.env.example` (pointing at `localhost` instead of the Compose hostnames) and run:

```bash
cd backend && uv run uvicorn app.main:app --reload
```

> **On Windows, keep `--reload`.** uvicorn picks its event loop by platform: without `--reload` it uses `ProactorEventLoop`, which psycopg's async driver cannot use, so every database call fails and `/readyz` reports `database: false` even with PostgreSQL running. With `--reload` (or in a Linux container) uvicorn uses a selector loop and it works. Verified on Windows 11 with uvicorn 0.54.

Compose publishes PostgreSQL on host port **5433** and Redis on **6380** by default, because a developer machine often already runs those services on their standard ports. Override with `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT`; container-to-container traffic always uses the standard ports, so `DCP_DATABASE_URL` is unaffected.

## Checks

Run everything, as CI does:

```bash
./scripts/check-all.sh
```

Individually — see [`docs/TESTING.md`](docs/TESTING.md) §16 for the full list:

```bash
cd backend && uv run ruff check . && uv run mypy && uv run pytest
```

```bash
cd frontend && npm run lint && npm run typecheck && npm run test && npm run build
```

Install the pre-commit hooks once. The secret scanner is the one that matters most — a credential must never reach a commit:

```bash
pre-commit install
```

## Layout

```
backend/            FastAPI modular monolith + Celery workers (AD-01)
  app/core/         config, logging, redaction, errors, middleware
  app/health/       liveness and readiness
  app/db/           engine and session factory
  app/workers/      Celery app and tasks
  tests/            unit tests; no network, no database
frontend/           React + TypeScript + Vite + Tailwind SPA
infra/compose/      Docker Compose stack, profiled
infra/{prometheus,loki,alloy}/   observability config (placeholders until M2/M3)
scripts/            check-all, smoke test, doc link check, digest pinning
docs/               architecture, security, database, API, plan, testing
```

## What M1.1 deliberately leaves out

Each lands in a named milestone, so nothing here is an accidental gap:

| Not implemented | Milestone |
|---|---|
| Database schema, models, Alembic migrations | M1.2 |
| OIDC authentication, sessions, CSRF enforcement | M1.3 |
| RBAC and per-route permission dependencies | M1.4 |
| Server and application inventory | M1.5 |
| Dashboard | M1.6 |
| Prometheus metrics, systemd service state, staleness | M2 |
| Loki log search, alert rules, notifications | M3 |
| Cloud adapters, service operations, approvals, audit | M4 |
| Jenkins deployments | M5 |

`/readyz` reports a `migrations` check that currently returns `false`: no schema exists yet, so it is reported but does not gate readiness. M1.2 adds it to the required set and upgrades it from "schema present" to "schema at head".

## Security

[`docs/SECURITY.md`](docs/SECURITY.md) §1 lists ten mandatory rules. Two shape the code already:

- **No shell execution surface exists**, and none may be added. Service operations will accept a closed action enum and a registered target id — never a command.
- **Secrets are never logged.** A redaction pass sits at the end of the logging chain, proven by canary tests rather than assumed.

Configuration refuses to start without the credentials it needs, rejects wildcard CORS in every environment, and rejects demonstration mode outside `local`.
