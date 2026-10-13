# DevOps Control Plane

Centralized monitoring and management portal for servers and applications across AWS, Azure and on-premises environments.

**Current state: milestone M1.4 — authentication, sessions and authorisation.** You can sign in, be granted a scoped role, and be refused everything you have not been granted. The administrative API (users, roles, read-only mode) is the only domain API so far: it monitors nothing and has no cloud, Jenkins, Prometheus or Loki integration. Authentication has been verified only against a local **mock** identity provider that checks no password (`OPEN-01`). [`PROGRESS.md`](PROGRESS.md) is the authoritative statement of what works.

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
docker compose -f infra/compose/docker-compose.yml --profile core --profile mocks up --build
```

Then: frontend on <http://localhost:5173>, API on <http://localhost:8000>, API docs on <http://localhost:8000/docs> (local only).

**Sign in at <http://localhost:5173>.** The `mocks` profile is what makes that work: it runs a
[mock OIDC provider](infra/compose/mock-idp/README.md) that checks no password and offers three
synthetic accounts. Without that profile the app still starts and `/api/v1/auth/login` answers
`503 not_configured` — which is the honest answer, not a failure.

All three accounts begin with **no roles**, so you can sign in and see nothing until a role is
granted. There is no bootstrap administrator by design. Grant the first one with the one-shot CLI,
which refuses to run if an administrator already exists:

```bash
docker compose -f infra/compose/docker-compose.yml exec backend python -m app.admin.bootstrap --email admin@example.invalid
```

It grants `ADMINISTRATOR` to an **existing** user — so sign in once first — with every environment
in scope, and audits the grant as `system:bootstrap`. After that, roles are granted and revoked
through `/api/v1/admin/users/{id}/roles`, and a change takes effect on the target's next request
with no re-login.

> **Two things to know about this Compose setup.**
>
> - `mock-idp` shares the `backend` container's network namespace, so that one issuer URL
>   (`http://localhost:9000`) resolves both from your browser and from inside the backend. The
>   `iss` claim is compared literally, so a single URL that works on both sides is the only way
>   to avoid weakening the check that discovered endpoints stay on the issuer's origin. The
>   consequence: if you recreate `backend` on its own, recreate `mock-idp` too. A plain
>   `up -d` with both profiles does that for you.
> - `DCP_OIDC_REDIRECT_URL` points at the **frontend** origin (`:5173`), not the backend's. In
>   production the SPA and the API share one origin behind the reverse proxy (AD-02) and the dev
>   server's proxy reproduces that, so the login flow and the redirect at the end of it stay on
>   the origin you are browsing.

> **`scripts/smoke-test.sh` is still unexecuted**, so the M1.1 criterion "every service starts
> healthy" is not formally verified — though the `core` and `mocks` services have now been
> started and used. See `PROGRESS.md`.

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

> **On Windows, keep `--reload`.** uvicorn picks its event loop by platform: without `--reload` it uses `ProactorEventLoop`, which psycopg's async driver cannot use, so every database call fails and `/readyz` reports `database: false` even with PostgreSQL running. With `--reload` (or in a Linux container) uvicorn uses a selector loop and it works. Verified on Windows 11 with uvicorn 0.54. The test suite handles this itself via a loop-factory hook in `backend/tests/conftest.py`.

Running the backend natively but the mock IdP in Compose works too — point
`DCP_OIDC_REDIRECT_URL` at `http://localhost:8000/api/v1/auth/callback` instead, since the SPA's
dev server is not in the loop.

Compose publishes PostgreSQL on host port **5433** and Redis on **6380** by default, because a developer machine often already runs those services on their standard ports. Override with `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT`; container-to-container traffic always uses the standard ports, so `DCP_DATABASE_URL` is unaffected.

## Database

Migrations are an explicit step, never run from application startup:

```bash
cd backend && uv run alembic upgrade head
```

```bash
cd backend && uv run alembic downgrade -1
```

The schema is defined in [`docs/DATABASE.md`](docs/DATABASE.md). Two things about it are load-bearing rather than incidental:

