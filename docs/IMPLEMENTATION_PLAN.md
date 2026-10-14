# Implementation Plan — DevOps Control Plane

**Status:** Draft for review. **Nothing in this plan has been implemented.** Current state is tracked in [`../PROGRESS.md`](../PROGRESS.md).
**Source of truth:** [`../PRD.md`](../PRD.md) §13. Contracts: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) · [DATABASE.md](DATABASE.md) · [API_CONTRACTS.md](API_CONTRACTS.md) · [TESTING.md](TESTING.md)

---

## How to use this plan

1. **One milestone at a time.** Each phase is split into numbered milestones (`M1.1`, `M1.2`, …). A milestone is a few days of work that ends in something runnable and verifiable. **Do not attempt a whole phase, and never the whole plan, in one pass.**
2. **No milestone is done until its Definition of Done is satisfied with evidence** — commands actually run, output actually seen ([CLAUDE.md](../CLAUDE.md)).
3. **Security checks are part of the milestone, not a later phase.** PRD §9 makes security a release requirement.
4. **Update [`../PROGRESS.md`](../PROGRESS.md) when a milestone completes**, with real test results. Never mark anything complete because the plan says it should be.
5. **No milestone may depend on a later phase's work.** Dependencies are stated explicitly and only ever point backwards.

### Smallest useful first milestone

**M1.1 — Repository skeleton + Docker Compose core + health endpoint + CI.** It ends with `docker compose up` producing a reachable `/healthz`, a passing (near-empty) test suite, and linting and type checking enforced in CI. It proves the toolchain end to end before any domain logic exists, and every later milestone inherits that verification loop. It touches no authentication, no database schema and no integration.

### Milestone map

| Phase | Milestones | Theme |
|---|---|---|
| 1 | M1.1 – M1.7 | Foundation: repo, config, auth, RBAC, schema, inventory, dashboard shell |
| 2 | M2.1 – M2.5 | Prometheus, host metrics, systemd state, staleness |
| 3 | M3.1 – M3.4 | Alloy, Loki, log search, alerts, notifications |
| 4 | M4.1 – M4.6 | Provider adapters, controlled operations, approvals, audit |
| 5 | M5.1 – M5.5 | Jenkins, deployment lifecycle, build logs, production approvals, health checks |
| 6 | M6.1 – M6.6 | Additional providers, hardening, performance, backup/recovery, production readiness |

---

# Phase 1 — Foundation

**Objective.** A running, authenticated, authorised application with a migrated schema, a working server inventory and a dashboard shell — the vertical slice everything else attaches to (PRD §13 Phase 1).

**Prerequisites.** Docker and Docker Compose available; the documentation set in `docs/` reviewed; OPEN-01 (IdP) at least provisionally answered, or the mock IdP accepted for development; ~~OPEN-02 (application↔environment) confirmed before M1.5~~ — **done 2026-10-14: environment-agnostic**.

**Dependencies.** None.

---

## M1.1 — Repository skeleton, Compose core, health endpoint, CI

**Tasks**

