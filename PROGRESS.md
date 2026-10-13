# PROGRESS — DevOps Control Plane

**Purpose:** preserve development state between AI coding sessions. Read this first; update it last.
**Last updated:** 2026-10-13
**Updated by:** M1.1 implementation session

> **Rules for this file**
> - Record only what has **actually happened**. Never mark anything complete because a plan says it should be.
> - Record real test output, with the date it was run. "Should pass" is not a result.
> - Record integrations as *configured* only when a real connection has been made and verified.
> - Keep entries dated and append-only where practical, so history is not lost.

---

## 1. Current project stage

**Stage: Phase 1 — Foundation. Milestone M1.1 complete; M1.2 not started.**

The repository now contains a running application skeleton: FastAPI backend with
liveness/readiness endpoints, a React frontend, Celery worker and beat, a profiled
Docker Compose stack, and CI. It monitors nothing and authenticates nobody — see
[§4](#4-pending-milestones) for what each later milestone adds.

```
PRD.md  CLAUDE.md  PROGRESS.md  README.md
.env.example  .gitignore  .dockerignore  .pre-commit-config.yaml
.github/workflows/ci.yml
backend/     pyproject.toml, uv.lock, Dockerfile, app/{core,health,db,workers}, tests/
frontend/    package.json, package-lock.json, Dockerfile, vite/ts/eslint/prettier config, src/
infra/       compose/docker-compose.yml, prometheus/, loki/, alloy/
scripts/     check-all.sh, smoke-test.sh, check_doc_links.py, pin-digests.sh
docs/        eight design documents
```

There is still **no** database schema, no Alembic directory, no authentication, no
authorisation, no domain model and no integration of any kind.

Version control: branch `main` tracking `origin/main` on GitHub. Initial
documentation commit `d7dad3d`; `944558b` updated this file.

## 2. Completed milestones

| Milestone | Status | Date | Evidence |
|---|---|---|---|
| Documentation set drafted (8 documents + this file) | **Drafted, awaiting review** | 2026-10-13 | Committed as `d7dad3d`, pushed to `origin/main` |
| **M1.1** — repository skeleton, Compose core, health endpoints, CI | **Complete, with two caveats** | 2026-10-13 | All 13 local checks pass ([§7](#7-tests-executed-and-their-actual-results)); endpoints verified over real HTTP |

**M1.1 caveats — neither blocks M1.2, both are tracked in [§6](#6-known-issues):**

1. **The Docker Compose stack has never been started.** The Docker daemon was not
   running on the implementation machine. `docker compose config --profile core`
   validates (exit 0), but `docker compose up` and `scripts/smoke-test.sh` are
   **unexecuted**. The M1.1 acceptance criterion "every service starts healthy" is
   therefore **not verified**.
2. **Container images are pinned to exact tags, not digests**, which AD-16 requires.
   Resolving a digest needs a daemon. `scripts/pin-digests.sh` closes this; fake
   digests were not invented.

**No other implementation milestone has been started.**

---

## 3. In-progress milestone

**None.** M1.1 is finished; M1.2 (database schema and migrations) has not begun.

Outstanding decisions, in the order they bite:

- [ ] **`OPEN-02` — before M1.5.** `PRD.md` §4.9 lists `environment` as an application
      attribute, but §6's `applications` table omits it while `deployments` carries it.
      The documents assume applications are environment-agnostic (`AD-12`). This
      changes the schema and the applications API, so confirm it before M1.5.
- [ ] `OPEN-04` (session store: Redis-only vs a durable table) — before M1.3.
- [ ] `OPEN-01` (identity provider) — before M1.3 hardening; the mock IdP is enough to start.
- [ ] `TS-02` (systemd metrics source) and `TS-01` (charting library) — before M2.
- [ ] Run the Compose stack and `scripts/smoke-test.sh` on a machine with a working
      Docker daemon, then record the result here.

Decisions settled during M1.1:

- **`TS-03` → uv.** It was already installed on the implementation machine. `uv.lock` is
  committed and CI installs with `--frozen`.
- **`OPEN-06` → environment variables for now.** Credentials arrive as `DCP_*` variables
  injected by the platform, held as `SecretStr`. A pluggable secret-store resolver was
  deliberately **not** built: nothing in M1.1 has a provider to read from, and an
  interface with one unused implementation is scaffolding. It lands with the first real
  provider (M4.x). `OPEN-06` stays open for that choice.

## 4. Pending milestones

Per [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md). **All pending — none started.**

| Phase | Milestones | Status |
|---|---|---|
| 1 — Foundation | ~~M1.1 skeleton + Compose + CI~~ **done** · M1.2 schema + migrations · M1.3 OIDC auth · M1.4 RBAC · M1.5 inventory · M1.6 dashboard shell · M1.7 consolidation | **M1.2 next** |
| 2 — Monitoring | M2.1 Prometheus client · M2.2 host metrics + charts · M2.3 target discovery · M2.4 systemd monitoring · M2.5 stale detection | Pending |
| 3 — Logs and alerts | M3.1 Alloy + Loki · M3.2 log search · M3.3 alert rules + lifecycle · M3.4 notifications | Pending |
| 4 — Controlled operations | M4.1 adapter interface · M4.2 operation lifecycle · M4.3 AWS SSM · M4.4 Azure · M4.5 approvals · M4.6 audit API | Pending |
| 5 — Deployments | M5.1 targets + Jenkins client · M5.2 lifecycle · M5.3 build logs · M5.4 production approvals · M5.5 health checks + rollback | Pending |
| 6 — Hardening | M6.1 additional providers · M6.2 security verification · M6.3 performance · M6.4 backup/recovery · M6.5 production readiness · M6.6 final verification | Pending |

---

## 5. Architectural decisions already approved

**None are approved yet.** Every `AD-nn` in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §18 is **proposed, pending review**.

The decisions with the widest blast radius, listed so review can prioritise them:

| ID | Decision | Why it matters |
|---|---|---|
| AD-01 | Modular monolith + separate Celery workers | Shapes the whole repository layout |
| AD-02 | OIDC auth-code + PKCE, backend-for-frontend, opaque session cookie | Shapes auth, CSRF and the frontend's relationship to the API |
| AD-03 | RBAC resolved per request from PostgreSQL, fail-closed | Required for `PRD.md` §4.1's immediate-revocation criterion |
| AD-04 / AD-05 | Backend renders PromQL and LogQL from allowlisted templates; clients never send query strings | Closes injection and scope-bypass; limits ad-hoc querying |
| AD-07 | Provider writes only through pre-registered, parameter-constrained artifacts; no shell | The core security boundary of the product |
| AD-08 | Three-layer idempotency | Required for "duplicate requests do not execute twice" |
| AD-10 | Deployment success = pipeline success **and** health checks pass | Required by `PRD.md` §4.10 |
| AD-11 | `audit_events` append-only via database grants | Required by `PRD.md` §4.12 |
| AD-12 | Applications are environment-agnostic | **Resolves a PRD contradiction — needs product confirmation (`OPEN-02`)** |

Once a decision is reviewed, record the date and the outcome here.

**Exercised in code as of M1.1** (implemented, not formally approved):

| ID | Where |
|---|---|
| AD-01 modular monolith | `backend/app/` module layout; `create_app()` composition root |
| AD-15 demonstration mode | `DCP_DEMO_MODE`, rejected outside `local` by a config validator |
| AD-16 lockfile pinning | `uv.lock` + `package-lock.json` committed; CI uses `--frozen` / `npm ci`. **Image digests still outstanding** |
| AD-18 correlation IDs | `CorrelationIdMiddleware`; accepted, bounded, generated, echoed, bound into logs |
| Rule 7 never log secrets | redaction processor last in the logging chain, proven by canary tests |
| Rule 9 fail closed | config refuses to start on missing credentials or a typo'd `DCP_*` variable |
| docs/SECURITY.md §7 headers | `SecurityHeadersMiddleware`, asserted on success *and* error responses |
| docs/SECURITY.md §12 CORS | wildcard rejected in every environment at config load |
| docs/API_CONTRACTS.md §3/§8 | error envelope and structured validation errors, with the rejected value never echoed |

---

## 6. Known issues

### PRD contradictions and gaps found during documentation (2026-10-13)

| # | Finding | Handling |
|---|---|---|
| 1 | §4.9 lists `environment` as an application attribute; §6's `applications` table omits it while `deployments` has it, and §4.9 also requires one application to span many servers. | Assumed environment-agnostic (`AD-12`). **`OPEN-02` — needs product confirmation.** |
| 2 | §6 defines `services.action_policy_id` but no table for it to reference. | Added `service_action_policies` ([`docs/DATABASE.md`](docs/DATABASE.md) §4.6). |
| 3 | §6 defines `roles`, `permissions` and `user_roles` but no role↔permission link, so RBAC cannot be expressed. | Added `role_permissions`. |
| 4 | §4.6/§4.10/§9 require a "configured approval policy"; §6 defines only `approvals` (the decisions), not the policy. | Added `approval_policies`. |
| 5 | §4.10 requires that pipelines and deployment targets be restricted to registered configurations; §6 has no registry. | Added `deployment_targets`. |
| 6 | §4.12 requires `environment` and an approval reference on each audit event, and an "actor **or service identity**"; §6's `audit_events` has none of these. | Added `environment`, `approval_id`, `actor_type`, `actor_label`, `source_ip`, `user_agent`. |
| 7 | §4.8 requires deduplication, grouping and assignment; §6's `alert_events` has no dedup key or assignee. | Added `dedup_key`, `occurrence_count`, `assigned_to`, `environment`, `details`, `acknowledged_at`. |
| 8 | §4.8 requires notification failures to be "observable and retryable"; §6 has no notification entities. | Added `notification_channels` and `notification_deliveries` (Phase 3). `OPEN-13` records the alternative. |
| 9 | §4.3 lists server attributes (OS version, private IP, owner team, monitoring-agent status, registration timestamp, enable/disable monitoring) that §6's `servers` table omits. | Added seven columns, each marked in [`docs/DATABASE.md`](docs/DATABASE.md) §4.2. |
| 10 | §4.6 requires verifying the resulting service state, but the state list has no state in which verification happens. | Added internal `VERIFYING`, reported externally as `RUNNING` (`AD-09`), so the public enum matches the PRD exactly. |
| 11 | §3 names the read-only role "Read-Only User"; the roles to be documented were given as "Viewer". | Treated as the same role; `VIEWER` is the identifier, and both names are used interchangeably ([`docs/SECURITY.md`](docs/SECURITY.md) §3). |
| 12 | §4.5 requires systemd monitoring, and §4.4 requires Windows metrics — but systemd is Linux-only, so Windows service management has no defined scope. | Metrics only for Windows; unit state reports `UNSUPPORTED`. `OPEN-09`. |
| 13 | §9 requires "an approved secret store" while the product is explicitly multi-cloud, so the canonical store is undefined. | Pluggable interface, one configured backend per deployment. `OPEN-06`. |
| 14 | §10 requires recovery objectives "before production rollout" but sets none. | `OPEN-07`, blocking M6.4. |
| 15 | §6 lists `permissions.scope_definition` and `user_roles.scope_definition` without defining the shape. | Shape proposed in [`docs/DATABASE.md`](docs/DATABASE.md) §3.5, validated in application code. |

### Documentation issues

| # | Issue |
|---|---|
| 16 | 14 open decisions (`OPEN-01` … `OPEN-25`, see each document's open-decisions table) remain unresolved. Each has a working default, so Phase 1 is not blocked, but `OPEN-02` must be settled before M1.5. |
| 17 | `TS-01` (charting library) and `TS-02` (systemd metrics source) are unselected. `TS-01` has a functional gate: the library must render data gaps as gaps, not zeros. |
| 18 | No exact dependency versions are recorded anywhere, deliberately — versions belong in resolved lockfiles, not in prose. Nothing has been compatibility-tested. |
| 19 | The documentation set contains 236 relative links; all were verified to resolve to existing files and headings on 2026-10-13. Re-verify after any heading change — moving the `**[MANDATORY]**` labels out of `docs/SECURITY.md` headings during review was needed precisely because they altered 24 anchors. |

### Implementation issues (found during M1.1, 2026-10-13)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 20 | **The Compose stack has never been started.** No Docker daemon on the implementation machine. `docker compose --profile core config` validates (exit 0), but `up` is unexecuted, so "every service starts healthy" is **unverified**. `scripts/smoke-test.sh` has likewise never run. | Blocks the M1.7 smoke gate, not M1.2 | Run both on a machine with a daemon and record the result here. Expect to fix something. |
| 21 | **Images use exact tags, not digests**, contrary to AD-16. A digest cannot be resolved without a daemon, and inventing one would be fabricating a fact. | Reproducibility | `scripts/pin-digests.sh` resolves them; commit the result and close this entry. |
| 22 | **Native Windows without `--reload` cannot reach PostgreSQL.** uvicorn 0.54 picks its loop by platform: no `--reload` on Windows means `ProactorEventLoop`, which psycopg's async driver rejects, so `/readyz` reports `database: false` even with PostgreSQL up. Verified directly. | Local dev ergonomics only; production is Linux | Documented in `README.md`. An attempted `asyncio` policy fix was **removed** — uvicorn uses a loop *factory* and ignores the policy, so the code fixed nothing while looking like it did. |
| 23 | **Compose host ports moved off the defaults.** A PostgreSQL instance is already listening on 5432 on the implementation machine, so the original mapping would have collided on first `up`. | Avoided | PostgreSQL now publishes on host 5433 and Redis on 6380, both overridable. Container-to-container traffic is unchanged. |
| 24 | **`uv run pip-audit --strict` fails on the local project**, which is not on PyPI. | Avoided | `--strict` dropped from CI and `check-all.sh`, with the reason in a comment. Verified empirically that plain `pip-audit` still exits 1 on a real vulnerability (reproduced with pytest 8.4.2, 2 findings) and 0 when clean. |
| 25 | **One upstream deprecation warning remains** in the backend suite: starlette advises `httpx2` for `TestClient`. Left visible rather than silenced, as a migration signal. | Cosmetic | Revisit when `httpx2` is stable. |
| 27 | **Fixed 2026-10-13: no `.gitattributes`, so a Windows checkout produced CRLF and `npm run format:check` failed on 14 files** while Linux CI passed — a check that fails only on some machines. The shell scripts would have broken the same way on Linux (`bash: : command not found`). Found by running the checks from a fresh clone, not from the working tree. | Was blocking local verification | `.gitattributes` with `* text=auto eol=lf` added (`ef1de2b`); working tree renormalised. Committed blobs were already LF, so no file content changed. Re-verified from a fresh clone: LF checkout, all frontend and backend checks pass. |
| 26 | **The secret scanner, image scan and Compose smoke gate are unverified locally.** `pre-commit`, `gitleaks` and `trivy` are not installed on the implementation machine, so the M1.1 criterion "the scanner fails on a planted test secret" has **not** been demonstrated. The hooks and CI jobs are configured but unproven. | Verification gap | Run `pre-commit install && pre-commit run --all-files`, plant a test secret, confirm it fails, then record it here. |

---

## 7. Tests executed and their actual results

All results below were produced by running the commands on 2026-10-13 and reading
the output. Nothing here is inferred.

### Executed — `./scripts/check-all.sh`, 13/13 passed

| Check | Command | Result |
|---|---|---|
| Backend lint | `uv run ruff check .` | **pass** — "All checks passed!" |
| Backend format | `uv run ruff format --check .` | **pass** — 24 files formatted |
| Backend types | `uv run mypy` (strict) | **pass** — "no issues found in 17 source files" |
| Backend security lint | `uv run bandit -q -r app` | **pass** — no findings |
| Backend tests | `uv run pytest` | **77 passed**, 1 warning, 4.70s |
| Backend dependency audit | `uv run pip-audit` | **pass** — "No known vulnerabilities found" |
| Frontend lint | `npx eslint .` | **pass** |
| Frontend format | `npx prettier --check .` | **pass** |
| Frontend types | `npx tsc --noEmit` | **pass** |
| Frontend tests | `npx vitest run` | **13 passed** (2 files), 1.41s |
| Frontend build | `npm run build` | **pass** — 31 modules, 228 kB JS / 71 kB gzip |
| Frontend audit | `npm audit --audit-level=high` | **pass** — 0 vulnerabilities |
| Documentation links | `python scripts/check_doc_links.py .` | **pass** — 250 links, 12 files, 0 problems |

Backend tests by area: config 14, redaction 25, health 8, middleware 17, errors 13.
Frontend: `App` 7, `ThemeToggle` 6.

### Executed — verification beyond the suites

| Check | Method | Result |
|---|---|---|
| App serves over real HTTP | `uvicorn` on 127.0.0.1, `curl /healthz` | **200** `{"status":"ok"}` |
| Security headers on a live response | `curl -D -` | **present** — CSP, nosniff, DENY, no-referrer, Permissions-Policy, COOP, `X-Correlation-ID`, `X-Request-Duration-ms` |
| Honest degradation with dependencies down | `curl /readyz` with no PostgreSQL/Redis | **503** `{"status":"unavailable","checks":{"database":false,"broker":false,"migrations":false}}` — booleans only, no hostname, port, driver or error text |
| Liveness survives a dependency outage | `curl /healthz` in the same state | **200** |
| Credentials absent from logs | grep the server log for the DSN password | **0 occurrences** |
| Fail-fast on a missing credential | run with `DCP_DATABASE_URL` unset | **refuses to start**: "DCP_DATABASE_URL: Field required", "DCP_REDIS_URL: Field required" |
| Fail-fast on a typo'd variable | run with `DCP_DEMOMODE=true` | **refuses to start**: "Unrecognised DCP_* environment variable(s)" |
| Compose file validity | `docker compose --profile core config --quiet` | **exit 0** — parses and resolves without a daemon |
| `pip-audit` really does gate | temporarily pinned pytest 8.4.2 | **exit 1**, 2 PYSEC findings; exit 0 after reverting |

### Executed — post-merge verification from a clean clone (2026-10-13)

Run after the M1.1 merge (`f9a21ca`) by cloning the repository to a scratch
directory, because a working tree can pass checks that a fresh clone fails.

| Check | Result |
|---|---|
| Clone file inventory vs working tree | **identical** (68 tracked files) |
| `uv sync --frozen` | **pass** — `uv.lock` is consistent with `pyproject.toml`, which is what CI asserts |
| `npm ci` | **pass** — `package-lock.json` consistent with `package.json` |
| Backend ruff / format / mypy / bandit / pytest from the clone | **pass** — 77 tests |
| Frontend lint / typecheck / tests / build / audit from the clone | **pass** — 13 tests |
| Frontend `format:check` from the clone | **FAILED on 14 files** → root-caused and fixed, see issue 27 |
| Committed blobs are LF (so Linux CI is unaffected) | **confirmed** for every tracked text file |
| `.env.example` tracked despite the `.env.*` ignore rule | **confirmed** — the `!.env.example` negation works |
| Lockfiles committed; `.venv` / `node_modules` / `dist` ignored | **confirmed** |
| Re-verified from a second fresh clone after the fix | **LF checkout; all checks pass** |

### NOT executed — no result may be claimed

| Check | Why |
|---|---|
| `docker compose up` / `scripts/smoke-test.sh` | Docker daemon not running (issue 20) |
| Image build and Trivy scan | same, and `trivy` not installed |
| `pre-commit run --all-files`; planted-secret test | `pre-commit` and `gitleaks` not installed (issue 26) |
| The GitHub Actions workflow | never triggered; `.github/workflows/ci.yml` is unproven |
| Integration tests against real PostgreSQL/Redis | none exist yet (M1.2) |
| E2E tests | none exist yet (M1.7) |
| Performance tests | none exist yet (M6.3); **no performance figure has been measured** |
| Backup/recovery rehearsal | nothing to back up yet (M6.4) |

## 8. External integrations

**None is configured. None has been connected. None is operational.**

| Integration | Status | Notes |
|---|---|---|
| OIDC identity provider | **Not configured** | Provider not chosen (`OPEN-01`). Mock IdP planned for M1.3 |
| PostgreSQL | **Declared, never started** | Compose service defined (postgres 17.2-alpine, host port 5433). Never run — no daemon. No schema (M1.2) |
| Redis | **Declared, never started** | Compose service defined (redis 7.4.1-alpine, appendonly, noeviction, host port 6380). Never run |
| Prometheus | **Declared, never started** | Compose service + placeholder `infra/prometheus/prometheus.yml`. No rules, no real targets (M2.1) |
| Node Exporter | **Declared, never started** | In the `observability` profile as a sample scrape target (M2.1) |
| systemd exporter | **Not configured** | Source not selected (`TS-02`); M2.4 |
| windows_exporter | **Not configured** | Scope undecided (`OPEN-09`) |
| Grafana Alloy | **Declared, never started** | Compose `logs` profile + placeholder `infra/alloy/config.alloy` that collects nothing (M3.1) |
| Grafana Loki | **Declared, never started** | Compose service + minimal `infra/loki/loki-config.yaml`. No ingestion (M3.1) |
| Alertmanager | **Not configured** | M3.3 |
| Grafana | **Declared, never started** | In the `observability` profile; no dashboards |
| AWS Systems Manager | **Not configured** | No account, no role, no SSM document, no credential. M4.3 |
| Azure management APIs | **Not configured** | No subscription, no managed identity, no Run Command script. M4.4 |
| Jenkins | **Not configured** | No instance, no token, no registered target. Mock planned M5.1 |
| Secrets manager | **Not configured** | Backend not chosen (`OPEN-06`) |
| Notification channels | **Not configured** | M3.4 |
| Restricted agent | **Not designed** | Adopt-vs-build undecided (`OPEN-05`). M6.1 |

**No cloud resource has been provisioned. No production account has been contacted.
No credential has been created.** Every value in `.env.example` is a placeholder;
the only non-placeholder connection attempt made during M1.1 was a local psycopg
probe against `127.0.0.1:5432` using placeholder credentials, which failed
authentication as expected (see issue 23).

---

## 9. Next recommended task

**M1.2 — Database schema and migrations**
([`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md#m12--database-schema-and-migrations))

It is the natural next increment: M1.1 left a readiness probe that already reports a
`migrations` check, and M1.2 is what makes that check meaningful and promotes it into
`REQUIRED_CHECKS`. Every later milestone needs the schema.

Scope reminder from the plan: models for the Phase 1 tables, an Alembic initial
revision that includes the `CHECK` constraints, partial unique indexes, `audit_events`
monthly partitions and the `INSERT, SELECT`-only grant, a seed revision, partition
pre-creation on the `maintenance` queue, and migration tests against **real
PostgreSQL** (never SQLite — the partial indexes and grants are the point).

**Before starting:**

1. **Confirm `OPEN-02`** if you intend to continue past M1.2 into M1.5. It does not
   block M1.2's identity and inventory tables, but it decides whether `applications`
   carries an `environment` column, so settling it now avoids a rewrite.
2. Decide `OPEN-04` (durable session table vs Redis-only) — it determines whether
   M1.2 creates a `user_sessions` table.
3. Get a Docker daemon running and clear issues 20, 21 and 26: start the Compose
   stack, run `scripts/smoke-test.sh`, run `scripts/pin-digests.sh`, and install
   `pre-commit` to prove the secret scanner fails on a planted secret. M1.2's tests
   need containerised PostgreSQL anyway, so this is the cheapest moment to do it.

**Two specific code changes M1.2 must make**, both flagged in M1.1's source:

- `backend/app/health/checks.py` — add `"migrations"` to `REQUIRED_CHECKS` and replace
  `check_migrations()` ("is `alembic_version` present?") with a real head comparison.
  `tests/test_health.py::test_migrations_check_is_reported_but_not_yet_required`
  asserts the current state and must change with it.
- `backend/app/db/session.py` — add the declarative base and the per-request session
  dependency; only the engine and session factory exist today.

## 10. Scope confirmation

**Documentation session (earlier, 2026-10-13).** Documentation only: no code, no
migrations, no packages, no cloud resources, no credentials. Only `PRD.md` pre-existed
and it was read, not modified.

**M1.1 implementation session (2026-10-13).** Scope was milestone M1.1 and nothing
beyond it. Specifically **not** implemented, to avoid pulling later milestones forward:

- No database models, no declarative base, no Alembic directory, no migrations (M1.2).
- No authentication, session handling, CSRF enforcement or OIDC client (M1.3).
- No permission model, no `require_permission` dependency, no route-coverage test (M1.4).
- No domain routes: no servers, applications, services, metrics, logs, alerts,
  operations, deployments, approvals or audit endpoints (M1.5 onward).
- No provider adapters, Jenkins client, Prometheus client or Loki client.
- No E2E tests or Playwright (M1.7).

Packages **were** installed in this session (`uv sync`, `npm install`) — that is M1.1's
job, and both lockfiles are committed. No cloud resource was provisioned, no production
system contacted, and no real credential created or stored.

## 11. Consistency review

Performed 2026-10-13 across `PRD.md` and the eight documents.

| Check | Result |
|---|---|
| Documents match the PRD | Yes, with the 15 contradictions and gaps in §6 recorded rather than silently resolved |
| Architecture uses the chosen stack | Yes — React/TypeScript/Vite/Tailwind, FastAPI/Pydantic/SQLAlchemy/Alembic, PostgreSQL/Redis/Celery, Prometheus/Node Exporter/systemd exporter/windows_exporter/Alloy/Loki/alerting, Docker Compose, AWS SSM, Azure APIs, OIDC, Jenkins REST. No technology appears outside this set |
| Database supports the API contracts | Yes — every endpoint's reads and writes map to defined tables, columns and indexes. Five entities were added, each traced to a PRD requirement the listed columns could not satisfy |
| Security controls appear in the implementation and testing plans | Yes — each mandatory rule has a milestone security checklist and a test in [`docs/TESTING.md`](docs/TESTING.md) §8 |
| Service and deployment state transitions consistent | Yes, after one fix — `OperationStatus` and `DeploymentStatus` in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §9 are exactly `PRD.md` §4.6 and §4.10; `CHECK` constraints in [`docs/DATABASE.md`](docs/DATABASE.md) use the same values; the state diagrams in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §6.2 and §8.1 use the same values. Internal `VERIFYING` is documented as internal-only (`AD-09`). **Fixed during review:** three timeout tables stated that a deployment deadline breach yields `TIMED_OUT`, which is not a member of `DeploymentStatus` (`PRD.md` §4.10 lists no such state). Now recorded as `FAILED` with `failure_reason = DEPLOYMENT_TIMED_OUT` |
| Every privileged operation has authorisation and audit | Yes — [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) names a permission and an audit `action_type` per endpoint; §10 and §28 define the audit obligation and catalogue |
| Provider capabilities not assumed identical | Yes — capability matrix in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §7.2, per-account capabilities endpoint, per-OS-family metric templates, and a test that pins the differences |
| Implementation plan has no forward dependencies | Yes — each milestone's dependencies point only backwards; the gating table lists which open decision blocks which milestone |
| Duplication avoided | Yes — each topic has one owner (security rules in SECURITY, schema in DATABASE, endpoints in API_CONTRACTS, decisions in ARCHITECTURE §18–19), referenced by relative link elsewhere |
| Relative links | All inter-document links verified as existing files with matching heading anchors |

Re-run this review whenever a contract document changes.

### M1.1 code-vs-documentation review (2026-10-13)

| Check | Result |
|---|---|
| Stack matches [`docs/TECH_STACK.md`](docs/TECH_STACK.md) | Yes — React/TS/Vite/Tailwind, FastAPI/Pydantic/SQLAlchemy, PostgreSQL/Redis/Celery, Docker Compose. Nothing outside the approved set. Tailwind 4 via `@tailwindcss/vite`, so no `tailwind.config` or `postcss.config` is needed |
| Module layout matches [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §2.2 | Yes for the parts that exist (`core/`, `health/`, `db/`, `workers/`). `auth/`, `authz/`, `inventory/`, `metrics/`, `logs/`, `alerts/`, `operations/`, `deployments/`, `approvals/`, `audit/`, `providers/`, `admin/` are absent by design |
| Health endpoints match [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §11 | Yes — per-dependency booleans only, 200/503, no version, hostname or error text. Verified against a live server |
| Error envelope matches §3 and §8 | Yes — `{error:{code,message,correlation_id,details}}`, validation `details.fields[]` with the published codes, and the rejected value never echoed |
| Celery config matches [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §4.3 | Yes — `acks_late`, `prefetch_multiplier=1`, JSON-only serialisation, five queues by blast radius, visibility timeout 4 h (longer than the 1 h deployment deadline), empty beat schedule |
| No shell execution surface | Confirmed — no `subprocess`, `os.system`, `eval` or `exec` anywhere in `backend/app`; bandit clean |
| Mandatory rule 3 (no secrets in VCS) | `.env` git-ignored, `.env.example` placeholders only, `gitleaks` configured in pre-commit and CI — **but unproven locally** (issue 26) |
| Deviation: image digests | AD-16 requires digests; exact tags shipped. Recorded as issue 21, not silently accepted |
| Deviation: smoke gate | [`docs/TESTING.md`](docs/TESTING.md) §11 defines 12 smoke steps; `scripts/smoke-test.sh` implements the 10 that M1.1 can cover and says so in its header. Never executed (issue 20) |

No documentation change was needed as a result of M1.1 — the contracts were followed
rather than amended. The two deviations are tracked as issues, not absorbed.
