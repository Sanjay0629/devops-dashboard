# DevOps Control Plane

Centralized monitoring and management portal for servers and applications across AWS, Azure and on-premises environments.

**Current state: milestone M2.2 — host metrics.** Beyond the M1.6 shell below, a server with a registered `metrics_instance` now shows real current values and history read from Prometheus (and two or more can be compared); a host with no data says why instead of showing a zero. The remainder of this paragraph describes the **dashboard** at M1.6, which is unchanged: You can sign in, be granted a scoped role, be refused everything you have not been granted, register and browse servers and applications, and open a dashboard. **The dashboard itself aggregates no metrics yet**, and that is visible rather than hidden: of the dashboard's nine sections only two have a source (server status counts and the failed-unit count, both from the database), and the other seven report `not_configured` with `null` values and the milestone that will fill them — never a zero. There is no cloud, Jenkins or Loki integration. Authentication has been verified only against a local **mock** identity provider that checks no password (`OPEN-01`). [`PROGRESS.md`](PROGRESS.md) is the authoritative statement of what works.

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
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile core --profile mocks up --build --wait
```

Then: frontend on <http://localhost:5173>, API on <http://localhost:8000>, API docs on <http://localhost:8000/docs> (local only).

> **`--env-file .env` is not optional, and M1.7 is where that was found out.**
> Compose takes its *project directory* from the compose file's location, so with
> `-f infra/compose/docker-compose.yml` it looks for `.env` in `infra/compose/` and never
> reads the one at the repository root. Every `${VAR}` in the compose file has a default, so
> the stack came up correctly anyway and nobody noticed — but the `cp` above was a no-op, and
> anything you edited in `.env` (a port, a password) was silently discarded. Verified directly:
> `DCP_LOG_LEVEL=WARNING` in a root `.env` still produced `INFO` in the container without this
> flag, and `WARNING` with it.
>
> `--wait` is there so the command returns when the stack is actually ready rather than when
> the containers have been created.

The stack applies its own migrations: a one-shot `migrate` service runs `alembic upgrade head`
and exits, and `backend`, `worker` and `beat` all wait for it to succeed. **Before M1.7 nothing
did this** — a fresh clone got a stack of healthy containers over an empty database, because the
container healthcheck is `/healthz` (liveness) while the migration gate is on `/readyz`. It
worked for five milestones only because the first developer had run `alembic upgrade head` by
hand and the volume outlived every later `up`. `scripts/smoke-test.sh` step 19 (`down -v`, then
`up` again) is what caught it.

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

**Check the whole stack in one command:**

```bash
./scripts/smoke-test.sh
```

It brings up `core` plus `mocks`, asserts every service reaches healthy, that migrations are at
head, that `/readyz` reports all three dependencies true, that nothing runs as root or
privileged, that a Celery task round-trips, that a login completes through the mock IdP, that the
seeded inventory is readable, that a database outage degrades to `503` without substituting
zeros, and that `down -v` followed by `up` returns to the same state from empty volumes. Then it
tears the stack down (`KEEP_STACK=1` leaves it running). Its steps are numbered to match
[`docs/TESTING.md`](docs/TESTING.md) §11. **Step 18 starts the `observability` profile** and asserts
§11 step 8's Prometheus half: the bundled `node-exporter` target is `up` and all ten metric
templates execute, read through the backend's own client inside the container. Step 8's Loki half
needs M3.1, and `loki`/`grafana` are still placeholders, which is why the script starts
`prometheus` and `node-exporter` by name rather than the whole profile.

**End-to-end tests** need the stack running, and run against it rather than starting their own:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.yml --profile core --profile mocks up -d --wait
cd frontend && npm run test:e2e
```

The suite seeds what it needs (an administrator via the bootstrap CLI, then a Viewer granted
`VIEWER`) and is idempotent, so it can be re-run against a long-lived stack. Set
`DCP_DASHBOARD_CACHE_TTL_SECONDS=0` when starting the stack if you add a test that writes and
then reads a dashboard total — the aggregate is cached for 15 s by default.

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
infra/prometheus/    real scrape config (M2.1); targets static until M2.3
infra/{loki,alloy}/  log pipeline config (placeholders until M3.1)
scripts/            check-all, smoke test, doc link check, digest pinning
docs/               architecture, security, database, API, plan, testing
```

## What is deliberately left out so far

Each lands in a named milestone, so nothing here is an accidental gap:

| Not implemented | Milestone |
|---|---|
| **Any domain API at all** — the biggest gap right now: authorisation is enforced, but the only endpoints behind it are administrative | M1.5+ |
| Server and application inventory, and therefore any *scoped* resource for the matcher to evaluate a target against | M1.5 |
| ~~Dashboard~~ — shipped in M1.6, but with only database-backed sections populated; metrics, alerts and recent activity report `not_configured` | ~~M1.6~~ · real data in M2–M5 |
| Prometheus metrics, systemd service state, staleness | M2 · **M2.1 done**: the backend renders allowlisted PromQL and reads a real Prometheus (`GET /api/v1/metrics/templates`, `GET /api/v1/integrations/status`). **M2.2 done**: per-server current values, range charts and server/environment comparison (`GET /api/v1/servers/{id}/metrics`, `…/metrics/range`, `POST /api/v1/metrics/compare`; pages at `/servers/:id/metrics` and `/compare`). The dashboard's utilisation section still reports `not_configured` — a fleet-wide aggregate is not part of M2.2. **M2.3 done**: registering a server makes it a scraped target with no config edit (`file_sd`, written by the worker), and `agent_status` / `last_seen_at` are maintained from Prometheus's own `up`. To see the bundled exporter in development, register a server with `metrics_instance` `node-exporter:9100` |
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