- [ ] Repository layout: `backend/`, `frontend/`, `infra/compose/`, `infra/prometheus/`, `infra/loki/`, `infra/alloy/`, `docs/`, `.github/workflows/`.
- [ ] Backend project (`pyproject.toml` + lockfile), FastAPI app factory, `GET /healthz`, `GET /readyz` (DB + migration-head + broker checks, returning booleans only).
- [ ] `core/config.py`: Pydantic settings, fail-fast on missing required variables, **no defaults for secrets**.
- [ ] `core/logging.py`: structured JSON logging, `correlation_id` binding, redaction processor (patterns from [SECURITY.md §17](SECURITY.md#17-sensitive-log-redaction)).
- [ ] `core/errors.py`: the error envelope and exception handlers from [API_CONTRACTS.md §3](API_CONTRACTS.md#3-error-format).
- [ ] Middleware: correlation ID accept/generate/echo, request timing, security headers.
- [ ] Frontend project (Vite + React + TypeScript strict + Tailwind), one page rendering backend health, dark/light theme tokens.
- [ ] `docker-compose.yml` with profiles `core`, `observability`, `logs`, `mocks`; `core` = frontend, backend, worker, beat, postgres, redis. Images pinned by digest. `HEALTHCHECK` on each service.
- [ ] `.env.example` with placeholders only; `.gitignore`; `.dockerignore`.
- [ ] Celery app, one `ping` task, beat with an empty schedule.
- [ ] Tooling: Ruff, mypy strict, pytest, ESLint, Prettier, `tsc --noEmit`, Vitest, pre-commit with a secret scanner.
- [ ] CI: lint, format check, type check, backend tests, frontend tests, frontend build, `pip-audit`, `npm audit`, secret scan, image build + scan.
- [ ] `README.md`: how to start the stack and run checks.

**Expected files**

```
backend/pyproject.toml  backend/app/main.py  backend/app/core/{config,logging,errors,middleware}.py
backend/app/workers/celery_app.py  backend/tests/test_health.py
frontend/package.json  frontend/src/{main.tsx,App.tsx}  frontend/tailwind.config.ts
infra/compose/docker-compose.yml  .env.example  .pre-commit-config.yaml
.github/workflows/ci.yml  README.md
```

**Acceptance criteria**

- `docker compose --profile core up` starts every service healthy.
- `GET /healthz` → `200`; `GET /readyz` → `200` with DB and broker true.
- The frontend loads and shows backend health in both themes.
- Every CI job passes on a clean checkout.
- Starting the backend with a required variable missing fails immediately with a clear message — it does not start degraded.

**Tests**

- *Unit:* config loading (valid, missing-required, invalid-type); redaction processor against seeded canaries; correlation-ID middleware generates and echoes; error-envelope shape for each handled exception.
- *Integration:* `/healthz` and `/readyz` via the ASGI test client; `/readyz` returns `503` with the database stopped.
- *Frontend:* App renders; theme toggle switches tokens.

**Security checks**

- [ ] Secret scanner runs in pre-commit **and** CI and fails on a planted test secret.
- [ ] No secret has a default value in config.
- [ ] Security headers present on every response (asserted by test).
- [ ] Containers run as non-root; `docker compose config` shows no privileged flags.
- [ ] The redaction processor is proven by a canary test, not assumed.

**Definition of done.** All boxes ticked, all tests run and passing with output recorded in `PROGRESS.md`, CI green, README accurate.

**Risks / rollback.** Risk: toolchain churn (Tailwind/Vite/Pydantic major interactions). Mitigation: pin from day one; resolve with the solver, not by editing docs. Rollback: the milestone is additive — revert the branch.

---

## M1.2 — Database schema and migrations

**Tasks**

- [ ] SQLAlchemy base, naming convention, `updated_at` trigger, UUID v7 helper.
- [ ] Models for all Phase 1 tables: `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `cloud_accounts`, `servers`, `applications`, `application_servers`, `services`, `service_action_policies`, `approval_policies`, `audit_events` ([DATABASE.md](DATABASE.md)).
- [ ] Alembic initial revision including **`CHECK` constraints, partial unique indexes, the monthly `audit_events` partitions and the `INSERT, SELECT`-only grant** — the parts autogenerate misses.
- [ ] Seed revision: five roles, the permission catalogue, `role_permissions`, a default conservative `service_action_policy`, a default production `approval_policy`, the `ON_PREM` cloud account.
- [ ] Partition pre-creation maintenance task + a monitor for the partition runway.
- [ ] Session management: one session per request, `statement_timeout`, commit/rollback middleware.
- [ ] `make migrate` / documented migration command; migrations run as a **separate step**, never from app startup.

**Expected files**

```
backend/app/db/{base,session,types,mixins}.py
backend/app/{auth,authz,inventory,audit}/models.py
backend/alembic/env.py  backend/alembic/versions/0001_initial.py  backend/alembic/versions/0002_seed.py
backend/app/workers/tasks/maintenance.py  backend/tests/test_migrations.py
```

**Dependencies.** M1.1.

**Acceptance criteria**

- `alembic upgrade head` on an empty database creates every table, constraint, index, partition and grant.
- `downgrade -1` then `upgrade head` succeeds.
- Duplicate `(cloud_account_id, provider_resource_id)` is rejected by the database.
- The application role cannot `UPDATE` or `DELETE` an `audit_events` row.
- Seeded roles and permissions match [SECURITY.md §3–§4](SECURITY.md#3-roles).

**Tests**

- *Unit:* model defaults; enum `CHECK` rejection; `updated_at` trigger fires.
- *Integration (real PostgreSQL, never SQLite):* migrate from empty; downgrade/upgrade round trip; each unique and partial index enforced; audit immutability under the app role; partition routing by `occurred_at`; seed idempotency when re-run.

**Security checks**

- [ ] Audit immutability proven by a failing `UPDATE`/`DELETE` as the app role.
- [ ] `users` has no password-like column.
- [ ] `cloud_accounts.credential_reference` is documented and tested as a reference, never a value.
- [ ] Migrations contain no credential or environment-specific host.

**Definition of done.** Migration tests pass against containerised PostgreSQL; schema diff against [DATABASE.md](DATABASE.md) reviewed; `PROGRESS.md` updated.

**Risks / rollback.** Risk: partitioning and grants are easy to get subtly wrong, and a missing future partition makes audit inserts fail — which fails privileged operations. Mitigation: runway monitor plus a test that inserts into a future month. Rollback: `downgrade` is implemented and tested.

---

## M1.3 — OIDC authentication and sessions

**Status: complete, 2026-10-13.** `OPEN-04` resolved as Redis-only. Verified against
the mock IdP only — a real-IdP round trip is still outstanding (`OPEN-01`). See
[`../PROGRESS.md`](../PROGRESS.md) §7 for the executed results.

**Tasks**

- [x] OIDC discovery, JWKS cache with rotation, auth-code + PKCE flow.
- [x] `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/session` per [API_CONTRACTS.md §12](API_CONTRACTS.md#12-authentication-endpoints).
- [x] `state`/`nonce`/verifier in Redis, single-use, TTL.
- [x] Session store: opaque id, idle + absolute deadlines, rotation on privilege change, termination support.
- [x] Cookie flags per [SECURITY.md §5](SECURITY.md#5-session-handling); `Secure=false` only on localhost.
- [x] CSRF double-submit middleware.
- [x] Just-in-time user provisioning on first login; disabled users rejected.
- [x] Auth-event audit writing.
- [x] Mock IdP in the `mocks` Compose profile.
- [x] Frontend: login redirect, session context, 401 handling, logout.

**Expected files**

```
backend/app/auth/{router,oidc_client,session,dependencies,service,schemas}.py
backend/app/core/csrf.py  infra/compose/mock-idp/
frontend/src/auth/{SessionProvider.tsx,useSession.ts}  frontend/src/pages/Login.tsx
backend/tests/auth/{test_login_flow.py,test_session.py,test_csrf.py}
```

**Dependencies.** M1.1, M1.2.

**Acceptance criteria**

- A user can log in through the mock IdP and `/auth/session` returns their identity.
- An unauthenticated request to a protected endpoint returns `401` (PRD §4.1 acceptance criterion).
- Logout makes the session immediately unusable.
- Idle and absolute timeouts are enforced server-side.
- A disabled user cannot log in.
- No token is readable by JavaScript.

**Tests**

- *Unit:* ID-token verification (valid; bad signature; wrong `iss`/`aud`; expired; wrong `nonce`); `next` validation rejecting absolute URLs and `//host` forms; cookie flag assembly; session expiry arithmetic.
- *Integration:* full login round trip against the mock IdP; replayed `state` rejected; missing/invalid CSRF token on a `POST` → `403`; session termination; auth events written with no token material.

**Security checks**

- [x] Open-redirect suite on `next` — 19 rejected forms, including `//host`, `/\host`, percent-encoded `//` and CRLF header injection.
- [x] `state` replay and CSRF bypass attempts fail — verified in the integration suite and by hand over HTTP.
- [x] No token or session id in any log line (canary test) — asserted in the suites and swept over the real container logs.
- [x] Rate limit on `/auth/*` enforced — and proven to fail **closed** when Redis is unavailable.
- [x] `/auth/session`'s `permissions` array is proven to be advisory — an endpoint still rejects an action the array implies. **Done in M1.4** (PROGRESS.md issue 36): `test_the_permissions_array_is_advisory` grants an empty-scope `ADMINISTRATOR`, finds `admin:users` in the array, and watches `GET /api/v1/admin/users` answer `403`.

**Definition of done.** Login/logout work in Compose; the full auth test suite passes; `PROGRESS.md` records which IdP was used (mock vs real). — **Met 2026-10-13:** login and logout driven through the `core` + `mocks` stack in a real browser; 126 auth unit tests and 39 auth integration tests pass; the IdP used was the **mock** and `PROGRESS.md` says so.

**Risks / rollback.** Risk: real-IdP specifics (claim shapes, redirect registration) differ from the mock; OPEN-01 unresolved. Mitigation: keep the client generic, test against the real IdP as soon as one exists, and treat the mock as a development aid rather than proof. Rollback: feature-branch revert; no schema change beyond `users`.

---

## M1.4 — RBAC and authorisation enforcement

**Tasks**

- [x] Permission model, scope resolution, and the **single** `require_permission(resource_type, action)` dependency. The `scope_source` parameter in the original wording is **not** built: [SECURITY.md §20](SECURITY.md#20-api-authorization-requirements) requires scope to be validated *after* the target row is loaded, which a dependency cannot do, so the scoped question is `Authorized.permits(resource_type, action, target)` calling the same matcher from the handler.
- [x] Scope matcher: conjunctive dimensions, `*` handling, `production` never implicit, `DENY` beats `ALLOW`, fail-closed on any error. 27 table-driven cases.
- [x] Per-request resolution from PostgreSQL. **No cache and no `permissions_version` counter** — an agreed divergence from `AD-03`, approved by the product owner; the four contract documents were corrected in the same change ([ARCHITECTURE.md §9.2](ARCHITECTURE.md#92-authorization--rbac-with-scope-evaluated-per-request-ad-03)).
- [x] `AUTHZ_DENIED` audit writing, committed before the `403` propagates so a failed request still leaves a trail.
- [x] Admin endpoints 25.1–25.9 ([API_CONTRACTS.md §25](API_CONTRACTS.md#25-user-and-role-administration)), including no-privilege-escalation-through-delegation and last-Administrator protection, plus the new 25.10 read-only toggle. The first administrator comes from a one-shot `python -m app.admin.bootstrap` CLI.
- [x] Read-only mode flag blocking state-changing endpoints, as a durable `system_settings` row ([DATABASE.md §3.6](DATABASE.md#36-system_settings--added-entity-m14)).
- [x] CI route-coverage test: every route has a permission dependency or is on the explicit exception list, which distinguishes *public* from *session-authenticated* and asserts each claim.
- [~] Frontend: control hiding (`src/auth/permissions.ts`, advisory and tested) and the read-only banner. **Permission-aware navigation is not built: there is no navigation yet** — the routing shell is M1.6, which is where the nav gating belongs.

**Expected files**

```
backend/app/authz/{dependencies,matcher,service,models,schemas}.py
backend/app/admin/{router,service,schemas}.py  + bootstrap.py
backend/alembic/versions/0003_system_settings_and_admin_permissions.py
backend/tests/authz/{test_matcher.py,test_route_coverage.py}
backend/tests/integration/test_admin_roles.py   # not tests/authz/: needs the real schema and Redis
frontend/src/auth/permissions.ts
```

**Dependencies.** M1.2, M1.3.

**Acceptance criteria**

- Each of the five roles is permitted exactly the matrix in [SECURITY.md §4](SECURITY.md#4-permissions-scope-and-resourceenvironment-restrictions).
- A revoked grant takes effect on the **next request**, with no re-login and no redeploy (PRD §4.1 acceptance criterion).
- `environment: ["*"]` does not grant production.
- Every route is covered by the route-coverage test.
- Read-only mode blocks writes while reads continue working.

**Tests**

- *Unit:* table-driven matcher cases — exact match, wildcard, production-implicit denial, multi-dimension conjunction, explicit deny, expired grant, empty scope, malformed scope JSON (⇒ deny), resolution exception (⇒ deny).
- *Integration:* each role against each privileged endpoint; grant → request → revoke → request; delegation escalation attempt rejected; last-Administrator revocation refused; `AUTHZ_DENIED` audit rows written.

**Security checks**

- [x] No route lacks a permission dependency (CI-enforced by `tests/authz/test_route_coverage.py`).
- [x] Fail-closed proven by fault injection into scope resolution (`test_resolution_failure_denies`).
- [x] A `curl` with a valid session but insufficient permission is rejected identically to the UI path — asserted for all ten endpoints × four non-admin roles, and exercised by hand against the running stack.
- [x] No `if DEBUG: skip_auth` path exists (grep-gated test, which has its own test proving a planted bypass is caught).

**Definition of done.** Matcher suite and per-role endpoint suite pass; route coverage green; `PROGRESS.md` updated. **Met 2026-10-13:** 270 unit tests and 132 integration tests pass against real PostgreSQL and Redis; see [`../PROGRESS.md`](../PROGRESS.md) §7 for the actual output.

**Risks / rollback.** Risk: authorisation logic duplicated as features grow, causing drift. Mitigation: one matcher, one dependency, route-coverage test. Rollback: revert; no other milestone depends on partial RBAC.

---

## M1.5 — Server and application inventory

**~~Confirm OPEN-02 before starting this milestone~~ — done 2026-10-14: environment-agnostic**, confirming `AD-12`. `applications` carries no `environment` column, which is what M1.2 built, so this milestone starts with no migration owed to the decision.

**Tasks**

- [ ] Endpoints 14.1–14.7 and 18.1–18.5 ([API_CONTRACTS.md §14](API_CONTRACTS.md#14-server-inventory), [§18](API_CONTRACTS.md#18-applications)).
- [ ] Pagination (cursor + offset), filtering/sorting allowlists, free-text search.
- [ ] Duplicate detection surfaced as `409 duplicate_resource`.
- [ ] Scope-filtered queries (filter inside the query, not after it).
- [ ] Optimistic concurrency via `If-Unmodified-Since`.
- [ ] `metrics_instance` uniqueness and format validation.
- [ ] Audit writing for every inventory mutation; `SERVER_ENVIRONMENT_CHANGED` treated as privilege-relevant.
- [ ] Frontend: server list with filters, server detail (Overview tab), registration and edit forms, application list/detail, association management.
- [ ] Seed script with synthetic inventory for local development, clearly marked as demonstration data.

**Expected files**

```
backend/app/inventory/{router_servers,router_applications,service,schemas,queries}.py
backend/app/core/pagination.py  backend/app/core/filters.py
frontend/src/pages/servers/{ServerList,ServerDetail,ServerForm}.tsx
frontend/src/pages/applications/{ApplicationList,ApplicationDetail}.tsx
backend/tests/inventory/{test_servers_api.py,test_duplicate_detection.py,test_scope_filtering.py}
```

**Dependencies.** M1.2, M1.4.

**Acceptance criteria**

- An Administrator can register a server with the required metadata (PRD §4.3 acceptance criterion).
- A duplicate provider resource id within an account is rejected (PRD §4.3 acceptance criterion).
- The inventory distinguishes `PENDING`, `MANAGED`, `STALE`, `UNREACHABLE`, `RETIRED` (PRD §4.3 acceptance criterion).
- A newly registered server is `PENDING` and grants no operational permission.
- One application spans several servers and one server hosts several applications (PRD §4.9 acceptance criterion).
- A Viewer cannot register or edit anything.

**Tests**

- *Unit:* request-schema validation (hostname, IP, enums, immutable fields); cursor encode/decode and tamper rejection; filter allowlist rejection.
- *Integration:* CRUD round trips; duplicate rejection; cross-scope read returns `404`; pagination stability under concurrent inserts; `If-Unmodified-Since` conflict; audit rows for every mutation.
- *Frontend:* list renders and filters; form validation errors map to fields; permission-based control hiding.

**Security checks**

- [ ] Scope filtering verified by querying as a narrowly scoped user and confirming `total` excludes hidden rows.
- [ ] Immutable fields rejected on `PATCH`.
- [ ] Free-text search is parameterised (injection suite).
- [ ] Seeded demonstration data is visibly marked.

**Definition of done.** Inventory usable end to end in Compose; test suite passing; `PROGRESS.md` updated.

**Risks / rollback.** Risk: a late OPEN-02 reversal forces a schema and API change. **Retired 2026-10-14** — OPEN-02 was confirmed environment-agnostic before this milestone started, which was the whole point of confirming first; environment handling stays in one place (`servers`, `deployment_targets`, `deployments`). Rollback: additive migration, revertible.

---

## M1.6 — Dashboard shell (database-only)

**Tasks**

- [ ] `GET /dashboard/summary` and `/dashboard/health` using **PostgreSQL only**; metric sections return `data_state: "not_configured"` until Phase 2.
- [ ] Short-TTL Redis caching with explicit `collected_at`.
- [ ] Frontend dashboard: health summary cards, server status table, filters, drill-through links, and loading / empty / **stale** / error states.
- [ ] `StatusBadge` as the single owner of status visuals (icon + text + colour).
- [ ] Configurable auto-refresh.

**Expected files**

```
backend/app/dashboard/{router,service,schemas}.py
frontend/src/pages/dashboard/Dashboard.tsx
frontend/src/components/{StatusBadge,DataStateBanner,EmptyState,ErrorState}.tsx
backend/tests/dashboard/test_summary.py
```

**Dependencies.** M1.5.

**Acceptance criteria**

- Selecting a status filter updates the server list (PRD §4.2 acceptance criterion).
- Unavailable sections render as unavailable, **never as `0`** (PRD §4.2 acceptance criterion).
- `collected_at` is displayed for every data section.
- Summary cards link to the relevant detail view.
- Status is never conveyed by colour alone (PRD §8).

**Tests**

- *Unit:* aggregate query correctness on a seeded fixture; `data_state` selection logic.
- *Integration:* filter combinations; cache hit/miss behaviour; scope-filtered counts.
- *Frontend:* a `null` metric renders as "unavailable" and not `0` (a direct regression test for the PRD's own warning); empty, loading and error states; `axe` has no violations.

**Security checks**

- [ ] Dashboard counts respect scope.
- [ ] Cache keys include the identity's scope so one user's aggregate cannot be served to another.

**Definition of done.** Dashboard renders real inventory with honest data states; tests pass; `PROGRESS.md` updated.

**Risks / rollback.** Risk: a cache key that ignores scope leaks counts across users. Mitigation: explicit test. Rollback: disable caching (correct, slower).

---

## M1.7 — Phase 1 consolidation

**Tasks**

- [ ] Compose smoke test script asserting the full `core` stack ([TESTING.md §11](TESTING.md#11-docker-compose-smoke-tests)).
- [ ] First E2E tests: login → dashboard → server list → register server → unauthorised action rejected.
- [ ] `.env.example` completeness check; developer setup doc verified on a clean machine.
- [ ] Demonstration-mode banner wired to `demo_mode`.
- [ ] Coverage gate enabled in CI.
- [ ] `PROGRESS.md` updated with actual results.

**Acceptance criteria.** A new developer can go from clone to a working stack by following the README alone; E2E suite passes in CI.

**Definition of done.** PRD §13 Phase 1 deliverables all demonstrable; no item marked complete without recorded evidence.

---

# Phase 2 — Monitoring

**Objective.** Real host and systemd-unit visibility with honest staleness (PRD §13 Phase 2).

**Prerequisites.** Phase 1 complete. Prometheus in Compose. At least one exporter target (the bundled `node-exporter` suffices). OPEN-14 (target discovery) and OPEN-09 (Windows scope) decided; TS-01 (charting library) and TS-02 (systemd exporter) selected.

**Dependencies.** M1.2 (schema), M1.4 (authz), M1.5 (inventory + `metrics_instance`), M1.6 (dashboard shell).

---

## M2.1 — Prometheus client and metric templates

**Tasks:** read-only Prometheus client with timeouts, retries and a circuit breaker; the allowlisted template catalogue with **per-OS-family** queries; query-bound guards (max range, point count, server count); `GET /metrics/templates`; an integration-status entry for Prometheus.

**Files:** `backend/app/metrics/{client,templates,service,router,schemas}.py`, `infra/prometheus/prometheus.yml`, `backend/tests/metrics/{test_templates.py,test_guards.py}`.

**Acceptance:** rendered PromQL matches expected strings for each template and OS family; a query exceeding a guard returns `422 query_limit_exceeded`; Prometheus down ⇒ `data_state: "unavailable"`, never a 500 and never a zero.

**Tests:** *Unit* — template rendering, including label escaping; guard arithmetic; error-class mapping. *Integration* — against a Prometheus container with seeded series; timeout and circuit-breaker behaviour with a stub that hangs.

**Security:** no client-supplied PromQL accepted (injection suite including label-escape attempts); Prometheus not reachable from the browser; metric endpoints require `metrics:read`.

**DoD:** templates documented in code and matching [API_CONTRACTS.md §15](API_CONTRACTS.md#15-server-metrics).

**Risks:** label mismatch between inventory and Prometheus silently yields no data. Mitigation: a verification step at registration and a `metrics_instance` health indicator rather than an empty chart.

---

## M2.2 — Host metrics endpoints and charts

**Tasks:** endpoints 15.1–15.3; threshold evaluation; frontend metric cards, range charts with selectable ranges, and server/environment comparison.

**Acceptance:** metrics attributed to the correct server (PRD §4.4 acceptance criterion); the displayed time range and timestamp are visible; **gaps render as gaps**; history respects retention.

**Tests:** *Unit* — threshold classification, including boundary values. *Integration* — instant and range queries against seeded series; a server with no series returns `unavailable`. *Frontend* — a series containing `null` renders a gap, asserted explicitly (the single most likely way to violate PRD §4.4).

**Security:** per-server scope enforced on metric reads; comparison endpoint drops out-of-scope servers and reports `excluded_count`.

**Risks:** chart library coerces `null` to `0`. Mitigation: this is a selection criterion ([TECH_STACK.md §2.5](TECH_STACK.md#25-charting-library)) with a test, not a hope.

---

## M2.3 — Target discovery and agent health

**Tasks:** generate the Prometheus target file from inventory (OPEN-14) and trigger a reload; connectivity/agent-health probe task; `agent_status` maintenance; alerting on exporter absence (rules land in M3.3).

**Acceptance:** registering a server with monitoring enabled results in a scraped target without hand-editing Prometheus config; a stopped exporter sets `agent_status` to `UNREACHABLE` within the configured interval.

**Security:** the generated file contains no secret; the reload endpoint/signal is not reachable from the browser; the file writer cannot be influenced by user-supplied content beyond validated inventory fields.

---

## M2.4 — systemd service monitoring

**Tasks:** systemd exporter in Compose with a unit allowlist; state **normalisation** per [ARCHITECTURE.md §6.1](ARCHITECTURE.md#61-monitoring-read); endpoints 16.1–16.7 (reads); `services.last_observed_*` refresh job; dashboard failed-services panel; frontend service list and detail.

**Acceptance (all PRD §4.5 criteria):** a failed service is identifiable without logging in; state is always shown with its collection timestamp; **an unreachable server does not render as all-services-inactive**; `UNSUPPORTED`, `UNCOLLECTED` and `FAILED` are distinct.

**Tests:** *Unit* — the normalisation table, case by case, including host-stale overriding unit state and the Windows `UNSUPPORTED` path. *Integration* — stop a unit in a container and observe `FAILED`; stop the exporter and observe `UNCOLLECTED`; stop the host and observe `UNKNOWN` for all units. *Frontend* — the unreachable-server case renders "unknown", not "inactive".

**Security:** unit state is read from metrics, not by executing anything on the host; log tail requires `logs:read:system`.

**Risks:** scraping every unit explodes cardinality. Mitigation: exporter-side allowlist aligned with registered services.

---

## M2.5 — Stale-data detection

**Tasks:** per-environment staleness thresholds in settings; the stale sweep job updating `servers.status`; health classification combining `last_seen_at`, exporter `up` and sample recency; `data_state` propagation; dashboard "stale servers" counter.

**Acceptance:** a server that stops reporting becomes `STALE` then `UNREACHABLE` per thresholds (PRD §4.2 acceptance criterion); a healthy server is never confused with a silent one; **the system never reports healthy merely because no alert arrived** (PRD §2).

**Tests:** *Unit* — classification across a time matrix (fresh, warning, stale, unreachable, never-seen). *Integration* — simulate scrape loss with a frozen clock and assert the transitions; `last_seen_at IS NULL` is treated as never-seen, not stale.

**DoD:** PRD §13 Phase 2 deliverables demonstrable; `PROGRESS.md` updated with real results.

---

# Phase 3 — Logs and alerts

**Objective.** Centralised log search and an alert lifecycle with notifications (PRD §13 Phase 3).

**Prerequisites.** Phase 2 complete. Loki, Alloy and Alertmanager in Compose. OPEN-03 (rule authoring), OPEN-08 (retention) and OPEN-13 (channel config) decided.

**Dependencies.** M1.4 (authz scoping), M2.x (server/service identity for log labels and alert targets).

---

## M3.1 — Alloy and Loki ingestion

**Tasks:** Alloy config for journald, Nginx, PHP-FPM, application and Docker sources; the bounded label set; redaction stages; Loki config with limits and retention; sample log producers for local development.

**Acceptance:** logs from a sample server are queryable in Loki with exactly the allowed labels; a seeded secret pattern is redacted before storage.

**Tests:** *Integration* — produce a log line, assert it appears with expected labels; assert a high-cardinality label is dropped; assert a canary secret does not reach Loki.

**Security:** label allowlist enforced; outbound push only; redaction tested rather than assumed, with its best-effort limitation documented in [SECURITY.md §17](SECURITY.md#17-sensitive-log-redaction).

---

## M3.2 — Log search API and UI

**Tasks:** Loki client with timeouts and circuit breaker; **server-side LogQL construction** from structured filters; pattern-preset catalogue; endpoints 19.1–19.3; cursor pagination; outbound redaction; frontend log search with filters, result list, source/timestamp display, and navigation from a failed service or a deployment to its logs.

**Acceptance (PRD §4.7 criteria):** an authorised user searches across several servers; results show source and timestamp; unauthorised users cannot query restricted environments; limits and retention are respected. Loki down renders an explicit unavailable state — **never "no logs found"**, which during an incident is a dangerous lie.

**Tests:** *Unit* — selector construction for every filter combination; `contains` escaping; limit/range guard arithmetic. *Integration* — query seeded logs; scope intersection drops unreadable servers and reports `excluded_by_scope_count`; Loki outage path. *Frontend* — filters, pagination, and the unavailable banner.

**Security:** no raw LogQL accepted (injection suite); `logs:read:system` vs `logs:read:application` separation tested; log lines rendered as text (XSS suite with markup in a log line); filters are not in a URL or access log (`POST`, OPEN-20).

---

## M3.3 — Alert rules and lifecycle

**Tasks:** Prometheus rule files for the PRD §4.8 conditions (server stopped reporting, CPU/memory/disk thresholds, failed unit, failed health check, failed deployment, operation timeout); Alertmanager routing; the webhook receiver with shared-secret auth, schema validation and source restriction; `alert_events` upsert with `dedup_key`; endpoints 20.1–20.5; frontend alert list, detail, acknowledge and assign.

**Acceptance (PRD §4.8 criteria):** a configured condition produces an alert event; repeats are grouped/deduplicated; resolved alerts retain history; **acknowledgement does not mark the underlying problem resolved**.

**Tests:** *Unit* — `dedup_key` computation; status transition legality; label→target resolution including the unmatched case. *Integration* — post webhook payloads (new, repeat, resolved, malformed, unmatched labels, batch with one bad entry); acknowledge and assert `resolved_at` stays null; rule evaluation against seeded series using `promtool`.

**Security:** shared secret verified in constant time; source restriction and rate limiting enforced; **a crafted alert payload cannot cause any operation to execute** — an explicit adversarial test ([SECURITY.md §21](SECURITY.md#21-alert-webhook-ingress)); label and annotation lengths capped.

**Risks:** an alert storm floods rows or notifications. Mitigation: dedup plus rate limit with explicit, logged overflow behaviour.

---

## M3.4 — Notifications

**Tasks:** `notification_channels` and `notification_deliveries` tables and admin endpoints; routing rules; delivery tasks on the `notifications` queue with bounded retry and backoff; delivery status visible in the UI; a manual retry action.

**Acceptance (PRD §4.8):** notification failures are **observable and retryable**; destination references come from registered configuration only.

**Tests:** *Unit* — routing-rule matching; backoff schedule; `ABANDONED` after the cap. *Integration* — delivery success, transient failure then success, permanent failure; manual retry; a webhook destination resolved from the secret store.

**Security:** SSRF controls on webhook destinations (metadata IP, loopback, RFC1918, redirect, DNS rebinding); destination secrets never returned by the API; channel changes audited.

**DoD:** PRD §13 Phase 3 deliverables demonstrable; `PROGRESS.md` updated.

---

# Phase 4 — Controlled service operations

**Objective.** Execute allowlisted service operations safely, with approvals, background tracking, verification and a complete audit trail (PRD §13 Phase 4). **The highest-risk phase in the project.**

**Prerequisites.** Phases 1–3 complete. A non-production cloud account with a scoped execution role. The registered SSM document / Run Command script authored and reviewed. OPEN-05 (agent) and OPEN-10 (approval policy) decided.

**Dependencies.** M1.2 (`operations`, `approvals`, policies), M1.4 (authz), M2.4 (service state for verification), M3.3 (operation-failure alerts).

---

## M4.1 — Provider adapter interface and the demo adapter

**Tasks:** the `ProviderAdapter` interface, result envelope and error taxonomy; capability sets; adapter registry by `cloud_accounts.provider`; `DemoAdapter`; `GET /cloud-accounts/{id}/capabilities`; `demo_mode` propagation.

**Acceptance:** no provider-specific branching exists above the adapter layer (reviewed, and grep-checked for provider names outside `providers/`); demonstration mode returns `not_configured` for writes and marks every affected response.

**Tests:** *Unit* — error mapping for each class; capability gating returning `UnsupportedOperation`. *Integration* — a full operation lifecycle against `DemoAdapter`.

**Security:** the interface exposes **no** command/script/argv parameter — enforced by the signature itself, so a shell path has nowhere to exist.

---

## M4.2 — Operation lifecycle and the state machine

**Tasks:** the state machine with guarded transitions and illegal-transition errors; `deadline_at`; the timeout reaper; `parent_operation_id` retries; endpoints 17.1–17.3 and 22.1–22.4; the one-in-flight partial unique index in use; idempotency handling; Celery `operations` queue with `acks_late` and a visibility timeout longer than the deadline; worker-side re-authorisation and target re-validation; frontend confirmation dialog, progress polling and result display.

**Acceptance (PRD §4.6 criteria):** a user without permission cannot start/stop/restart; an approved restart creates an operation record; the UI shows the execution result **and the verified service state**; failed operations preserve redacted diagnostics; duplicate requests do not execute twice.

**Tests:** *Unit* — every legal transition and a representative set of illegal ones; deadline computation; idempotency key resolution. *Integration* — happy path to `SUCCEEDED` with `observed_state_after`; verification mismatch ⇒ `FAILED`; provider timeout ⇒ `TIMED_OUT`; concurrent duplicate submissions produce exactly one execution; two workers racing one row produce exactly one claim; worker killed mid-operation ⇒ reaper times it out and does **not** re-invoke; permission revoked while queued ⇒ the worker refuses.

**Security:** the full allowlist gate in order; closed action enum; `403` audited; no field anywhere accepts a command; worker re-check proven by revoking permission between request and execution.

**Risks:** the worst failure mode in this project is a *wrongly reported* success. Mitigation: verification required by the state machine **and** by a database `CHECK`; failing that test blocks release.

---

## M4.3 — AWS SSM adapter

**Tasks:** `boto3` client with role-based credentials and adaptive retries; invoke the **registered document** with typed parameters; persist `CommandId` **before** polling; poll with backoff; verification read; throttling honoured via `retry_after`; `DescribeInstanceInformation` connectivity; inventory sync; error-class mapping.

**Acceptance:** a restart on a non-production test instance succeeds and is verified; a throttled response backs off instead of hammering; a permission error surfaces as a configuration problem with actionable text.

**Tests:** *Unit* — parameter construction; error mapping; backoff. *Integration* — against a mocked AWS layer for the full matrix (success, throttle, auth error, not-found, timeout, partial failure). *Manual, documented, non-production only* — one real instance test, with its result recorded in `PROGRESS.md` as a manual check rather than an automated pass.

**Security:** the execution role is verified to **deny** `AWS-RunShellScript` and `AWS-RunPowerShellScript`; targeting restricted by tag; no static keys present; the document itself reviewed for parameter injection (a service-name parameter must not be interpolable into a shell line).

**Risks:** the SSM document is the one place a shell line genuinely exists. Mitigation: treat it as security-critical code — reviewed, version-controlled, parameter-validated on both sides.

---

## M4.4 — Azure adapter

**Tasks:** managed-identity credential; ARM inventory; Run Command with a registered script; Azure Arc for supported external servers; **model Azure's per-VM serialisation and async semantics explicitly**; capability set reflecting real differences.

**Acceptance:** unsupported operations are reported explicitly as unsupported, never as a generic failure (PRD §4.11 acceptance criterion); Azure-specific limits are visible in the capability matrix.

**Tests:** as M4.3 against a mocked Azure layer, plus a concurrency test asserting that a second Run Command on the same VM is handled as a provider-level conflict rather than a DCP bug.

**Security:** role assignment scoped to a resource group; no wildcard actions; no `Microsoft.Authorization/*`.

**Risks:** assuming AWS semantics. Mitigation: the capability matrix is a contract, and a test asserts the adapters disagree where they genuinely differ.

---

## M4.5 — Approval workflow

**Tasks:** policy evaluation at request time and re-check at execution; approval creation, decision endpoints, expiry sweep; **self-approval denial**; approver queue UI with the `self_requested` flag; approval notifications; audit with approval references.

**Acceptance:** a production operation under a policy cannot execute without an approval; a requester cannot approve their own request unless the policy explicitly allows it (PRD §3.6); an expired approval window cancels rather than lingering as live production intent.

**Tests:** *Unit* — policy evaluation matrix (environment × action × policy); self-approval decision. *Integration* — approve→execute; reject→no execution; expire→cancel; approve twice ⇒ `409`; **direct execution attempt bypassing approval ⇒ refused by the worker** (the adversarial test for mandatory rule 6).

**Security:** approval bypass suite; policy missing or unreadable ⇒ deny; approver scope enforced.

---

## M4.6 — Audit API and operations history

**Tasks:** endpoints 23.1–23.2 and 24.1–24.3; the export task; frontend operations-history and audit-log pages with filters.

**Acceptance (PRD §4.12 criteria):** every privileged operation traces to its initiating identity; operation history includes result and execution id; **audit records cannot be altered through ordinary APIs**.

**Tests:** *Integration* — audit completeness across every privileged path (a test that enumerates state-changing endpoints and asserts each produced an audit row); immutability under the app role; `AUDIT_QUERIED` written when the trail is read; export bounds enforced.

**Security:** `audit:read` restricted to Approver/Administrator; no secret in any audit row (canary sweep); export is audited and single-retrieval.

**DoD:** PRD §13 Phase 4 deliverables demonstrable; every security check in this phase passing; `PROGRESS.md` updated.

---

# Phase 5 — Deployment management

**Objective.** Trigger, track and verify Jenkins deployments with production approvals (PRD §13 Phase 5).

**Prerequisites.** Phase 4 complete. A Jenkins instance (mock locally) with a test pipeline. API token in the secret store. `deployment_targets` registered. OPEN-18 decided.

**Dependencies.** M1.2/M4.x (lifecycle, approvals, audit patterns), M3.2 (log viewing for build logs), M2.x (health checks reuse server/service state).

---

## M5.1 — Deployment targets and Jenkins client

**Tasks:** `deployment_targets` admin endpoints and UI; `GET /deployment-targets`; Jenkins REST client (trigger with parameters, queue→build resolution, status, console tail) with timeouts, backoff and a circuit breaker; CSRF-crumb handling; host allowlist; mock Jenkins in the `mocks` profile.

**Acceptance:** only registered targets are selectable; a request naming an unregistered application+environment pair gets `422 not_registered`.

**Tests:** *Unit* — parameter-template rendering; ref-pattern matching (allowed, denied, regex-anchoring edge cases). *Integration* — against mock Jenkins: trigger, queue→build resolution, status polling, console fetch, outage.

**Security:** **no Jenkins URL, job name or build parameter is accepted from a request** (adversarial test attempting all three); token read from the secret store and never returned; host allowlist enforced.

---

## M5.2 — Deployment lifecycle

**Tasks:** endpoints 21.1–21.6; the state machine; `external_execution_id` persisted before polling; the polling task with backoff and deadline; `last_poll_error` surfaced; idempotency; one-active-deployment-per-application+environment; frontend deployment page per PRD §8 (application, environment, ref, requester, approval status, pipeline status, build logs, health results, rollback availability).

**Acceptance (PRD §4.10 criteria):** an authorised user triggers a registered non-production pipeline; progress and result are visible; every deployment has a complete audit record; failures show useful diagnostics.

**Tests:** *Unit* — transitions; ref validation; deadline. *Integration* — full lifecycle; Jenkins unavailable mid-poll leaves state intact with `last_poll_error` and recovers; worker restart resumes polling without re-triggering; duplicate requests produce one build.

**Security:** environment-scoped `deployment:create` (a Developer with non-production scope is refused production); audit on every transition.

---

## M5.3 — Build logs

**Tasks:** endpoint 19.3; pagination/offset handling; redaction; frontend log panel with follow-mode.

**Acceptance:** authorised users see pipeline logs; unauthorised users cannot.

**Security:** redaction canary in console output (build logs are a classic secret-leak path); `deployment:read` scoped; lines rendered as text.

---

## M5.4 — Production approvals for deployments

**Tasks:** reuse M4.5 policy machinery for `applies_to = DEPLOYMENT`; approval UI in the deployment flow; approval notifications; the pre-execution display of the exact deployment target.

**Acceptance:** production deployments follow the configured approval workflow (PRD §4.10 acceptance criterion); self-approval denied by default; the exact target is shown before execution.

**Tests:** *Integration* — production without approval ⇒ no trigger; approve ⇒ trigger; reject ⇒ no trigger; approval expiry; worker re-check refuses a revoked approval.

**Security:** approval bypass suite for deployments, mirroring M4.5.

---

## M5.5 — Post-deployment health checks and rollback

**Tasks:** health-check runner against **registered** endpoints with retries and timeouts; conjunctive success policy (AD-10); `health_check_result` persisted; `FAILED` with `failure_reason = HEALTH_CHECKS_FAILED` when checks fail despite pipeline success; endpoint 21.7 rollback where the target declares support; `ROLLED_BACK` only after the rollback deployment succeeds.

**Acceptance (PRD §4.10, mandatory rule 8):** **a deployment is never reported successful until the pipeline and the configured health checks both satisfy the success policy**; rollback is offered only where the deployment system provides it.

**Tests:** *Unit* — success policy evaluation (pipeline pass + checks pass/fail/partial; no checks configured). *Integration* — pipeline success + health failure ⇒ `FAILED`; health flake then pass within retries ⇒ `SUCCEEDED`; rollback flow; rollback on an unsupporting target ⇒ `422`.

**Security:** health-check URLs are registered only, SSRF-validated at config and request time, no redirect following; response bodies are not echoed verbatim.

**DoD:** PRD §13 Phase 5 deliverables demonstrable; `PROGRESS.md` updated with real results.

**Risks:** Jenkins semantics vary by plugin set; the queue→build transition is a known source of lost tracking. Mitigation: persist the queue item id too, and reconcile rather than re-trigger.

---

# Phase 6 — Hardening and expansion

**Objective.** Production readiness: broader provider support, security verification, measured performance, tested backups and documented operations (PRD §13 Phase 6).

**Prerequisites.** Phases 1–5 complete. OPEN-07 (RPO/RTO), OPEN-12 (topology) and OPEN-15 (audit export) decided.

**Dependencies.** All prior phases.

---

## M6.1 — Additional provider support (restricted agent / Arc)

**Tasks:** implement OPEN-05's chosen approach (prefer adopting SSM hybrid activations or Azure Arc over building an agent); per-server credentials, revocable individually; capability set; connectivity and operation paths.

**Acceptance:** a registered non-cloud Linux server is monitorable and operable through the same allowlisted interface with no new API surface.

**Security:** fixed RPC verbs with no free-form command field; per-server credential rotation and revocation; outbound-connecting only.

**Risks:** building an agent means owning a privileged daemon on every host — a large, permanent security surface. This is precisely why adopting a provider-native channel is preferred, and why the choice is a documented decision rather than an implementation detail.

---

## M6.2 — Security hardening and verification

**Tasks:** complete the [SECURITY.md §26](SECURITY.md#26-security-testing) suite in CI; CSP tightening; cookie and header review; least-privilege review of cloud roles against actual API usage; dependency and image scan gates; audit export to an independent store (OPEN-15); read-only mode runbook; incident runbooks ([SECURITY.md §27](SECURITY.md#27-incident-response)).

**Acceptance:** every mandatory control in [SECURITY.md §1](SECURITY.md#1-mandatory-rules) has a passing automated test or a documented manual verification with a recorded result.

**DoD:** a security checklist with evidence per item. **[RECOMMENDED]** external penetration test before production rollout.

---

## M6.3 — Performance testing

**Tasks:** load tests for the dashboard, inventory, metric range and log query paths; concurrency tests for operations; realistic data volumes; index tuning from measured plans; **record measured results** against the PRD §10 budgets.

**Acceptance:** measured p95s recorded in `PROGRESS.md` with the data volume and method stated. Where a budget is missed, the gap is documented with a remediation plan.

**Explicitly:** until this milestone runs, **no performance claim may be made anywhere** — PRD §10 figures are design budgets, not results.

---

## M6.4 — Backup and recovery

**Tasks:** automated encrypted PostgreSQL backups with WAL archiving; documented restore procedure; **scheduled restore rehearsal** into an isolated environment; recovery of application metadata verified against PRD §15 item 13; audit continuity after restore; RPO/RTO targets set (OPEN-07).

**Acceptance:** a restore is performed and verified, with its date and outcome recorded. An untested backup does not count.

**Security:** backup encryption and access separation verified; the restore itself audited.

---

## M6.5 — Production readiness

**Tasks:** production Compose/orchestration manifests with resource limits; TLS termination and HSTS; secrets-manager wiring; self-monitoring dashboards and alerts for DCP itself; documented upgrade and rollback procedures; migration-with-backup deploy runbook; on-call documentation.

**Acceptance:** PRD §11 production requirements each have a documented, verified implementation; the single-host SPOF is explicitly acknowledged and accepted or addressed (OPEN-12).

---

## M6.6 — Expanded roles, scopes and final verification

**Tasks:** `teams` modelling if OPEN-11 resolves that way; scope-granularity expansion if needed; the full E2E suite over PRD §15's thirteen Definition-of-Done items; documentation review for drift.

**Acceptance:** every PRD §15 item demonstrated end to end, with evidence recorded.

**DoD:** PRD §12 release criteria all satisfied — critical authorisation tests pass, privileged actions audited, secret-leakage tests pass, failed/timed-out operations represented correctly, migrations tested, deployment and recovery procedures documented.

---

## Cross-phase rules

1. **Each milestone produces a working increment.** The stack starts and the test suite passes at every milestone boundary.
2. **No forward dependencies.** If a milestone needs something from a later phase, the plan is wrong — fix the plan, do not borrow the feature.
3. **Security tests land with the feature**, never in a later cleanup pass.
4. **Migrations are additive and reversible** wherever PostgreSQL allows.
5. **Documentation follows the code in the same change.** A behaviour that diverges from [API_CONTRACTS.md](API_CONTRACTS.md) or [DATABASE.md](DATABASE.md) is either a bug or a documentation update — never left ambiguous.
6. **Rollback for every milestone** is a branch revert plus (where relevant) an Alembic downgrade. No milestone leaves the system unable to start.
7. **Nothing is marked complete without recorded evidence** ([CLAUDE.md](../CLAUDE.md)).

## Risk register

| Risk | Phase | Impact | Mitigation |
|---|---|---|---|
| Reporting a privileged operation as successful when it was not | 4 | Severe — the operator acts on a lie during an incident | Verification required by the state machine and a DB `CHECK`; explicit mismatch test |
| An execution path that can reach a general shell | 4 | Severe | Interface has no command parameter; IAM explicit deny; document reviewed; CI grep gate |
| Approval bypass | 4, 5 | Severe | Policy checked at request **and** execution; adversarial bypass suite |
| Secret leaking through logs, build logs or diagnostics | 3, 5 | Severe | Two redaction layers; canary tests across logs, responses, audit rows and the bundle |
| Stale data presented as current | 2 | High — wrong operational decisions | `data_state` everywhere; null-not-zero tests; stale sweep |
| Alert-payload-driven action | 3 | High | Webhook is data-only; adversarial test that no payload triggers an operation |
| Duplicate execution from retries | 4, 5 | High | Three-layer idempotency; never blind-retry a write |
| Cardinality blowup in Prometheus/Loki | 2, 3 | Medium | Label allowlists; unit allowlist; query guards |
| Provider semantics assumed identical | 4 | Medium | Capability matrix as a contract, with tests asserting the differences |
| Single-host production SPOF | 6 | Medium | Documented and accepted (OPEN-12); revisit when availability targets exist |
| ~~OPEN-02 reversal after Phase 1~~ | 1 | Medium | **Closed 2026-10-14** — confirmed environment-agnostic before M1.5, so there is nothing to reverse |
| Scope creep into PRD §14 non-goals (terminal, custom CI, Kubernetes) | all | Medium | §14 is a hard boundary; new scope needs an explicit requirement and risk assessment |

## Open decisions gating specific milestones

| Decision | Gates |
|---|---|
| OPEN-01 IdP | M1.3 hardening |
| ~~OPEN-02 application↔environment~~ | **Resolved 2026-10-14 — environment-agnostic** |
| OPEN-03 alert rule authoring | M3.3 |
| OPEN-04 session durability | M1.3 |
| OPEN-05 restricted agent | M4.1, M6.1 |
| OPEN-06 secret store | M1.1 config |
| OPEN-07 RPO/RTO | M6.4 |
| OPEN-08 retention | M2.1, M3.1 |
| OPEN-09 Windows scope | M2.4 |
| OPEN-10 approval policy | M4.5 |
| OPEN-11 teams model | M6.6 |
| OPEN-12 production topology | M6.5 |
| OPEN-13 notification config | M3.4 |
| OPEN-14 target discovery | M2.3 |
| OPEN-15 audit export | M6.2 |
| TS-01 charting library | M2.2 |
| TS-02 systemd exporter | M2.4 |

Full register: [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions).
