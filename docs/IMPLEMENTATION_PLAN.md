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

**Tasks** — implemented 2026-10-14 except where noted.

- [x] Endpoints 14.1–14.7 and 18.1–18.5 ([API_CONTRACTS.md §14](API_CONTRACTS.md#14-server-inventory), [§18](API_CONTRACTS.md#18-applications)). 14.8 is **not** in this milestone: it is a synchronous probe through a provider adapter, and the first adapter is M4.1.
- [x] Pagination (cursor), filtering/sorting allowlists, free-text search. **Cursor only** — §14.1 specifies cursor pagination and §18.1 reuses it so one client paginator serves both; offset pagination already exists for the administrative collections (§25).
- [x] Duplicate detection surfaced as `409 duplicate_resource`.
- [x] Scope-filtered queries (filter inside the query, not after it) — `app/authz/queries.py`, cross-checked against the matcher row by row.
- [x] Optimistic concurrency via `If-Unmodified-Since`.
- [x] `metrics_instance` uniqueness and format validation.
- [x] Audit writing for every inventory mutation; `SERVER_ENVIRONMENT_CHANGED` treated as privilege-relevant and requiring Administrator.
- [x] Frontend: server list with filters, server detail (Overview tab), registration form, application list/detail. **Not built:** a server *edit* form and association management in the UI — `PATCH`, `POST`/`DELETE .../applications` are implemented and tested on the backend, but have no UI control yet. See PROGRESS.md.
- [x] Seed script with synthetic inventory for local development, clearly marked as demonstration data (`python -m app.inventory.seed`).

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

- [x] Scope filtering verified by querying as a narrowly scoped user and confirming the hidden rows are gone. Verified on the **items and `has_more`**, not on `total`: §5 keeps `total` `null` on a cursor page precisely because a total over scope-filtered data invites inference about the rows the caller cannot see.
- [x] Immutable fields rejected on `PATCH`.
- [x] Free-text search is parameterised, with its own wildcards escaped.
- [x] Seeded demonstration data is visibly marked (`[DEMO]` prefix, `.invalid` hostnames, `provider_metadata.synthetic`, and the seed refuses to run outside `local`).

**Definition of done.** Inventory usable end to end in Compose; test suite passing; `PROGRESS.md` updated.

**Risks / rollback.** Risk: a late OPEN-02 reversal forces a schema and API change. **Retired 2026-10-14** — OPEN-02 was confirmed environment-agnostic before this milestone started, which was the whole point of confirming first; environment handling stays in one place (`servers`, `deployment_targets`, `deployments`). Rollback: additive migration, revertible.

---

## M1.6 — Dashboard shell (database-only)

**Tasks**

- [x] `GET /dashboard/summary` and `/dashboard/health` using **PostgreSQL only**; metric sections return `data_state: "not_configured"` until Phase 2.
- [x] Short-TTL Redis caching with explicit `collected_at`. `DCP_DASHBOARD_CACHE_TTL_SECONDS`, default 15 s, `0` disables.
- [x] Frontend dashboard: health summary cards, server status table, filters, drill-through links, and loading / empty / error states. **`stale` is rendered by `DataStateBanner` but no section can reach that state yet** — nothing collects, so nothing can go stale. *Corrected 2026-10-16: this said "first reachable in M2.1", and it is not.* M2.1 gives the dashboard no source — the `utilisation` section stays `not_configured` with nulls until M2.2 wires §15.1 to it — so the first section that can go `stale` arrives with M2.2, and `stale` as a *computed* state over `last_seen_at` is M2.5.
- [x] `StatusBadge` as the single owner of status visuals (glyph + text + colour), replacing `pages/inventory/states.tsx`.
- [x] Configurable auto-refresh (`VITE_DASHBOARD_REFRESH_SECONDS`, default 30 s, `0` disables).

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

- [x] Dashboard counts respect scope — applied inside the query via `app/authz/queries.readable_servers`, covered by `test_counts_are_scope_filtered` and `test_the_health_list_is_scope_filtered`.
- [x] Cache keys include the identity's scope so one user's aggregate cannot be served to another — `test_a_scope_change_is_not_served_from_the_previous_cache_entry` narrows a live caller's grant inside the TTL and asserts the narrowed value, plus four unit cases on the key itself. Both were confirmed to fail against a deliberately scope-blind key before being relied on.

**Definition of done.** Dashboard renders real inventory with honest data states; tests pass; `PROGRESS.md` updated.

**Risks / rollback.** Risk: a cache key that ignores scope leaks counts across users. Mitigation: explicit test. Rollback: disable caching (correct, slower).

---

## M1.7 — Phase 1 consolidation

**Tasks**

- [x] Compose smoke test script asserting the full `core` stack ([TESTING.md §11](TESTING.md#11-docker-compose-smoke-tests)). `scripts/smoke-test.sh`, covering §11 steps 1–7 and 9–12, **passed twice on 2026-10-15** including once from empty volumes. Step 8 is the observability profile (M2.1/M3.1) and is not asserted. This also closes **issue 20** — the script had never been executed before.
- [x] First E2E tests: login → dashboard → server list → register server → unauthorised action rejected. Playwright, `frontend/e2e/`, covering [TESTING.md §12](TESTING.md#12-end-to-end-tests-playwright) paths 1, 2 and 8. **11 tests, five consecutive clean runs.** Paths 3–7 and 9 need Phase 2+ and are deliberately not stubbed.
- [x] `.env.example` completeness check; developer setup doc verified. `scripts/check_env_example.py` (five rules, in CI as the `env-example` job) found **six undocumented settings and one value that disagreed with the compose default**. Setup verified by cloning the branch to a fresh directory and following the README: every service healthy, `/readyz` all three checks true, `alembic current` at `0004 (head)`, and a login round-trip ending on `/dashboard` with `roles: []`. **Not a clean machine** — see the wording in [`PROGRESS.md`](../PROGRESS.md) §7, which states exactly what was and was not fresh.
- [x] Demonstration-mode banner wired to `demo_mode`. **Already done in M1.3** and unit-tested (`SessionProvider.test.tsx`, "is never silent"); M1.7 added the end-to-end assertion against a real stack, which is the part no unit test could make. No new code.
- [x] Coverage gate enabled in CI. `scripts/check_coverage_diff.py` enforces `OPEN-22`'s 80 % floor on **changed lines**, per-diff, on pull requests. It failed on this milestone's own diff at 60 % and the tests it asked for were written.
- [x] `PROGRESS.md` updated with actual results.

**Acceptance criteria.** A new developer can go from clone to a working stack by following the README alone; E2E suite passes in CI.

**Met, and the criterion was not met before this milestone.** Three separate faults stood between a fresh clone and a working stack, each invisible because the volume on the one machine that had ever run it outlived every `up`: nothing applied the migrations, the worker's healthcheck could never pass, and the root `.env` the README tells you to create was never read. All three are fixed and asserted. The E2E suite passes in the new `e2e` CI job; it has not yet been observed running on GitHub Actions, only locally — see [`PROGRESS.md`](../PROGRESS.md) §7.

**Definition of done.** PRD §13 Phase 1 deliverables all demonstrable; no item marked complete without recorded evidence.

---

# Phase 2 — Monitoring

**Objective.** Real host and systemd-unit visibility with honest staleness (PRD §13 Phase 2).

**Prerequisites.** Phase 1 complete. Prometheus in Compose. At least one exporter target (the bundled `node-exporter` suffices). OPEN-14 (target discovery) and OPEN-09 (Windows scope) decided; TS-01 (charting library) and TS-02 (systemd exporter) selected.

**Prerequisite state as of M2.1 (2026-10-16): three of the five are still open, and the milestone that needs each is named rather than the whole phase being blocked on all of them.** Phase 1 is complete; Prometheus and `node-exporter` are now configured and scraped rather than declared, which satisfies the second and third. `OPEN-09` has a working default *recorded and implemented* (Windows is `UNSUPPORTED`, see M2.1 below) rather than being decided. **`OPEN-14` still gates M2.3**, not M2.1 — the target list in `infra/prometheus/prometheus.yml` is static on purpose. **`TS-01` is RESOLVED (2026-10-16)**: `d3-shape` + `d3-scale`, used directly with no React wrapper, integrated as `frontend/src/components/chart/linePath.ts` ([TECH_STACK.md §2.5](TECH_STACK.md#25-charting-library)). It was decided the way this plan asked — with the gap test in hand, not under M2.2's deadline — and the test stays as a standing regression test. **M2.2 is no longer blocked on a decision.** **`TS-02` gated M2.4 and is RESOLVED (2026-10-17)**: `node_exporter`'s systemd collector with a unit allowlist, chosen after testing it against a real systemd rather than from documentation ([TECH_STACK.md §5.3](TECH_STACK.md#53-systemd-metrics-integration)). The test found that no exporter exposes `sub_state`, `load_state`, enabled state or main PID; that gap is `OPEN-29`, agreed as `null` for M2.4. M2.1 needed none of the three.

**Dependencies.** M1.2 (schema), M1.4 (authz), M1.5 (inventory + `metrics_instance`), M1.6 (dashboard shell).

---

## M2.1 — Prometheus client and metric templates

**Status: complete (2026-10-16).** Evidence beside each item.

**Tasks**

- [x] Read-only Prometheus client with timeouts, retries and a circuit breaker. `backend/app/metrics/client.py`. Instant and range reads; `follow_redirects=False` (§19.5); a 16 MB response cap; a deadline at **both** ends (an `httpx.Timeout` and Prometheus's own `timeout` parameter, asserted by `test_every_call_carries_a_deadline_at_both_ends`). **Retry narrowed to one attempt for `ConnectError`/`ConnectTimeout` only** — every call here is a read, so retrying is safe in principle, but a *read* timeout is not retried because that would silently double the 10 s deadline §15.2 publishes. Breaker: five consecutive failures to open, 30 s cooldown, exactly one half-open probe, a failed probe re-opening for a full cooldown. 12 breaker and retry cases in `tests/metrics/test_client.py`, all against `httpx.MockTransport` with an injected clock, so no test sleeps and the hang case is deterministic.
- [x] Allowlisted template catalogue with per-OS-family queries. `backend/app/metrics/templates.py`: the ten templates [AD-04](ARCHITECTURE.md#4-metrics-collection-architecture) names, **LINUX only**. `test_linux_renders_the_expected_query` pins all ten rendered strings literally rather than re-deriving them. **`OPEN-09` stays open with Windows-unsupported as its recorded default** — see the note below.
- [x] Query-bound guards (max range, point count, server count). `backend/app/metrics/service.py`, every bound transcribed from §15.2/§15.3 rather than invented: 30 days, 11 000 points, step ≥ 15 s, 1–5 templates, ≤ 20 servers, ≤ 4 environments. 14 cases in `tests/metrics/test_guards.py`, each at `max` and `max + 1`, including the off-by-one that matters (`(MAX_POINTS - 1) * step` yields exactly `MAX_POINTS` because Prometheus returns a point at `start`, so a floored division would admit a query one point over).
- [x] `GET /metrics/templates`. §15.4, `metrics:read`, no parameters — and every query parameter rejected, so `?metrics=cpu_utilisation` is a `422` rather than the full catalogue pretending a filter applied. `backend/app/metrics/router.py`.
- [x] An integration-status entry for Prometheus. §26.1 at `GET /api/v1/integrations/status`, `admin:integrations`, with a **live probe** (`vector(1)`, which touches no series) for Prometheus and honest `not_configured` rows for the six integrations the contract names that have no configuration surface yet. Three states, not four: **`degraded` is deliberately not emitted** — see the note below.
- [x] **Closed the Prometheus half of [TESTING.md §11](TESTING.md#11-docker-compose-smoke-tests) step 8**, the one smoke-test step M1.7 left uncovered. `prometheus` and `node-exporter` stop being Compose placeholders; `scripts/smoke-test.sh` step 18 starts the `observability` profile and queries **all ten templates** against it: the exporter target `up` and four more values asserted in range, and the remaining five — the ones carrying label filters — asserted to be valid PromQL with whatever value they produce printed, because which filesystems, disks and interfaces a containerised exporter sees is an environment detail and a smoke test that failed on it would be wrong about the code. All ten returned real values on the development machine — **run inside the backend container through `app/metrics/client.py`**, so what passes is the code the API uses rather than a `curl` that happens to produce the same JSON. Its Loki half still needs M3.1.

**Files:** `backend/app/metrics/{client,templates,service,router,schemas}.py`, `infra/prometheus/prometheus.yml`, `backend/tests/metrics/{test_templates,test_guards,test_client,test_status}.py`. Two test files beyond the plan's list: the plan's own test line asks for "error-class mapping" and "circuit-breaker behaviour with a stub that hangs", and neither belongs in a file about templates or guards. Also `backend/tests/integration/test_metrics_api.py` for the authorisation paths, which need a real session and a real grant.

**Acceptance: met.**

- *Rendered PromQL matches expected strings for each template and OS family* — `test_linux_renders_the_expected_query`, ten pinned strings; `test_windows_has_no_queries_so_it_resolves_to_unsupported` asserts the other family raises `UnsupportedOsFamilyError`.
- *A query exceeding a guard returns `422 query_limit_exceeded`* — `QueryLimitExceededError`, one code for every bound with the crossed maximum in `details`, asserted per bound.
- *Prometheus down ⇒ `data_state: "unavailable"`, never a 500 and never a zero* — `PrometheusUnavailableError` is **not** an `AppError`, deliberately, so it cannot be rendered as a 5xx if it escapes a caller (`test_an_http_error_is_never_rendered_as_a_5xx` asserts both the reason and the non-`AppError`-ness across six upstream statuses). `NaN`, `+Inf` and `-Inf` parse to `None`, never `0`.

**Tests:** 121 unit in `tests/metrics/` across 4 files (63 template, 38 client, 13 guard, 7 status — the template and injection cases are parametrized), 15 new config cases, and 6 integration in `tests/integration/test_metrics_api.py` for the authorisation paths. Actual results and the commands run are in [`PROGRESS.md`](../PROGRESS.md) §7.

**Security: met.**

- *No client-supplied PromQL accepted* — nothing in the module takes a query from a request; §15.4 takes no parameter at all. The 20-case injection suite in `test_templates.py` covers label-escape attempts (closing the string literal, a trailing backslash, a pre-escaped quote, CR/LF/tab, regex wildcards, an extra matcher, a PromQL modifier, `${selector}` itself) and each is **refused rather than escaped**, including when it appears beside a valid instance. Escaping is a second layer, tested on its own because it is what holds if the pattern is ever loosened.
- *Prometheus not reachable from the browser* — published to loopback for `promtool` and the expression browser only; the SPA has no code path to it and no response carries rendered PromQL (`test_the_catalogue_never_exposes_promql`).
- *Metric endpoints require `metrics:read`* — route-level dependency, asserted by the existing route-coverage gate and by `test_a_caller_with_no_grant_is_refused_for_the_right_reason`, which also asserts the refusal is not a CSRF refusal.

**DoD: met.** Templates are documented in code and match [API_CONTRACTS.md §15](API_CONTRACTS.md#15-server-metrics); §15.4's payload is served; §26.1's row shape is served.

**Risks.** Label mismatch between inventory and Prometheus silently yields no data. **Not yet mitigated, and deliberately so:** the registration-time verification and the `metrics_instance` health indicator this line proposes are M2.3's `agent_status` work (§14.2 is explicitly out of M2.1's scope). What M2.1 contributes is that the mismatch is *observable* — an unscraped instance returns no series rather than a zero, asserted by smoke-test step 18 — and that `agent_up` is in the catalogue for the indicator to read.

**Two decisions recorded rather than quietly taken.**

1. **`OPEN-09` (Windows scope): templates are LINUX-only, and a WINDOWS server gets the `UNSUPPORTED` data state.** `windows_exporter` cannot run in a Linux container, so Windows PromQL would be strings that have never been executed against anything — which is worse than an honest refusal, because it looks like coverage. Expressed structurally: a template holds a mapping of OS family to query and there is no way to say "supported but empty", so an absent family *is* `unsupported`. The decision stays open; this is its working default.
2. **`DCP_PROMETHEUS_URL` does not pass through `app/core/ssrf.validate_registered_url`.** Reasoning in full in `app/metrics/client.py`'s module docstring: that function validates a URL arriving in an *API request*, §19.4's denied-range check would reject the only addresses an internal Prometheus can have (RFC1918, or a Docker service name), and gating it would force `DCP_SSRF_HOST_ALLOWLIST=prometheus` as a shipped default — widening the **user-supplied** surface (§18.2 health-check URLs) in order to permit an operator-supplied destination, which is PROGRESS issue 45 in reverse. The rest of [SECURITY.md §19](SECURITY.md#19-ssrf) is enforced: scheme, no credentials, no fragment and no whitespace at load time; https required outside `local`; redirects not followed; response size and timeout bounded.

**Also deliberately absent.** `degraded` on the §26.1 Prometheus row. It is the useful fourth state — "answering now, but not reliably" — and nothing this milestone keeps can decide it honestly, because a successful probe resets the breaker's consecutive-failure count and any flap verdict built on that count is therefore always false. Saying it properly needs failure-rate history over a window, which is a threshold nobody has asked for. The row carries both timestamps and the previous failure's reason instead, so an operator can see that an `ok` Prometheus failed a minute ago and judge for themselves.

---

## M2.2 — Host metrics endpoints and charts

**Status: complete (2026-10-16).** Evidence beside each item. Four decisions were closed before any code, with the user's agreement: thresholds are the catalogue defaults only (`OPEN-28`); `stale` is a sample-age bound below Prometheus's lookback; an environment comparison is one series per server and refuses over 20 rather than truncating; the dashboard's `utilisation` section was **not** flipped.

**Tasks**

- [x] Endpoints §15.1–§15.3. `backend/app/metrics/{readings,router,schemas}.py`. A reading is a value or a reason (`not_configured` / `unsupported` / `unavailable` / `stale` stay distinct); §15.3 is allowed in read-only mode because it only reads. Contract text and the divergences it forced are in [API_CONTRACTS.md §15](API_CONTRACTS.md#15-server-metrics).
- [x] Threshold evaluation. `backend/app/metrics/thresholds.py`: `>=` crosses, `None` classifies to `None`, never `normal`. Catalogue defaults only; configurable thresholds are `OPEN-28`.
- [x] Frontend: metric cards, range charts with selectable ranges, server and environment comparison. `ServerMetrics.tsx`, `Compare.tsx`, `components/chart/LineChart.tsx` (+ `ticks.ts`, `format.ts`), eight `--series-*` tokens. No dependency added.
- [x] Verified against a real Prometheus and `node_exporter`: `scripts/smoke-test.sh` step 18 now also asserts that `timestamp(up{…})` returns the **sample's** time (6.7 s before evaluation — if it returned evaluation time `stale` could never fire), runs `readings.current` and `readings.history` end to end, and gets a real series with 40 leading holes out of 41 points on a cold stack, none zero-filled. The pages were then looked at in a real browser against the running stack.

**Acceptance: met.** Metrics are attributed by `metrics_instance` through an equality (single) or anchored alternation (comparison) selector, and a comparison maps each series back by its `instance` label; the window and a UTC timestamp are visible on every card and chart; gaps are `null` points at their own timestamps and are drawn as gaps; history is bounded by the same 30 days Prometheus retains (`--storage.tsdb.retention.time=30d`).

**Tests:** 43 new backend unit tests (`test_thresholds`, `test_readings`), 35 new integration tests (`test_metrics_values`), 43 new frontend tests (12 `LineChart`, 11 `ServerMetrics`, 8 `Compare`, 10 `range`/`formatValue`, 2 on `ServerDetail`'s link). The scope tests were mutation-checked: removing the single-server scope check fails two, removing the comparison scope predicate fails two, and `>=`→`>` fails the boundary case. See [PROGRESS.md §7](../PROGRESS.md#7-tests-executed-and-their-actual-results).

**Security: met.** Scope is `metrics:read` against the server target; out-of-scope reads as `404` indistinguishable from an unknown id; a comparison drops out-of-scope servers and reports `excluded_count` (never disclosing a count for an environment selection); no endpoint accepts PromQL and no response carries rendered PromQL.

**Known limitations, recorded rather than hidden:** `no_data` cannot tell "nothing scraped" from "label matches no target" until M2.3; `stale` is only ever reached by a lagging Prometheus, host-level staleness being M2.5; an environment that resolves to more than 20 servers is refused rather than aggregated; no E2E path (§12 path 3) yet.

**Original scope (kept for reference).**

**Tasks:** endpoints 15.1–15.3; threshold evaluation; frontend metric cards, range charts with selectable ranges, and server/environment comparison.

**Acceptance:** metrics attributed to the correct server (PRD §4.4 acceptance criterion); the displayed time range and timestamp are visible; **gaps render as gaps**; history respects retention.

**Tests:** *Unit* — threshold classification, including boundary values. *Integration* — instant and range queries against seeded series; a server with no series returns `unavailable`. *Frontend* — a series containing `null` renders a gap, asserted explicitly (the single most likely way to violate PRD §4.4).

**Security:** per-server scope enforced on metric reads; comparison endpoint drops out-of-scope servers and reports `excluded_count`.

**Risks:** chart library coerces `null` to `0`. **Mitigated ahead of the milestone (2026-10-16), and the mitigation is a test rather than a hope.** `TS-01` was resolved to `d3-shape` + `d3-scale` used directly, so the gap rule is one function of ours (`linePath`, using `line().defined()`) rather than a library default that a minor release could change — which is precisely how Recharts would have held it, through `connectNulls` continuing to default to `false`. `frontend/src/components/chart/linePath.test.ts` asserts the rendered path geometry and catches **both** failure modes: coercion (continuous line, a vertex gained on the baseline) and bridging (continuous line, a vertex lost). Removing `.defined()` as a mutation fails 6 of its 12 tests. What M2.2 still owes is the *frontend* half of its own test list — a series containing `null` rendered through an actual chart component — since `linePath` is geometry and not yet a component.

---

## M2.3 — Target discovery and agent health

**Status: complete (2026-10-16).** Evidence beside each item. Five decisions were closed with the user before any code: `OPEN-14` resolved to a DCP-generated `file_sd` with **no reload call**; what a target may point at is a policy; registration-time verification became **asynchronous**; the probe reads Prometheus's own `up` and owns `last_seen_at`; and the static dev target was removed in favour of registering it. One more was found *while* designing and taken without a round trip because the alternative was a broken contract: migration 0005 (see below).

**Tasks**

- [x] Generate the Prometheus target file from inventory (`OPEN-14`). `backend/app/metrics/discovery.py`: one predicate selects targets for both the file and the probe; the file is deterministic, atomic and unchanged-skipping, holds only `host:port`, `environment` and `server_id`, and is written under a Postgres advisory lock so an older snapshot cannot overwrite a newer one. **No reload is requested anywhere**; `--web.enable-lifecycle` was removed from the Prometheus container.
- [x] Target policy. Port allowlist (`DCP_METRICS_TARGET_PORTS`, default `9100`), refused loopback / link-local (including the metadata endpoint **and its IPv4-mapped IPv6 spelling**) / unspecified / multicast / reserved addresses, and the `.invalid` TLD — applied at the write boundary *and* again when the file is built.
- [x] Agent-health probe and `agent_status` / `last_seen_at` maintenance. `backend/app/metrics/agents.py` + `app/workers/tasks/monitoring.py`: `HEALTHY` / `UNREACHABLE` / `UNKNOWN` from `max_over_time(up[window])`; `DEGRADED` and `NOT_INSTALLED` deliberately not emitted. When Prometheus cannot be asked every monitored server becomes `UNKNOWN` — a status never outlives its evidence.
- [x] Triggers. After every inventory write that can change a target (register, retire, and a PATCH touching `metrics_instance`, `monitoring_enabled`, `environment` or `status`), best effort and argument-free; a 5-minute beat heals a missed enqueue; the probe runs every `DCP_AGENT_PROBE_INTERVAL_SECONDS` (30).
- [x] **Migration 0005 — `servers.updated_at` ignores observations.** Found while designing the probe: the generic `updated_at` trigger fires on every UPDATE, so a probe writing every 30 s would have made every `If-Unmodified-Since` edit a spurious `409`. The new trigger advances `updated_at` only when something other than `agent_status` / `last_seen_at` changed. Asserted end to end, and the test fails if the trigger ignores nothing.
- [x] Frontend: the server detail page shows the agent as a word and says *why* it is unknown (monitoring disabled / no metrics instance / waiting for the first scrape).
- [ ] Alerting on exporter absence — rules land in M3.3, as the plan said.

**Acceptance: met, against real systems.** `scripts/smoke-test.sh` step 18 now registers the bundled `node-exporter` **through the API** (it is no longer in `prometheus.yml`), watches the target file list it, waits for `HEALTHY` with a real `last_seen_at`, asserts the seeded `[DEMO]` servers stay `UNKNOWN`, **stops the exporter and waits for `UNREACHABLE`**, and starts it again and waits for `HEALTHY`. The intervals are tightened for that run (10 s probe, 30 s window), and the step asserts the worker actually *sees* them, which also proves the Compose passthrough works.

**Tests:** 47 new backend unit tests (`test_discovery`), 24 new integration tests (`test_monitoring`), 6 new frontend tests. Mutation-checked: the trigger ignoring nothing fails 2, removing the reserved-domain rule fails 2, and defaulting unobserved hosts to `HEALTHY` fails 1.

**Security: met.** The file holds no secret and no free text (asserted with hostile `display_name` and `owner_team`); the lifecycle API is not enabled, so there is no reload endpoint to reach; the file is written only by the worker and mounted read-only into Prometheus; a rejected target is never echoed. **Limitation, stated:** a DNS name can still resolve somewhere the policy would refuse, and Prometheus resolves it at scrape time — the real control is the monitoring network's egress policy (`OPEN-12`).

**Known limitations, recorded rather than hidden:** the shared volume is a single-host arrangement (`OPEN-12`); the probe flips every monitored server to `UNKNOWN` during a Prometheus outage; `DEGRADED` and `NOT_INSTALLED` are never emitted; the Windows exporter is out of scope (`OPEN-09`); no E2E path.

**Original scope (kept for reference).**

**Tasks:** generate the Prometheus target file from inventory (OPEN-14) and trigger a reload; connectivity/agent-health probe task; `agent_status` maintenance; alerting on exporter absence (rules land in M3.3).

**Acceptance:** registering a server with monitoring enabled results in a scraped target without hand-editing Prometheus config; a stopped exporter sets `agent_status` to `UNREACHABLE` within the configured interval.

**Security:** the generated file contains no secret; the reload endpoint/signal is not reachable from the browser; the file writer cannot be influenced by user-supplied content beyond validated inventory fields.

---

## M2.4 — systemd service monitoring

**Status: complete (2026-10-17).** Evidence in [`PROGRESS.md`](../PROGRESS.md) §3 and §7. Two things the plan below says were done differently, each for a reason found by testing: **"stop the exporter ⇒ `UNCOLLECTED`" is not observable** with the collector inside `node_exporter` (a stopped exporter is an unreachable host, so `UNKNOWN`; `UNCOLLECTED` is the collector failing, produced by stopping the system bus — [TESTING.md §11](TESTING.md#11-docker-compose-smoke-tests)), and **`sub_state`, `load_state`, enabled state and main PID are `null`** because no exporter exposes them (`OPEN-29`). Endpoint 16.7 is routed and validated but answers `503 not_configured` until Loki (M3.1). Registration, edit and deregistration (16.3, 16.5, 16.6) exist on the backend with no screen yet (PROGRESS issue 72).

**Tasks:** enable `node_exporter`'s systemd collector (`TS-02`) with a host-configured unit allowlist (DCP also filters on read to registered services), plus a systemd-running container as the integration fixture (the dev `node-exporter` has no system bus); state **normalisation** per [ARCHITECTURE.md §6.1](ARCHITECTURE.md#61-monitoring-read); endpoints 16.1–16.7 (reads); `services.last_observed_*` refresh job; dashboard failed-services panel; frontend service list and detail.

**Acceptance (all PRD §4.5 criteria):** a failed service is identifiable without logging in; state is always shown with its collection timestamp; **an unreachable server does not render as all-services-inactive**; `UNSUPPORTED`, `UNCOLLECTED` and `FAILED` are distinct.

**Tests:** *Unit* — the normalisation table, case by case, including host-stale overriding unit state and the Windows `UNSUPPORTED` path. *Integration* — stop a unit in a container and observe `FAILED`; stop the exporter and observe `UNCOLLECTED`; stop the host and observe `UNKNOWN` for all units. *Frontend* — the unreachable-server case renders "unknown", not "inactive".

**Security:** unit state is read from metrics, not by executing anything on the host; log tail requires `logs:read:system`.

**Risks:** scraping every unit explodes cardinality. Mitigation: exporter-side allowlist aligned with registered services.

---

## M2.5 — Stale-data detection

**Status: complete locally (2026-10-17); not yet pushed or run on hosted CI.** Evidence in [`PROGRESS.md`](../PROGRESS.md) §3, §6 (issues 88–90) and §9. What the plan below says was done differently: health is computed from `last_seen_at` alone (the probe already folds `up` and sample recency into it); thresholds are one `DCP_HEALTH_THRESHOLDS` JSON setting rather than `system_settings` (which accepts one key); ages are measured against the last time Prometheus answered, not the wall clock (a Prometheus outage must not look like a fleet outage); the "frozen clock" integration test became a real silent host and a real Prometheus outage in smoke step 18a; and the work found and fixed a circuit-breaker defect that stopped the worker recovering from that outage (issue 88). Decisions: `OPEN-30` in [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions).

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

**Status: complete locally (2026-10-18); not pushed, not on hosted CI.** Evidence beside each item. Closed before any code, at the owner's instruction to take the recommended option: `OPEN-03`, `OPEN-08`, `OPEN-13`, `OPEN-16`, `OPEN-20`, `OPEN-28`; raised and decided: `AD-21`, `AD-22`, `OPEN-31`.

**Tasks**

- [x] Loki config with limits and retention. `infra/loki/loki-config.yaml`: 14 d retention with the compactor on, at most seven label names, no auto-added `service_name`, 7-day old-sample rejection, ingestion and stream limits, usage reporting off. Asserted by smoke step 18c.
- [x] Sample log producers. `infra/compose/sample-logs/` (`produce.py`, `canaries.json`), Compose `sample-logs` in the `logs` profile reusing the backend development image. `backend/tests/test_sample_logs.py` (6) asserts every canary is emitted — it failed on its first run.
- [x] Alloy config for Nginx, PHP-FPM, syslog, application and Docker sources (`infra/alloy/config.alloy`) and journald (`journal.alloy`, separate so a host without a journal still reports ready). Admin port on loopback and unpublished, non-root, reporting off.
- [x] The bounded label set (`AD-21`): `stage.label_keep` last; `level` and `service` collapsed to closed sets; a line with a missing or malformed identity is dropped. Smoke step 18d, including four fail-closed cases asserted by drop counter.
- [x] Redaction stages: six RE2 `stage.replace` ports of `app/core/redaction.py`. `backend/tests/test_canary_corpus.py` (27) ties both layers to one corpus; smoke 18d proves it through a real Alloy into a real Loki. **Limit, pinned by a test:** a multi-line PEM key is not redacted.
- [x] Journald against a real journal. Smoke step 18e (`SMOKE_SYSTEMD`-gated: NOT RUN where the fixture cannot start).
- [~] Docker source against a real daemon: **run once by hand, not in the smoke test.** It works as uid 0 (read-only, no caps) and silently reads nothing as a non-root user — see `AD-22`.

**Acceptance:** logs from a sample server are queryable in Loki with exactly the allowed labels; a seeded secret pattern is redacted before storage. **Met for the sample server**, and not for a registered one: nothing renders Alloy config per server from inventory yet, and the sample identity is not a registered server.

**Tests:** *Integration* — produce a log line, assert it appears with expected labels; assert a high-cardinality label is dropped; assert a canary secret does not reach Loki.

**Security:** label allowlist enforced; outbound push only; redaction tested rather than assumed, with its best-effort limitation documented in [SECURITY.md §17](SECURITY.md#17-sensitive-log-redaction).

---

## M3.2 — Log search API and UI

**Status: complete locally (2026-10-18); not pushed, not on hosted CI.** Decisions `OPEN-32` – `OPEN-38` closed before code (see [ARCHITECTURE.md §19](ARCHITECTURE.md)); evidence in [`PROGRESS.md`](../PROGRESS.md) §9. §19.3 and deployment-to-logs navigation move to M5 (they need Jenkins and a deployments page); everything else below was built.

**Tasks:** Loki client with timeouts and circuit breaker; **server-side LogQL construction** from structured filters; pattern-preset catalogue; endpoints 19.1–19.3; cursor pagination; outbound redaction; frontend log search with filters, result list, source/timestamp display, and navigation from a failed service or a deployment to its logs.

**Acceptance (PRD §4.7 criteria):** an authorised user searches across several servers; results show source and timestamp; unauthorised users cannot query restricted environments; limits and retention are respected. Loki down renders an explicit unavailable state — **never "no logs found"**, which during an incident is a dangerous lie.

**Tests:** *Unit* — selector construction for every filter combination; `contains` escaping; limit/range guard arithmetic. *Integration* — query seeded logs; scope intersection drops unreadable servers and reports `excluded_by_scope_count`; Loki outage path. *Frontend* — filters, pagination, and the unavailable banner.

**Security:** no raw LogQL accepted (injection suite); `logs:read:system` vs `logs:read:application` separation tested; log lines rendered as text (XSS suite with markup in a log line); filters are not in a URL or access log (`POST`, OPEN-20).

---

## M3.3 — Alert rules and lifecycle

**Status: complete locally (2026-10-18); not pushed, not on hosted CI.** Decisions `OPEN-39` – `OPEN-47` closed before code (see [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions)); evidence in [`PROGRESS.md`](../PROGRESS.md) §9. Five of PRD §4.8's eight conditions are produced; failed application health check, failed deployment and operation timeout have **no producer yet** and arrive with M5, M5 and M4 (`OPEN-40`) — the alert list says so rather than implying they are watched.

**Tasks**

- [x] Prometheus rule file for server stopped reporting, CPU, memory, disk and failed unit: **generated** by `scripts/gen_alert_rules.py` from the metric catalogue's PromQL and `DCP_UTILISATION_THRESHOLDS` / `DCP_HEALTH_THRESHOLDS` (`OPEN-46`), committed at `infra/prometheus/rules/dcp-alerts.yml`, kept current by `check-all.sh`, and run against seeded series by `promtool test rules`.
- [x] Alertmanager in Compose (`observability` profile, unpublished), routing to the webhook only; the always-firing watchdog proves the pipeline alive (`OPEN-47`).
- [x] The webhook receiver (§20.5): shared secret in constant time, peer-address allowlist, rate limit, strict schema with length caps, per-alert rejection inside a `202`, one transaction per alert, refusal counters, audit as `system:alertmanager`.
- [x] `alert_events` (monthly partitions) and `alert_open_keys` — the one-open-row-per-`dedup_key` invariant as a database constraint, because the documented partitioned unique index could not enforce it (`OPEN-42`); `DCP_SYSTEM` source; the `EXPIRED` task; the partition-runway monitor wired to alerting.
- [x] Endpoints 20.1 – 20.5, scoped in SQL through the server an alert is about.
- [x] Frontend alert list, detail, acknowledge, "assign to me" / unassign; the dashboard's `active_alerts`, per-row counts and the server page's `open_alerts` read real rows.
- [ ] *Deferred, with reasons:* an assignee picker (needs a user directory an operator may not read, `OPEN-44`); alerts on the server and service pages (the API's `target_id` filter and `links` are there); the 180-day partition drop, which is not built for `audit_events` either (PROGRESS issue 107).

**Acceptance (PRD §4.8 criteria):** a configured condition produces an alert event — **met against a real Prometheus and Alertmanager** (smoke 18g); repeats are grouped and deduplicated — met; resolved alerts retain history — met; **acknowledgement does not mark the underlying problem resolved** — met, and enforced by a schema CHECK as well as the code.

**Tests:** *Unit* — `dedup_key` stability and separator-injection; the status-transition table; label→target parsing; the payload as untrusted input; the generated rules. *Integration* — new, repeat, resolved, malformed, unmatched, a batch with one bad entry, concurrent ingestion (a deterministic race test and a load test), out-of-order resolved/firing, every refusal counted, the scope matrix, paging with ties, every illegal transition, rule evaluation with `promtool`. *Frontend* — every state of both pages, markup in a summary, every error. *Smoke 18g* — the real chain, a stop, an alert, a recovery and an Alertmanager outage.

**Security:** shared secret, source restriction and rate limiting enforced; **an explicit adversarial test shows a crafted alert cannot change a server, a service, a grant or the audit trail's action types, and triggers no outbound call** ([SECURITY.md §21](SECURITY.md#21-alert-webhook-ingress)); label and annotation lengths capped.

**Risks:** an alert storm floods rows or notifications — dedup plus a rate limit and a per-request cap, with the overflow returned per alert, counted and logged. A `for:` duration is a working default, not a measurement.

---

## M3.4 — Notifications

**Status: complete locally (2026-10-19); not pushed, not on hosted CI.** Decisions `OPEN-48` - `OPEN-57` closed before code, at the owner's instruction to take the recommended option (see [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions) and [`M3.4_PLAN.md`](M3.4_PLAN.md)); evidence in [`PROGRESS.md`](../PROGRESS.md) §9. Built: the secret resolver (`env://`), the request-time half of SECURITY.md §19, `notification_channels` / `notification_deliveries` as a transactional outbox, the delivery worker with lease, bounded retry and abandonment, SMTP and HTTPS senders, §26.8 - §26.10 and §20.6, the §26.1 rows, the channels page and the deliveries on an alert. **Not built (by decision):** resolved / acknowledged / expired notices, a send-test route, channel delete, AWS / Azure secret backends, webhook signing, per-channel rate limits. **Not verified:** a real Slack workspace, Teams tenant or mail provider (only the local sink was exercised); the hosted CI run.

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

**Status: complete locally (2026-10-19), on branch `phase-M4.1`; not pushed, not on hosted CI; the Compose smoke test passed locally from empty volumes.** Plan: [`M4.1_PLAN.md`](M4.1_PLAN.md). Decisions `OPEN-58` - `OPEN-66` (and `OPEN-05`) closed before code at the owner's instruction to take the recommended option, see [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions). Built: `app/providers/` (types, error taxonomy and API mapping, read/write retry table, three-valued capability sets, the ABC with a final gated `execute_operation`), `DemoAdapter`, an empty fail-closed registry, §26.7, `scripts/check_provider_branching.py` in `check-all.sh`, smoke step 17b (passed). **Adjustments to the tasks below:** no §14.8 (moves to M4.3); the "full operation lifecycle" is the adapter lifecycle, the state-machine one is M4.2; no `AgentAdapter`; no frontend page; "no provider branching" means no *behavioural* branching, with `inventory/schemas.py` a recorded, allowlisted exception ([`PROGRESS.md`](../PROGRESS.md) issue 127). **Not verified:** any real provider (none exists), hosted CI. Evidence: [`PROGRESS.md`](../PROGRESS.md) §7 and §9.

**Tasks:** the `ProviderAdapter` interface, result envelope and error taxonomy; capability sets; adapter registry by `cloud_accounts.provider`; `DemoAdapter`; `GET /cloud-accounts/{id}/capabilities`; `demo_mode` propagation.

**Acceptance:** no provider-specific branching exists above the adapter layer (reviewed, and grep-checked for provider names outside `providers/`); demonstration mode returns `not_configured` for writes and marks every affected response.

**Tests:** *Unit* — error mapping for each class; capability gating returning `UnsupportedOperation`. *Integration* — a full operation lifecycle against `DemoAdapter`.

**Security:** the interface exposes **no** command/script/argv parameter — enforced by the signature itself, so a shell path has nowhere to exist.

---

## M4.2 — Operation lifecycle and the state machine

**Status: complete locally (2026-10-19), on branch `phase-M4.2`; not pushed, not on hosted CI; the Compose smoke test was not run.** Built: `app/operations/` (transition table, evaluator, codes, guarded-update builder), migration `0010`, endpoints 17.1 - 17.3, the in-flight guard on deregister / retire, the executor, reaper and healer, the Actions panel, confirmation dialog and operation page. **Proven against a test fake only: in the shipped application no operation can be created until M4.3.** Evidence: [`PROGRESS.md`](../PROGRESS.md) §7 and §9. Plan: [`M4.2_PLAN.md`](M4.2_PLAN.md). Decisions `OPEN-67` - `OPEN-77` closed before code at the owner's instruction to take the recommended option, see [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions). **Adjustments to the tasks below:** endpoints 22.1 - 22.4 move to M4.5 (the `approvals` table does not exist before it); §17.1 accepts `start | stop | restart` only; an operation that needs approval is **refused (`503 not_configured`), not queued**, until M4.5; `VERIFYING` is never stored and two reaper-only `→ TIMED_OUT` edges are added; there is no resume-from-handle (M4.3); `parent_operation_id` is created but nothing sets it (no retry trigger); in the shipped application §17.1 cannot create an operation until M4.3, because no adapter is registered and the demo adapter refuses writes. The lifecycle is proven against the test-only `ScriptedAdapter`.

**Tasks:** the state machine with guarded transitions and illegal-transition errors; `deadline_at`; the timeout reaper; `parent_operation_id` retries (column only, see above); endpoints 17.1–17.3 (22.1–22.4 moved to M4.5); the one-in-flight partial unique index in use; idempotency handling; Celery `operations` queue with `acks_late` and a visibility timeout longer than the deadline; worker-side re-authorisation and target re-validation; frontend confirmation dialog, progress polling and result display.

**Acceptance (PRD §4.6 criteria):** a user without permission cannot start/stop/restart; an approved restart creates an operation record; the UI shows the execution result **and the verified service state**; failed operations preserve redacted diagnostics; duplicate requests do not execute twice.

**Tests:** *Unit* — every legal transition and a representative set of illegal ones; deadline computation; idempotency key resolution. *Integration* — happy path to `SUCCEEDED` with `observed_state_after`; verification mismatch ⇒ `FAILED`; provider timeout ⇒ `TIMED_OUT`; concurrent duplicate submissions produce exactly one execution; two workers racing one row produce exactly one claim; worker killed mid-operation ⇒ reaper times it out and does **not** re-invoke; permission revoked while queued ⇒ the worker refuses.

**Security:** the full allowlist gate in order; closed action enum; `403` audited; no field anywhere accepts a command; worker re-check proven by revoking permission between request and execution.

**Risks:** the worst failure mode in this project is a *wrongly reported* success. Mitigation: verification required by the state machine **and** by a database `CHECK`; failing that test blocks release.

---

## M4.3 — AWS SSM adapter

**Status: complete locally (2026-10-20), on branch `phase-M4.3`; not pushed, not on hosted CI; the Compose smoke test was not run. Built and proven against a STUBBED AWS layer only: it has never been run against AWS, so the acceptance criterion below is not met by the build and needs the owner's manual check ([`infra/aws/README.md`](../infra/aws/README.md)).** Plan: [`M4.3_PLAN.md`](M4.3_PLAN.md) (with its "what differed" list). Decisions `OPEN-78` - `OPEN-91` closed before code at the owner's instruction to take the recommended option, see [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions). Built: `app/providers/aws/`, `infra/aws/`, three interface amendments, resume from the persisted handle (migration `0011`), the region boundary, §14.8, §26.2 - §26.6, per-account §26.1 rows, the operation-failure alert and a *Check connectivity* button. **Adjustments to the tasks below:** the work includes what M4.1 / M4.2 handed over (§14.8, §26.2 - §26.6, resume, the failure alert); inventory sync *reconciles registered servers and never registers one* (`OPEN-88`); the execution role's deny of the shell documents is verified *offline*, not at runtime (`OPEN-87`); no cloud-accounts page.

**Tasks:** `boto3` client with role-based credentials and adaptive retries; invoke the **registered document** with typed parameters; persist `CommandId` **before** polling; poll with backoff; verification read; throttling honoured via `retry_after`; `DescribeInstanceInformation` connectivity; inventory sync; error-class mapping.

**Acceptance:** a restart on a non-production test instance succeeds and is verified; a throttled response backs off instead of hammering; a permission error surfaces as a configuration problem with actionable text.

**Tests:** *Unit* — parameter construction; error mapping; backoff. *Integration* — against a mocked AWS layer for the full matrix (success, throttle, auth error, not-found, timeout, partial failure). *Manual, documented, non-production only* — one real instance test, with its result recorded in `PROGRESS.md` as a manual check rather than an automated pass.

**Security:** the execution role is verified to **deny** `AWS-RunShellScript` and `AWS-RunPowerShellScript`; targeting restricted by tag; no static keys present; the document itself reviewed for parameter injection (a service-name parameter must not be interpolable into a shell line).

**Risks:** the SSM document is the one place a shell line genuinely exists. Mitigation: treat it as security-critical code — reviewed, version-controlled, parameter-validated on both sides.

---

## M4.4 — Azure adapter

**Status: DEFERRED (2026-10-20, by the owner: there is no Azure estate to manage; `OPEN-92`).** Nothing below is built, and nothing later in the plan depends on it. The plan text is kept unchanged so the milestone can be picked up if an Azure estate appears. PRD §4.11's Azure requirements are therefore **not met by this release** and `AZURE` accounts are refused at registration (`no_adapter_for_provider`). Consequences recorded in [`PROGRESS.md`](../PROGRESS.md) issue 159.

**Tasks:** managed-identity credential; ARM inventory; Run Command with a registered script; Azure Arc for supported external servers; **model Azure's per-VM serialisation and async semantics explicitly**; capability set reflecting real differences.

**Acceptance:** unsupported operations are reported explicitly as unsupported, never as a generic failure (PRD §4.11 acceptance criterion); Azure-specific limits are visible in the capability matrix.

**Tests:** as M4.3 against a mocked Azure layer, plus a concurrency test asserting that a second Run Command on the same VM is handled as a provider-level conflict rather than a DCP bug.

**Security:** role assignment scoped to a resource group; no wildcard actions; no `Microsoft.Authorization/*`.

**Risks:** assuming AWS semantics. Mitigation: the capability matrix is a contract, and a test asserts the adapters disagree where they genuinely differ.

---

## M4.5 — Approval workflow

**Status: complete locally (2026-10-20), on branch `phase-M4.5`; not pushed, not on hosted CI; the Compose smoke test was not run; never run against AWS.** Plan: [`M4.5_PLAN.md`](M4.5_PLAN.md); decisions `OPEN-93` – `OPEN-105` closed before code at the owner's instruction. **Adjustments to the tasks below:** one approval only (a policy needing more is refused at request time); approver notification is a pending-approval alert through the existing channels, not a new subsystem; the worker's re-check reads an `approvals` row, not the flag; **an Administrator editor for approval policies is in this milestone** (`OPEN-105`, owner's instruction), where the plan had proposed leaving it out. Like all of Phase 4 it is proven against stubbed providers only until the owner's manual AWS check ([`PROGRESS.md`](../PROGRESS.md) issue 146). M4.4 (Azure) is deferred (`OPEN-92`) and nothing here depends on it.

**Tasks:** policy evaluation at request time and re-check at execution; approval creation, decision endpoints, expiry sweep; **self-approval denial**; approver queue UI with the `self_requested` flag; approval notifications; audit with approval references.

**Acceptance:** a production operation under a policy cannot execute without an approval; a requester cannot approve their own request unless the policy explicitly allows it (PRD §3.6); an expired approval window cancels rather than lingering as live production intent.

**Tests:** *Unit* — policy evaluation matrix (environment × action × policy); self-approval decision. *Integration* — approve→execute; reject→no execution; expire→cancel; approve twice ⇒ `409`; **direct execution attempt bypassing approval ⇒ refused by the worker** (the adversarial test for mandatory rule 6).

**Security:** approval bypass suite; policy missing or unreadable ⇒ deny; approver scope enforced.

---

## M4.6 — Audit API and operations history

**Status: complete locally (2026-10-23), on branch `phase-M4.6` (seven commits); not pushed, not on hosted CI; the Compose smoke test and Playwright were not run and the new pages were not seen in a browser.** Plan: [`M4.6_PLAN.md`](M4.6_PLAN.md); decisions `OPEN-106` – `OPEN-117` closed before code at the owner's agreement. **Adjustments to the tasks below:** the export needs somewhere to live and a way to be fetched, so endpoints **24.4** (status) and **24.5** (download once) are added and a table, `audit_exports` (migration `0013`), holds the file; the operations history is scoped on the **stored** environment, which also fixes PROGRESS issue 164 in M4.2's visibility check; an audit event is scoped on its environment alone, so a grant on a server or account reaches no audit row and an event with no environment is Administrator-only; the dashboard's `recent_operations` is flipped (issue 143). "Immutability under the app role" is shown for the triggers on the role the application uses — a least-privilege role is M6.2. Like all of Phase 4 the history shows what stubbed providers and one sandbox run produced.

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

**Status: complete (2026-10-23), merged to `main` as PR #35 and green on hosted CI per the owner (the Compose smoke test with step 18i, and Playwright, included); proven against a mock Jenkins only.** Plan: [`M5.1_PLAN.md`](M5.1_PLAN.md); decisions `OPEN-18` and `OPEN-118` – `OPEN-131` closed before code at the owner's instruction to take the recommended option. **Adjustments to the tasks below:** Jenkins is one configured instance (`DCP_JENKINS_*`), not a registered one; the **`422 not_registered` acceptance criterion is met by `resolve_target` in M5.1 and becomes an HTTP response with §21.2 in M5.2** (no `deployments` table exists yet); the admin surface is endpoints 21.9 – 21.12 beside 21.8.

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
| ~~OPEN-03 alert rule authoring~~ | **Resolved 2026-10-17** — file-managed in git; numbers generated from `DCP_UTILISATION_THRESHOLDS` |
| OPEN-04 session durability | M1.3 |
| OPEN-05 restricted agent | M4.1, M6.1 |
| OPEN-06 secret store | M1.1 config |
| OPEN-07 RPO/RTO | M6.4 |
| ~~OPEN-08 retention~~ | **Resolved 2026-10-17** — Loki 14 d, Prometheus 30 d, no disk quota in v1 |
| OPEN-09 Windows scope | M2.4 |
| OPEN-10 approval policy | M4.5 |
| OPEN-11 teams model | M6.6 |
| OPEN-12 production topology | M6.5 |
| ~~OPEN-13 notification config~~ | **Resolved 2026-10-17** — DB tables, M3.4 |
| ~~OPEN-16 log classification~~ · ~~OPEN-20 `POST` for logs~~ · ~~OPEN-28 threshold home~~ | **Resolved 2026-10-17** — scoped permission only · `POST` only · per-environment `DCP_UTILISATION_THRESHOLDS` ([API_CONTRACTS.md §30](API_CONTRACTS.md#30-open-decisions)) |
| **OPEN-31 log-push provenance** (new in M3.1) | **Accepted for v1**; closed by a per-environment-tenant gateway before production rollout (Phase 6) |
| ~~OPEN-14 target discovery~~ | M2.3 — **resolved 2026-10-16**: DCP-generated `file_sd`, no reload call |
| OPEN-15 audit export | M6.2 |
| **OPEN-27 scoping a dimension-less resource** (new in M1.5) | **Not M1.6.** Corrected 2026-10-15 after M1.6 was built: §13.1 counts servers, failed services, alerts and recent activity — `application_id` appears only as a *filter*, and it resolves through `application_servers` to servers, which are already scope-filtered. The first milestone genuinely blocked is one that lists or counts applications in their own right. Working default in place: applications are not scope-filtered ([API_CONTRACTS.md §30](API_CONTRACTS.md#30-open-decisions)) |
| ~~TS-01 charting library~~ | ~~M2.2~~ — **resolved 2026-10-16**, before M2.2 began |
| TS-02 systemd exporter | M2.4 |

Full register: [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions).