- **`audit_events` is append-only.** `UPDATE`, `DELETE` and `TRUNCATE` all raise, enforced by triggers rather than by grants alone — a superuser bypasses grants, which was measured rather than assumed.
- **`system_settings` holds the read-only-mode switch** (migration `0003`), in PostgreSQL rather than Redis or an environment variable: [`docs/SECURITY.md`](docs/SECURITY.md) §27 makes it the first containment step in an incident, so it has to survive a restart and a Redis flush and be flippable without a redeploy. The reader fails **closed** — a missing or non-boolean row refuses writes.
- **`audit_events` is partitioned by month**, and a write outside every partition range *fails*. Since audit writes share the transaction of the privileged change they record, that failure would also fail the operation, so a scheduled job keeps a three-month runway ahead and warns when it shrinks.

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
  app/core/         config, logging, redaction, errors, middleware, CSRF,
                    Redis client, rate limiting
  app/health/       liveness and readiness
  app/db/           base, types, engines, session, partition helpers
  app/auth/         OIDC client, Redis session store, cookies, dependencies,
                    JIT provisioning, /auth/* router, User model
  app/authz/        roles, permissions, scoped grants, system settings;
                    the matcher, per-request resolution, require_permission
  app/admin/        user and role administration, read-only mode, bootstrap CLI
  app/inventory/    cloud accounts, servers, applications, services, policies
  app/approvals/    approval policy
  app/audit/        append-only audit events + the record() writer
  app/workers/      Celery app and tasks
  alembic/          migrations; the schema's source of truth
  tests/            unit tests (no network, no database)
  tests/auth/       OIDC, session, cookie and CSRF unit tests
  tests/integration/  needs real PostgreSQL via TEST_DATABASE_URL; the auth
                    suite also needs real Redis via TEST_REDIS_URL
frontend/           React + TypeScript + Vite + Tailwind SPA
  src/auth/         session context and hooks
  src/pages/        Login
infra/compose/      Docker Compose stack, profiled
  mock-idp/         mock OIDC provider — development aid only
infra/{prometheus,loki,alloy}/   observability config (placeholders until M2/M3)
scripts/            check-all, smoke test, doc link check, digest pinning
docs/               architecture, security, database, API, plan, testing
```

## What is deliberately left out so far

Each lands in a named milestone, so nothing here is an accidental gap:

| Not implemented | Milestone |
|---|---|
| **Any domain API at all** — the biggest gap right now: authorisation is enforced, but the only endpoints behind it are administrative | M1.5+ |
| Server and application inventory, and therefore any *scoped* resource for the matcher to evaluate a target against | M1.5 |
| Dashboard | M1.6 |
| Prometheus metrics, systemd service state, staleness | M2 |
| Loki log search, alert rules, notifications | M3 |
| Cloud adapters, service operations, approvals, audit | M4 |
| Jenkins deployments | M5 |

`/readyz` gates on three checks: `database`, `broker` and `migrations`. The last compares the applied Alembic revision against the head this build ships, so an instance whose schema is behind — or ahead of — its code reports itself unready rather than failing every query.

## Security

[`docs/SECURITY.md`](docs/SECURITY.md) §1 lists ten mandatory rules. Four shape the code already:

- **No shell execution surface exists**, and none may be added. Service operations will accept a closed action enum and a registered target id — never a command.
- **Secrets are never logged.** A redaction pass sits at the end of the logging chain, proven by canary tests rather than assumed.
- **Permissions are enforced on the backend, per route.** One matcher, one `require_permission` dependency, and a test that walks the live routing table and fails the build if any route carries neither a permission nor an explicit, justified exception. The frontend hides controls for usability only; its permission list is advisory and a test says so.
- **Fail closed.** Any error resolving a session, a grant, a scope or the read-only flag denies. There is no `if DEBUG: skip_auth` path, and a grep gate — with its own test that plants one — proves it.

Configuration refuses to start without the credentials it needs, rejects wildcard CORS in every environment, and rejects demonstration mode outside `local`.
