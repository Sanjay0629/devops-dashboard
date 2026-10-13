# PROGRESS — DevOps Control Plane

**Purpose:** preserve development state between AI coding sessions. Read this first; update it last.
**Last updated:** 2026-10-14
**Updated by:** M1.4 implementation session (RBAC and authorisation enforcement).
Previously: M1.3 implementation session, amended with the three decisions taken for
M1.4, all three of which are now implemented as agreed (see [§3](#3-in-progress-milestone))

> **Rules for this file**
> - Record only what has **actually happened**. Never mark anything complete because a plan says it should be.
> - Record real test output, with the date it was run. "Should pass" is not a result.
> - Record integrations as *configured* only when a real connection has been made and verified.
> - Keep entries dated and append-only where practical, so history is not lost.

---

## 1. Current project stage

**Stage: Phase 1 — Foundation. Milestones M1.1 – M1.4 complete; M1.5 next.**

The application has a running skeleton, a migrated PostgreSQL schema, working OIDC
authentication with server-side sessions, and — as of M1.4 — **authorisation**: one
matcher, one `require_permission` dependency, ten administrative endpoints behind it,
`AUTHZ_DENIED` auditing, and a route-coverage test that fails the build if any route
escapes a permission. It still monitors nothing — see [§4](#4-pending-milestones).

The first administrator is granted by a one-shot CLI
(`python -m app.admin.bootstrap --email …`), which refuses to run if an administrator
already exists. After that, roles are granted and revoked through
`/api/v1/admin/users/{id}/roles`, and a change takes effect on the target's **next
request** with no re-login and nothing to invalidate — verified against the running
stack, not only in tests.

```
PRD.md  CLAUDE.md  PROGRESS.md  README.md
.env.example  .gitattributes  .gitignore  .dockerignore  .pre-commit-config.yaml
.github/workflows/ci.yml
backend/     pyproject.toml, uv.lock, Dockerfile, alembic.ini
  app/core/        config, logging, redaction, errors, middleware, csrf,
                   redis client, rate limiting
  app/health/      liveness, readiness (database, broker, migrations-at-head)
  app/db/          base, types (uuid7), engines, session, partition helpers
  app/auth/        User, OIDC client, Redis session store, cookies,
                   dependencies, service (JIT provisioning), router, schemas
  app/authz/       Role, Permission, RolePermission, UserRole, SystemSetting,
                   matcher (the one decision), service (per-request resolution,
                   read-only flag, denial audit), dependencies
                   (require_permission), schemas (scope validation)
  app/admin/       router (§25.1–25.10), service, schemas, bootstrap CLI
  app/inventory/   CloudAccount, Server, Application, ApplicationServer,
                   Service, ServiceActionPolicy
  app/approvals/   ApprovalPolicy
  app/audit/       AuditEvent (partitioned, append-only), record() writer
  app/workers/     Celery app, ping, partition maintenance
  alembic/         0001 initial schema, 0002 seed reference data,
                   0003 system_settings + admin permission split
  tests/           unit + tests/auth/ + tests/authz/; tests/integration/ needs
                   real PostgreSQL, and the auth/admin suites also need Redis
frontend/    React + TypeScript + Vite + Tailwind SPA
  src/auth/        SessionProvider, sessionContext, useSession, permissions
  src/pages/       Login
  src/api/         health, session
infra/       compose/docker-compose.yml, compose/mock-idp/ (dev aid),
             prometheus/, loki/, alloy/
scripts/     check-all.sh, smoke-test.sh, check_doc_links.py, pin-digests.sh
docs/        eight design documents
```

There is still **no domain API** (inventory, metrics, logs, operations, deployments)
and **no external integration of any kind**. 14 of the 20 tables in
[`docs/DATABASE.md`](docs/DATABASE.md) exist; the six that record operations,
deployments and alerts arrive with the milestones that write to them. M1.4 added one
table — `system_settings` — in migration `0003`, so the schema is at revision `0003`.
It was applied to the existing development database (`dcp`) with its three users and
14 audit rows preserved; no volume was reset and no database created.

Version control: branch `main` tracking `origin/main`. Last pushed commit `1093311`
(the M1.3 merge); M1.4 work is uncommitted in the working tree at the time of
writing.

## 2. Completed milestones

| Milestone | Status | Date | Evidence |
|---|---|---|---|
| Documentation set drafted (8 documents + this file) | **Drafted, awaiting review** | 2026-10-13 | Committed as `d7dad3d`, pushed to `origin/main` |
| **M1.1** — repository skeleton, Compose core, health endpoints, CI | **Complete, with two caveats** | 2026-10-13 | All 13 local checks pass ([§7](#7-tests-executed-and-their-actual-results)); endpoints verified over real HTTP |
| **M1.2** — database schema, migrations, partitioning, audit immutability, seed data | **Complete** | 2026-10-13 | Migrations applied, downgraded and re-applied against **real PostgreSQL 17.2**; immutability verified against a confirmed superuser ([§7](#7-tests-executed-and-their-actual-results)) |
| **M1.3** — OIDC authentication, Redis sessions, CSRF, JIT provisioning, auth audit, mock IdP, frontend session | **Complete against the mock IdP** | 2026-10-13 | Login and logout driven through the Compose `core` + `mocks` stack **in a real browser**; 126 auth unit + 39 auth integration tests pass; all 14 checks in `scripts/check-all.sh` pass ([§7](#7-tests-executed-and-their-actual-results)) |
| **M1.4** — RBAC matcher, `require_permission`, admin endpoints 25.1–25.10, `AUTHZ_DENIED` audit, read-only mode, bootstrap CLI, route-coverage gate | **Complete** | 2026-10-14 | 270 unit + 132 integration tests pass against **real PostgreSQL 17.2 and real Redis**; the whole flow driven over HTTP against the running Compose stack (login → 403 → bootstrap → 200 → grant → revoke → read-only → 423); all 14 checks in `scripts/check-all.sh` pass ([§7](#7-tests-executed-and-their-actual-results)) |

**M1.3 caveat — the honest limit of what was verified.** Everything was tested
against the **mock** OIDC provider in `infra/compose/mock-idp/`, which checks no
password. That proves the OIDC client is self-consistent and that the session, CSRF,
provisioning and audit behaviour work. It is **not** evidence that a real identity
provider works: claim shapes, discovery quirks, redirect-URI registration and
token-endpoint authentication all differ per provider (`OPEN-01`). The mock is a
development aid and is labelled as one in its own README, in the Compose file and in
`.env.example`.

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

**None.** M1.4 is finished; M1.5 (server and application inventory) has not begun —
and **must not start before `OPEN-02` is confirmed** (it decides whether
`applications` carries an `environment` column, and reversing that after M1.5 means a
data migration).

### What M1.4 implemented (2026-10-14)

The three decisions recorded below as "Decided for M1.4" were implemented exactly as
agreed, and the documentation debt the second one created has been paid in full.

- **One matcher, one dependency.** `app/authz/matcher.py` holds the only
  authorisation decision in the codebase — a pure function, 27 table-driven cases.
  `app/authz/dependencies.py` holds the only dependency, `require_permission`, which
  resolves grants from PostgreSQL per request, audits every denial, and then applies
  the read-only gate. The scoped question (`Authorized.permits(…, target)`) calls the
  same matcher from the handler, because
  [`docs/SECURITY.md`](docs/SECURITY.md) §20 requires scope to be checked *after* the
  row is loaded — which is why the `scope_source` parameter sketched in the plan is
  not built.
- **Ten endpoints** (§25.1–25.9 plus the new §25.10 read-only toggle), each with an
  explicit permission dependency, each mutation audited.
- **A route-coverage gate** that walks the live routing table and fails if any route
  carries neither a permission dependency nor an explicit exception. Its exception
  list distinguishes *public* from *session-authenticated* and asserts both claims,
  and the gate has a test of its own that plants a bypass and proves it is caught.
- **Scope semantics the contracts left open**, recorded as assumption `A-09` below
  and written into [`docs/SECURITY.md`](docs/SECURITY.md) §4.

Deliberately **not** built, to avoid pulling later milestones forward: no domain
endpoints (M1.5+), no scope-filtered list queries (there is no scoped collection yet),
no permission-aware navigation (there is no navigation until M1.6's routing shell), no
read-class rate limiting on the admin reads (`docs/SECURITY.md` §13 defines no admin
class, and no read endpoint is rate-limited yet — the first domain reads in M1.5 are
where that belongs).

### Outstanding decisions

In the order they bite:

- [x] **`OPEN-04` — RESOLVED 2026-10-13 as Redis-only.** No `user_sessions` table was
      created, following the documented default and the instruction for this session.
      Login, logout and denial are audited in PostgreSQL, so the authentication record
      outlives Redis; live sessions do not. Accepted cost: restarting Redis signs
      everyone out. Reversing it later is still an additive migration. Recorded in all
      four contract documents.
- [ ] **`OPEN-01` — still open, and now the main caveat on M1.3.** Which identity
      provider, and does any claim ever map to a role? The documented default — "roles
      are assigned in DCP only" — is what M1.3 implemented: `IdentityClaims` has no
      field that could carry a role, and a test asserts that a token claiming
      `groups: ["ADMINISTRATOR"]` grants nothing. What is unresolved is *which* real
      provider, and therefore whether its claim shapes and registration rules match
      what the mock taught us.
- [ ] **`OPEN-02` — before M1.5.** `applications` was created **without** an
      `environment` column (`AD-12`). Reversing that after M1.5 means a data
      migration, so confirm it before then.
- [ ] `TS-02` (systemd metrics source) and `TS-01` (charting library) — before M2.
- [ ] `OPEN-15` — the audit-immutability hardening M1.2 could not finish: a
      non-superuser application role separate from the migration owner. Scheduled
      M6.2; see issue 28.

### Decided for M1.4 — agreed 2026-10-13, **implemented 2026-10-14**

Three questions M1.4 could not start without. None was settled by the PRD or the
contract documents, each was put to the product owner, and each answer below is
theirs. All three are now implemented as written; what each one became in code is
noted inline.

- [x] **The first administrator is granted by a one-shot CLI command.**
      `app.admin.bootstrap` grants `ADMINISTRATOR` to an **existing** user, writes an
      audit event, and refuses if any administrator already exists.
      `user_roles.granted_by` is `NOT NULL` and references `users`, so the schema
      forbids an unattributed grant and the first one has to name an attributable
      granter. Rejected: an auto-grant keyed on a configured subject at first login —
      it works with zero operator steps, but it puts a privilege-granting branch
      inside the authentication path, which is the last place to want one. Also
      rejected: documented SQL only, which leaves the first grant unaudited. Closes
      issue 37.
      **Implemented** as `backend/app/admin/bootstrap.py`
      (`python -m app.admin.bootstrap --email …`). It grants only to an **existing
      active** user, so it cannot invent an identity; refuses with exit code 2 if any
      unexpired ADMINISTRATOR grant held by an active user exists; grants a full scope
      with `production` named **literally**, because the matcher never lets `"*"`
      imply production; and audits `ROLE_GRANTED` as `actor_type='SYSTEM'`,
      `actor_label='system:bootstrap'` — the initiator holds shell access and has no
      user row, so attributing it to the granted user would be a false attribution.
      `granted_by` is a self-reference forced by the schema and is flagged as such in
      the audit metadata. Verified in the container: first run granted, second run
      exited 2, unknown address exited 1.

- [x] **No permissions cache. Resolve per request from PostgreSQL.** The
      `permissions_version` counter is **not** built. This is an **agreed divergence
      from `AD-03`**, approved by the product owner rather than taken unilaterally
      (CLAUDE.md §3): the correctness requirement in PRD §4.1 is per-request
      resolution, the cache is an optimisation with no measured need, and a wrong
      invalidation can only ever make revocation *less* immediate than no cache at
      all. **Documentation debt this creates, to be paid in the same commit as the
      implementation:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) `AD-03` and
      §9.2, [`docs/SECURITY.md`](docs/SECURITY.md) §4 rule 6,
      [`docs/DATABASE.md`](docs/DATABASE.md) §12 and
      [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §25.5 all currently describe a
      cache and a counter that will not exist. None of them may be left saying so.
      Revisit only if a measurement shows per-request resolution is too slow; the
      counter is additive whenever that happens.
      **Implemented and the debt is paid:** all four documents now describe
      per-request resolution with no cache
      ([`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) `AD-03` and §9.2,
      [`docs/SECURITY.md`](docs/SECURITY.md) §4 rule 6,
      [`docs/DATABASE.md`](docs/DATABASE.md) §3.5 and §12,
      [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §2 and §25.5), each amendment
      dated and attributed to this decision rather than silently rewritten. Nothing in
      the schema or the code mentions `permissions_version`.

- [x] **Read-only mode lives in a `system_settings` row, toggled by a new audited
      admin endpoint.** [`docs/SECURITY.md`](docs/SECURITY.md) §27 names it as the
      *first* containment step in an incident involving this portal, so it has to
      survive a restart and a Redis flush — which rules out a Redis flag — and has to
      be flippable without a redeploy, which rules out an environment variable. Needs
      **migration `0003`** (the first since M1.2) and a **new entry in
      [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §25**, which today defines no
      toggle endpoint at all despite `READ_ONLY_MODE_CHANGED` already being in the
      audit catalogue (§28).
      **Implemented:** migration `0003` creates `system_settings`
      ([`docs/DATABASE.md`](docs/DATABASE.md) §3.6) with a closed `key` set and a
      seeded `read_only_mode = false` row, and §25.10 is now in the contract. The gate
      lives **inside `require_permission`** rather than in middleware with a list of
      exempt paths: every state-changing endpoint has a permission dependency by
      construction, so that covers all of them with nothing to keep in sync, while the
      paths that must keep working during an incident — health probes, login, callback,
      logout — are exactly the ones with no permission dependency. The toggle itself
      opts out explicitly (`allow_in_read_only=True`), or it could not be switched off.
      Permission is checked **before** the read-only gate, so an unauthorised caller
      learns nothing about whether the portal is contained. Reading the flag fails
      **closed**: a missing or non-boolean row means refuse writes.

Assumption recorded during M1.4 (CLAUDE.md §9 — the contracts are silent on it):

- **`A-09` Scope dimension semantics.** [`docs/SECURITY.md`](docs/SECURITY.md) §4
  gives five evaluation rules and does not say what an **absent** dimension means, so
  the matcher had to decide. Chosen: a dimension the grant does not mention is
  **unconstrained**, because [`docs/DATABASE.md`](docs/DATABASE.md) §3.5's own example
  scope omits `cloud_account` entirely and would otherwise grant nothing; an **empty
  scope `{}` grants nothing at all**, which is the same document read literally; a
  grant constraining a dimension the **target does not have** is a denial; and a
  permission with **no target** (the admin endpoints) is a capability check whose
  grant must still be non-empty. Rule 4 overrides all of it: a production target
  requires `environment` to name `production` literally, so an absent or wildcard
  dimension can never *imply* production. Written into
  [`docs/SECURITY.md`](docs/SECURITY.md) §4 and
  [`docs/DATABASE.md`](docs/DATABASE.md) §3.5 in the same change, and each clause has
  its own case in `tests/authz/test_matcher.py`.

Assumptions recorded during M1.3 (CLAUDE.md §9 — the PRD is silent on each):

- **`A-05` Identity conflict means deny.** `users` carries a case-insensitive unique
  index over *active* users' emails, so a **new** OIDC subject presenting an address an
  active user already holds is a reachable case (a re-provisioned directory account,
  two providers). The PRD does not say what should happen. The default chosen is to
  **deny the login** — `403` plus an audited `LOGIN_DENIED` with
  `reason: identity_conflict` — rather than merge, rename or create a second row.
  Merging identities silently is how one person inherits another's audit history, and
  the trail is attributed by `user_id`. `403` rather than `409` because a `409` would
  confirm to an unauthenticated caller that the address is registered. An administrator
  resolves it by retiring the existing row, after which the address is reusable. Found
  by a failing test, not by reading: the first version of this path produced an
  unhandled `IntegrityError`, i.e. a `500` on an authentication endpoint.
- **`A-06` Forwarded client addresses are not trusted.** `X-Forwarded-For` is
  deliberately **not** read. It is attacker-controlled unless a known proxy overwrites
  it, and the value lands in `audit_events.source_ip` *and* in the rate-limit key — a
  spoofable rate-limit key is no rate limit. The connection address is used instead.
  Revisit when the production proxy is configured (`OPEN-12`), with the trusted hop
  count made explicit.
- **`A-07` Session identifiers are stored under a SHA-256 of themselves.** Not required
  by [`docs/SECURITY.md`](docs/SECURITY.md) §5, which only forbids them in URLs, logs
  and audit rows. A session id is a bearer credential for its lifetime, and a Redis key
  dump, `MONITOR` trace or `--bigkeys` scan should not hand over usable ones. Cost: one
  hash per lookup.
- **`A-08` CSRF is checked before routing.** The middleware sits outside the router, so
  a state-changing request that fails CSRF never reaches a handler or opens a database
  session — and probing with `POST` reveals nothing about which routes exist. Visible
  consequence: `POST` to a `GET`-only route answers `403`, not `405`. An existing M1.2
  test asserted that `405`; it now supplies the CSRF credentials to reach routing, and
  a new test asserts the precedence directly.

Decisions settled during M1.3:

- **A JWT library rather than hand-rolled verification.** `pyjwt[crypto]` added, per
  [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §10 rung 3: the "few lines" in question
  are RS256/ECDSA signature verification, JWKS key selection and the `alg`-confusion
  defence, which is the one place in this codebase not to save lines. `authlib` was
  rejected as a much larger surface that bundles an OAuth client whose redirect
  handling we deliberately own; `python-jose` as stale. `httpx` moved from the dev
  group to the runtime dependencies — it was already the approved outbound HTTP
  client. Net new packages: `pyjwt`, `cryptography`, `cffi`, `pycparser`.
- **The mock IdP runs from the backend image, not a third-party one.** ~230 lines of
  FastAPI in `infra/compose/mock-idp/`, needing no new dependency and no 300 MB JVM
  image, and giving full control over what it does and does not implement. It *does*
  implement the parts the security checks depend on — RS256 with a published JWKS and
  `kid`, discovery, `nonce` echoed into the ID token, PKCE S256 **verified**,
  single-use TTL-bounded codes, constant-time client-secret comparison — so passing
  against it means something, while still not meaning the real integration works.
- **`mock-idp` shares the backend container's network namespace.** `iss` is compared
  literally, so one issuer URL must resolve identically from the browser and from
  inside the backend. The alternatives were to weaken the check that discovered
  endpoints stay on the issuer's origin (a real control — it is what stops a tampered
  discovery document from redirecting the token exchange, client secret included), or
  to depend on `host.docker.internal` resolving on the host, which is not dependable.
  Known sharp edge, documented in the README and in issue 33: recreating `backend`
  alone leaves `mock-idp` attached to the old namespace.
- **`DCP_OIDC_REDIRECT_URL` points at the frontend origin in Compose.** In production
  the SPA and API share one origin behind the reverse proxy (AD-02); the dev server's
  proxy reproduces that, so the flow and its final `next` redirect stay on the origin
  the operator is browsing. Pointing it at `:8000` would land them on the backend,
  which serves no SPA.
- **Cookie `Secure` is a derived property, not a setting.** `docs/SECURITY.md` §5
  permits exactly one deviation — `Secure=false` on localhost — so there is no
  `DCP_COOKIE_SECURE` for a deployment to get wrong. A test asserts no such field
  exists.

Decisions settled during M1.2:

- **UUIDv7 over v4.** `uuid.uuid7` is Python 3.14+ and this project pins 3.12, so
  `app/db/types.py` implements RFC 9562 in twelve lines. Time-ordered ids keep index
  inserts at the right-hand edge of the B-tree on the append-heavy tables instead of
  scattering them. Ids are opaque, so swapping the generator later needs no migration.
- **Enums as `text` + `CHECK`, not native PostgreSQL enums**, per
  [`docs/DATABASE.md`](docs/DATABASE.md) §1 rule 5 — now exercised by tests that
  assert invalid values are rejected.
- **Alembic runs synchronously** while the app is async. Migrations have no reason to
  be concurrent, and psycopg3 serves both modes behind one dialect, so there is one
  driver and no second code path.
- **The initial migration was autogenerated, then hand-edited**, as
  [`docs/DATABASE.md`](docs/DATABASE.md) §16 item 2 prescribes. Autogenerate produced
  13 tables, the partial and functional indexes and the partition declaration; the
  `updated_at` trigger, the initial partitions and the immutability triggers were
  added by hand. It also omitted `from sqlalchemy.dialects import postgresql` despite
  emitting `postgresql.*` types — that import is now in `script.py.mako` so the gap
  does not recur on every future revision.

## 4. Pending milestones

Per [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md). **All pending below are unstarted.**

| Phase | Milestones | Status |
|---|---|---|
| 1 — Foundation | ~~M1.1 skeleton + Compose + CI~~ **done** · ~~M1.2 schema + migrations~~ **done** · ~~M1.3 OIDC auth~~ **done** · ~~M1.4 RBAC~~ **done** · M1.5 inventory · M1.6 dashboard shell · M1.7 consolidation | **M1.5 next (confirm `OPEN-02` first)** |
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

**Exercised in code as of M1.3** (implemented, not formally approved):

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
| AD-11 audit append-only | `audit_events` triggers + revoked grants; verified against a confirmed superuser (M1.2) |
| AD-12 applications environment-agnostic | `applications` created without an `environment` column — **still `OPEN-02`** |
| AD-14 derived server status | `servers.status` defaults to `PENDING`; the stale sweep owns the rest (M2.5) |
| **AD-02** OIDC auth code + PKCE, BFF, opaque cookie | `app/auth/oidc_client.py` (discovery, JWKS, PKCE S256, token exchange, ID-token verification), `app/auth/session.py` (opaque 256-bit id in Redis), `app/auth/cookies.py`. Verified in a real browser: the session cookie is invisible to JavaScript and no token reaches the SPA |
| **AD-19** Redis rate limits, fail-closed on privileged classes | `app/core/ratelimit.py`; `/auth/*` passes `fail_closed=True`, and a test observes a `429` with `Retry-After` when Redis is unreachable |
| docs/SECURITY.md §2 ID-token verification | Signature against cached JWKS with rotation, `iss`, `aud`, `exp`, `iat`, `nonce`, bounded skew; algorithm taken from our allowlist, never from the token. 44 unit tests, including `alg: none` and HS256 key confusion |
| docs/SECURITY.md §5 session handling | Idle (30 min) and absolute (8 h) deadlines enforced server-side; rotation on login; `terminate_user_sessions`; `HttpOnly`/`Secure`/`SameSite=Strict`/`Path=/`/host-only; permissions never stored in the session |
| docs/SECURITY.md §11 CSRF | `app/core/csrf.py` double-submit plus `Origin`/`Referer`, constant-time compare, checked before routing. 24 unit tests plus the live `403`s for missing, mismatched and cross-origin |
| Mandatory rule 9 never fetch a user-supplied URL | Every discovered OIDC endpoint must share the configured issuer's origin before it is fetched, so a tampered discovery document cannot redirect the token exchange (which carries the client secret). Redirects are not followed |
| Mandatory rule 6 fail closed | `require_session` answers `401` for every failure mode including an unreachable Redis; rate limiting fails closed on `/auth/*`; a provisioning `IntegrityError` denies rather than `500`s |
| PRD §4.1 immediate revocation | `/auth/session` re-reads roles and permissions from PostgreSQL on every call and the session record holds none. Tests grant and revoke with no re-login and no restart. **M1.4:** the same is now true of enforcement — `require_permission` resolves grants per request, and `test_grant_then_revoke_takes_effect_on_the_next_request` watches a `403` become a `200` and back |
| AD-15 demonstration banner | `demo_mode: true` on `/auth/session` renders a persistent banner; observed in the browser |
| Mandatory rule 10 attribution | `ck_audit_events_actor_attribution`: a `USER` event must name its user, enforced by the database |
| docs/DATABASE.md §10 soft delete | `deleted_at` only on inventory tables, each with partial unique indexes; a test asserts both |
| docs/DATABASE.md §16 migrations | autogenerated then hand-edited; drift test asserts models and schema agree |
| **AD-03** RBAC per request, fail closed, **no cache** (amended, agreed) | `app/authz/service.resolve_grants` (one indexed join), `app/authz/matcher.permits` (the single decision), `app/authz/dependencies.require_permission` (the single dependency). Fault injection into resolution answers `403` |
| docs/SECURITY.md §4 rules 1–7 | 27 table-driven matcher cases: deny by default, stored-attribute targets, deny-beats-allow, production never implicit, malformed/empty scope denies, one matcher |
| docs/SECURITY.md §6 never rely on frontend authz | Four non-admin roles × ten admin endpoints all answer `403` over HTTP with a valid session; the frontend's `permissions.ts` is advisory and its test says so |
| docs/SECURITY.md §20 every route declares a permission | `tests/authz/test_route_coverage.py` walks the live routing table; exceptions are listed individually as *public* or *session-authenticated* and both claims are asserted |
| docs/SECURITY.md §27 read-only mode | `system_settings.read_only_mode` + §25.10 toggle; writes answer `423` while reads and logout keep working. Verified over HTTP |
| docs/TESTING.md §8 no debug bypass | Grep gate over detokenised source, with its own test that plants a bypass and proves it is caught |
| docs/API_CONTRACTS.md §25 admin endpoints | `app/admin/router.py`, ten routes, each mutation audited; delegation escalation, last-administrator revocation and self-disable all refused |

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
| 20 | **FURTHER RESOLVED 2026-10-13 (M1.3).** The full `core` stack **plus** the `mocks` profile was started and used: `postgres`, `redis`, `backend`, `worker`, `beat`, `frontend` and `mock-idp` all reached `healthy`, and login/logout were driven through them in a real browser. `scripts/smoke-test.sh` itself is **still unexecuted**, so the M1.1 criterion is not formally signed off by its own gate. Previously: the Docker daemon became available during M1.2, and `postgres` and `redis` were started and verified healthy (PostgreSQL 17.2, `redis-cli ping` → PONG). Originally: **the Compose stack has never been started.** No Docker daemon on the implementation machine. `docker compose --profile core config` validates (exit 0), but `up` is unexecuted, so "every service starts healthy" is **unverified**. `scripts/smoke-test.sh` has likewise never run. | Blocks the M1.7 smoke gate only | Run `scripts/smoke-test.sh` and record the result. The stack itself is known to come up. |
| 21 | **Images use exact tags, not digests**, contrary to AD-16. A digest cannot be resolved without a daemon, and inventing one would be fabricating a fact. | Reproducibility | `scripts/pin-digests.sh` resolves them; commit the result and close this entry. |
| 22 | **Native Windows without `--reload` cannot reach PostgreSQL.** uvicorn 0.54 picks its loop by platform: no `--reload` on Windows means `ProactorEventLoop`, which psycopg's async driver rejects, so `/readyz` reports `database: false` even with PostgreSQL up. Verified directly. | Local dev ergonomics only; production is Linux | Documented in `README.md`. An attempted `asyncio` policy fix was **removed** — uvicorn uses a loop *factory* and ignores the policy, so the code fixed nothing while looking like it did. |
| 23 | **Compose host ports moved off the defaults.** A PostgreSQL instance is already listening on 5432 on the implementation machine, so the original mapping would have collided on first `up`. | Avoided | PostgreSQL now publishes on host 5433 and Redis on 6380, both overridable. Container-to-container traffic is unchanged. |
| 24 | **`uv run pip-audit --strict` fails on the local project**, which is not on PyPI. | Avoided | `--strict` dropped from CI and `check-all.sh`, with the reason in a comment. Verified empirically that plain `pip-audit` still exits 1 on a real vulnerability (reproduced with pytest 8.4.2, 2 findings) and 0 when clean. |
| 28 | **Audit immutability is enforced by triggers, not by grants — and the grant layer is currently inert.** Measured directly: with the single development database user (a superuser and the table owner), `information_schema` reported `UPDATE`/`DELETE` as revoked while both statements still succeeded. Triggers close it for every role, including superusers, but a superuser can still drop a trigger. | Accepted for now, documented | The remaining hardening is a non-superuser application role separate from the migration owner, plus audit export to an independently protected store. Both are M6.2 (`OPEN-15`). The grant layer is already in place and becomes effective the moment the app connects as a dedicated role. |
| 29 | **RESOLVED 2026-10-13. A DSN host of `localhost` hung the integration suite indefinitely.** First diagnosed as "slow" (25+ minutes); `pytest-timeout` showed pytest at **0 CPU seconds** with no database activity, and the stack pinned it to a sync `psycopg` connect inside `selectors.select()` with no deadline. Cause: Compose publishes these ports on IPv4 loopback only, `localhost` resolves to `::1` first on Windows, and the filtered IPv6 attempt had no connect timeout. | Was blocking the M1.2 DoD | Three fixes: `connect_timeout` on both application engines and on the test engine; host-side DSNs changed to `127.0.0.1`; `pytest-timeout` added with a 120s default so a hang fails instead of stalling. **Suite went from 25+ minutes to 9 seconds.** |
| 31 | **The readiness probe used one shared wall clock for all three checks**, so a single slow dependency consumed the budget and every check reported `false` — including the broker, which was healthy. Found while investigating issue 29. | Fixed | Each check now has its own deadline, so per-dependency verdicts are accurate. |
| 32 | **`compare_server_default` produces permanent false drift** on function defaults: Alembic compares the model's `TextClause` against the reflected string form, and `now()` never matches. | Fixed | Disabled in `alembic/env.py` and the drift test, with the reason in a comment. Table, column, index and constraint drift are still detected — `text("now()")` in `TimestampMixin` was also needed and is kept. |
| 30 | **`alembic.ini` needed `path_separator = os`.** Without it Alembic warns that it is falling back to legacy splitting of `prepend_sys_path` on spaces, commas and colons. | Fixed | Added; the deprecation warning is gone. |
| 25 | **One upstream deprecation warning remains** in the backend suite: starlette advises `httpx2` for `TestClient`. Left visible rather than silenced, as a migration signal. | Cosmetic | Revisit when `httpx2` is stable. |
| 27 | **Fixed 2026-10-13: no `.gitattributes`, so a Windows checkout produced CRLF and `npm run format:check` failed on 14 files** while Linux CI passed — a check that fails only on some machines. The shell scripts would have broken the same way on Linux, where a CRLF `.sh` fails with a carriage-return error. Found by running the checks from a fresh clone, not from the working tree. | Was blocking local verification | `.gitattributes` with `* text=auto eol=lf` added (`ef1de2b`); working tree renormalised. Committed blobs were already LF, so no file content changed. Re-verified from a fresh clone: LF checkout, all frontend and backend checks pass. |
| 26 | **The secret scanner, image scan and Compose smoke gate are unverified locally.** `pre-commit`, `gitleaks` and `trivy` are not installed on the implementation machine, so the M1.1 criterion "the scanner fails on a planted test secret" has **not** been demonstrated. The hooks and CI jobs are configured but unproven. | Verification gap | Run `pre-commit install && pre-commit run --all-files`, plant a test secret, confirm it fails, then record it here. |

### Implementation issues (found during M1.3, 2026-10-13)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 33 | **`mock-idp` detaches when `backend` is recreated on its own.** It uses `network_mode: service:backend` so that one issuer URL resolves from both the browser and the backend; recreating only `backend` (e.g. `up -d --build frontend`, which recreates it as a dependency) leaves `mock-idp` in the old network namespace, and `/auth/login` then answers `503 provider_unavailable`. Hit during this session and diagnosed from `docker exec dcp-backend-1 curl localhost:9000` returning nothing. | Local development only | Documented in `README.md` and `infra/compose/mock-idp/README.md`: a plain `up -d` with both profiles recreates both. Not worked around in code, because the alternatives are to weaken the issuer-origin check or to depend on `host.docker.internal`. |
| 34 | **RESOLVED 2026-10-13. The Vite dev server ignored `VITE_DEV_PROXY_TARGET`.** Compose has set it since M1.1 but `vite.config.ts` hard-coded `http://localhost:8000`, which inside the frontend container is the frontend. Invisible until M1.3 gave the SPA an API call to make; then `/api/v1/auth/session` through the dev server failed with `ECONNREFUSED`. | Was blocking the M1.3 Compose DoD | `vite.config.ts` now reads the variable. `vite.config.ts` is also mounted into the container — previously only `src/` and `index.html` were, so a config change needed an image rebuild, which is why the dead variable survived three milestones. |
| 35 | **RESOLVED 2026-10-13. Windows `ProactorEventLoop` broke every async-database test.** M1.2's integration tests use the *sync* engine, so this only appeared with M1.3's first async-database tests: psycopg refuses that loop and all 39 failed with `InterfaceError`. | Was blocking the M1.3 test suite | A `pytest_asyncio_loop_factories` hook in `backend/tests/conftest.py` selects `SelectorEventLoop` on Windows. This does **not** contradict issue 22's note that an asyncio *policy* fix was removed from application code: uvicorn picks its loop through a factory and ignores the policy, whereas pytest-asyncio takes a factory. The application answer on Windows is still `--reload`, or Linux. |
| 36 | **RESOLVED 2026-10-14 (M1.4).** `/auth/session`'s `permissions` array is now *proven* advisory: `test_the_permissions_array_is_advisory` grants an empty-scope `ADMINISTRATOR`, reads `admin:users` out of the array, and watches `GET /api/v1/admin/users` answer `403`. The M1.3 checklist box in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) is now ticked with that evidence. Previously: the claim rested on structure alone, because no permission-bearing endpoint existed to reject anything. | Was a verification gap | None. |
| 37 | **RESOLVED 2026-10-14 (M1.4)** — `python -m app.admin.bootstrap --email …` exists, refuses to run twice, grants only to an existing active user and audits the grant as `system:bootstrap`. Verified in the running container (granted, then exit 2, then exit 1 for an unknown address). Previously: **no way to grant the first role through the application.** JIT provisioning deliberately creates users with no roles, and there is no bootstrap administrator (alembic `0002`). Role administration is M1.4, so today the only way to grant a role is a manual `INSERT` into `user_roles` — whose `granted_by` is `NOT NULL` and references `users`, so the first grant has to name an existing user as its own granter. | Expected at this milestone | **Answered 2026-10-13** — a one-shot `app.admin.bootstrap` CLI command, see [§3](#3-in-progress-milestone). Implemented in M1.4 alongside the role endpoints. |
| 38 | **A restart of Redis signs every user out.** The accepted cost of `OPEN-04` resolving as Redis-only. Login, logout and denial remain in PostgreSQL, so the record survives; the live sessions do not. | Accepted, by decision | None. Revisit only if operators report it as a real problem; adding a durable table later is an additive migration. |

### Implementation issues (found during M1.4, 2026-10-14)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 39 | **The seeded permission catalogue does not use the names the contracts do, and M1.4 only reconciled the two it needed.** [`docs/SECURITY.md`](docs/SECURITY.md) §4 and [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §18/§24/§26 name `admin:integrations`, `logs:read:application` and `logs:read:system`; the M1.2 seed created `integration:manage`, `logs:read` and `logs:query`. Migration `0003` added the contract-named `admin:users` and `admin:roles` (needed by §25) and narrowed `admin:manage` to system settings, but left the integration and log permissions alone — renaming permissions nothing enforces yet would be churn, and the log split carries a real security distinction that the milestone enforcing it should design. | Naming drift; no behavioural effect today, because only ADMINISTRATOR holds any of them | Reconcile `integration:manage` → `admin:integrations` in M1.5 (the first endpoint to require it, §18.2) and `logs:read`/`logs:query` → `logs:read:application`/`logs:read:system` in M3.2. [`docs/SECURITY.md`](docs/SECURITY.md) §4 now names both forms so the divergence is visible rather than latent. |
| 40 | **A CSRF rejection answers `code: "forbidden"`, not `code: "csrf_failed"`.** [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §3 defines `403 csrf_failed` as a distinct code; `app/core/csrf.py` emits `forbidden` with the message "CSRF validation failed." Found while exercising the admin endpoints over HTTP without the header. | Contract divergence; a client switching on `code` cannot tell CSRF failure from permission failure | One-word fix in `app/core/csrf.py` plus the M1.3 CSRF tests that assert the current code. **Not done here**: `app/core/csrf.py` is M1.3's module and outside this milestone's scope (CLAUDE.md §3). |
| 41 | **A privilege change affecting *another* user does not rotate their session identifier,** which [`docs/SECURITY.md`](docs/SECURITY.md) §5 lists among the rotation triggers ("role change affecting the user"). Rotation requires issuing a new cookie, which can only happen on a request from that user's own browser, so a third party's grant change cannot rotate it. Nothing is cached in the session, so there is no stale authority to invalidate — the change takes effect on their next request regardless — and terminating their sessions instead would contradict [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §25.5's "without a re-login". `session_store.rotate()` therefore still has no caller. | Documented gap, no known exploit: the session record holds a `user_id` and nothing about authority | Either mark the session for rotation on its next use (a flag the session store would honour in `require_session`), or amend §5 to say rotation applies to the caller's own privilege change. Needs a decision; not invented here. |

---

## 7. Tests executed and their actual results

All results below were produced by running the commands and reading the output.
Nothing here is inferred.

### Executed — `./scripts/check-all.sh`, 14/14 passed (M1.4, 2026-10-14)

| Check | Command | Result |
|---|---|---|
| Backend lint | `uv run ruff check .` | **pass** — "All checks passed!" |
| Backend format | `uv run ruff format --check .` | **pass** — 80 files already formatted |
| Backend types | `uv run mypy` (strict) | **pass** — "no issues found in 51 source files" |
| Backend security lint | `uv run bandit -q -r app` | **pass** — no findings (4 633 lines scanned) |
| Backend unit tests | `uv run pytest -m "not integration"` | **270 passed**, 132 deselected, 1 warning, 18.74s |
| Backend integration tests | `uv run pytest -m integration` | **132 passed**, 270 deselected, 1 warning, 28.91s |
| Backend dependency audit | `uv run pip-audit` | **pass** |
| Frontend lint | `npx eslint .` | **pass** |
| Frontend format | `npx prettier --check .` | **pass** |
| Frontend types | `npx tsc --noEmit` | **pass** |
| Frontend tests | `npx vitest run` | **39 passed** (4 files), 2.51s |
| Frontend build | `npm run build` | **pass** — 38 modules, 234.35 kB JS / 72.66 kB gzip |
| Frontend audit | `npm audit --audit-level=high` | **pass** — 0 vulnerabilities |
| Documentation links | `python scripts/check_doc_links.py .` | **pass** — 342 links, 13 files, 0 problems |

Both `TEST_DATABASE_URL` and `TEST_REDIS_URL` were set (the existing `dcp_test`
database on the Compose PostgreSQL, Redis database `1`), so **nothing skipped**.

New in M1.4: **35 unit tests** (27 table-driven matcher cases, 8 route-coverage and
bypass-gate tests) and **32 integration tests** (the role matrix — four roles × ten
endpoints in one parametrised case per role — revocation, delegation, read-only mode,
the bootstrap CLI). Frontend: **9** new tests for the advisory permission read. One pre-existing test changed in each suite: the model
registry gained `SystemSetting`, and the migration round-trip now reads head and its
predecessor from the script directory instead of hard-coding `0002`/`0001`.

One deliberate deviation: the plan's expected path for the admin suite was
`backend/tests/authz/test_admin_roles.py`. It lives at
`backend/tests/integration/test_admin_roles.py`, because every assertion in it needs
the real schema and real Redis, and both the `migrated` fixture and the `integration`
marker come from `tests/integration/conftest.py`.

#### Two mutation checks, to show the suite is not vacuous

| What was broken on purpose | Result |
|---|---|
| Fault injected into `resolve_grants` (raises `OperationalError`) | An administrator's request answers **403**, not 200 — `test_resolution_failure_denies` asserts it |
| A bypass planted in a throwaway module (`if settings.debug: return None  # skip_authz`) | The grep gate **finds it**, and ignores the same words in a docstring — `test_the_bypass_gate_would_catch_one` |

### Executed — `./scripts/check-all.sh`, 14/14 passed (M1.3, 2026-10-13)

| Check | Command | Result |
|---|---|---|
| Backend lint | `uv run ruff check .` | **pass** — "All checks passed!" |
| Backend format | `uv run ruff format --check .` | **pass** |
| Backend types | `uv run mypy` (strict) | **pass** — "no issues found in 42 source files" |
| Backend security lint | `uv run bandit -q -r app` | **pass** — no findings |
| Backend unit tests | `uv run pytest -m "not integration"` | **235 passed**, 100 deselected, 1 warning, 21.41s |
| Backend integration tests | `uv run pytest -m integration` | **100 passed**, 235 deselected, 1 warning, 22.50s |
| Backend dependency audit | `uv run pip-audit` | **pass** — "No known vulnerabilities found" |
| Frontend lint | `npx eslint .` | **pass** |
| Frontend format | `npx prettier --check .` | **pass** |
| Frontend types | `npx tsc --noEmit` | **pass** |
| Frontend tests | `npx vitest run` | **30 passed** (3 files), 3.64s |
| Frontend build | `npm run build` | **pass** — 37 modules, 234 kB JS / 72.6 kB gzip |
| Frontend audit | `npm audit --audit-level=high` | **pass** — 0 vulnerabilities |
| Documentation links | `python scripts/check_doc_links.py .` | **pass** — 268 links, 13 files, 0 problems |

Both `TEST_DATABASE_URL` and `TEST_REDIS_URL` were set, so nothing skipped. The
run was against the existing `dcp_test` database on the Compose PostgreSQL and
Redis database `1` (database `0` is the Celery broker and was left alone).

Backend unit tests by area (235 total): **auth 126** (ID token 44, `next` and login
flow 42, CSRF 24, session and cookies 16), db units 29, redaction 27, middleware 18,
config 15, errors 11, health 9. Backend integration (100 total): **auth flow 39**,
plus M1.2's 61. Frontend (30 total): session gating and logout 17,
`BackendStatus` 7, `ThemeToggle` 6.

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

### Executed — M1.2, against real PostgreSQL 17.2 (2026-10-13)

| Check | Result |
|---|---|
| `alembic upgrade head` from empty | **pass** — 13 tables, 4 audit partitions, 9 `updated_at` triggers, seed data |
| `alembic downgrade base` then `upgrade head` | **pass** — full round trip, twice |
| Seed counts | roles 5, permissions 20, role_permissions 62, approval_policies 2, service_action_policies 2, cloud_accounts 1, **users 0** |
| **Audit immutability** | `INSERT` succeeds; `UPDATE`, `DELETE`, `TRUNCATE` all raise `audit_events is append-only`, **as a confirmed superuser** (`usesuper = t`), including directly against a partition. Grants alone were measured **not** to work — see issue 28 |
| Integration suite | **61 passed** in 9.01s (`-m integration`) |
| Unit suite | **108 passed** in 7.60s (`-m "not integration"`) |
| ruff / ruff format / mypy strict | **pass** — 31 source files |
| `/healthz` against the live app | **200** |

### Executed — M1.3, against the live Compose stack and a real browser (2026-10-13)

Stack: `docker compose --profile core --profile mocks up`. All seven services reached
`healthy`. Driven both with `curl` and, for the browser-dependent parts, in a real
browser against <http://localhost:5173>.

| Check | Method | Result |
|---|---|---|
| **`/readyz` all three green over HTTP** — the item M1.2 left explicitly unconfirmed | `curl http://127.0.0.1:8000/readyz` | **200** `{"status":"ok","checks":{"database":true,"broker":true,"migrations":true}}`. Previously never observed end to end |
| Full login round trip | `/auth/login` → mock IdP `/authorize` → `/auth/callback` → `/auth/session` | **302** to the IdP with `code_challenge_method=S256`; **302** back to `next`; **200** from `/auth/session` with the right identity |
| The same flow in a real browser | Clicked "Continue to identity provider", chose the synthetic `viewer` account | Landed back on the SPA showing the demo banner, name, email, "No roles granted — ask an administrator", session expiry, and all three readiness checks green |
| **No token readable by JavaScript** (M1.3 acceptance criterion) | `document.cookie`, `localStorage`, `sessionStorage` read in the browser | `dcp_session` **not visible**; only `dcp_csrf=…` readable; both storages empty; no `eyJ` anywhere. The session value is not a JWT |
| Cookie flags on the wire | raw `Set-Cookie` headers | `dcp_session=…; HttpOnly; Max-Age=28800; Path=/; SameSite=strict` and `dcp_csrf=…; Max-Age=28800; Path=/; SameSite=strict`. No `Domain`; no `Secure` because the environment is `local`, which is the one permitted deviation |
| Logout makes the session unusable | `POST /auth/logout` with cookie + CSRF, then `GET /auth/session` | **200** `{"logged_out":true}`, then **401**. Both cookies cleared (`document.cookie` empty afterwards) |
| CSRF: missing token | `POST /auth/logout` with a valid session, no header | **403** `{"code":"forbidden","message":"CSRF validation failed."}` |
| CSRF: mismatched token | header ≠ cookie | **403** |
| CSRF: cross-origin with a **valid** token | `Origin: https://evil.example.invalid` | **403** |
| The session survived the rejected requests | `GET /auth/session` after the three `403`s | **200** — a failed CSRF check does not destroy the session |
| CSRF from inside the browser | `fetch('/api/v1/auth/logout', {method:'POST'})` with no header | **403** |
| `state` replay | replayed the same `?code&state` | first **302**, replay **400**. Single-use via an atomic `GETDEL` |
| Open redirect on `next` | absolute URL, `//host`, and a shell-mangled `C:/Program Files/Git/servers` | **422** each, with the rejected value not echoed |
| Disabled user cannot log in | set `status='DISABLED'`, logged in as that account | **403** "This account is disabled.", and one `LOGIN_DENIED` row with `reason: user_disabled` attributed to that user's `actor_id` |
| Rate limit enforced | repeated `/auth/login` past the budget | **429** with `Retry-After`. Encountered unintentionally first, while hand-testing — the limit is real |
| Just-in-time provisioning | logged in as `operator` four times and `admin` once | **2** `users` rows, no duplicates. First login per subject audited `provisioned: true`, later ones `false` |
| **Auth audit rows** | `SELECT … FROM audit_events` on the live `dcp` database | `LOGIN_SUCCEEDED`, `LOGOUT`, `LOGIN_DENIED` attributed to `actor_type='USER'` with the user's `actor_id`; `LOGIN_FAILED` as `actor_type='SYSTEM'`, `actor_label='system:auth'`, `actor_id` NULL — which is what `ck_audit_events_actor_attribution` requires |
| **No token material in any audit row** | regex over `sanitized_metadata` and `actor_label` for `eyJ`, `id_token`, `access_token`, `nonce`, `secret`, `code_verifier`, `dcp_session` | **0 rows** |
| **No session id, CSRF token, client secret or JWT in the logs** | grep the live `backend` and `mock-idp` container logs for each value | **0 occurrences** of all four, in both containers |
| Redis holds only the expected key shapes | `redis-cli --scan --pattern 'dcp:*'` | `dcp:session:<sha256>`, `dcp:user-sessions:<uuid>`, `dcp:oidc-login:<sha256>`, `dcp:ratelimit:<bucket>`. No raw session identifier is a key |
| Access token is not retained | scanned every `dcp:*` string value for the mock's access token | **not present** — it is read from the token response and dropped |
| Unconfigured IdP degrades honestly | `/auth/login` with the `mocks` profile down | **503** `not_configured`, naming the variables to set and no values |
| SPA served and proxied | `GET /` and `/api/v1/auth/session` through the Vite dev server | **200** and **401** respectively (after fixing issue 34) |
| Compose config validity | `docker compose --profile core --profile mocks config --quiet` | **exit 0** |
| Schema unchanged by M1.3 | `SELECT version_num FROM alembic_version` before and after | **`0002`** both times. No migration was added; existing data preserved |

### Executed — M1.4, over HTTP against the live Compose stack (2026-10-14)

Driven with `curl` against the running `core` + `mocks` stack (the audit rows below
are timestamped `2026-10-13T18:2x` UTC). The backend mounts `backend/app`
read-only, so the new code was live without a rebuild; migration `0003` was applied
from the host, because `alembic/versions` is **not** mounted into the container.

| Step | Result |
|---|---|
| Real login through the mock IdP as `admin` | **302** to `/`, session cookie set |
| `GET /api/v1/auth/session` with no roles granted | `roles: []`, `permissions: []`, `read_only_mode: false` |
| `GET /api/v1/admin/users` with that session | **403** `{"code":"forbidden"}` — a real session with no grant |
| `python -m app.admin.bootstrap --email admin@example.invalid` in the container | **granted**, audited `ROLE_GRANTED` / `SYSTEM` / `system:bootstrap` |
| The same command again | **exit 2**, "An administrator already exists" |
| The same command for an unknown address | **exit 1**, "No active user with address …" |
| `GET /api/v1/auth/session` again, **no re-login** | `ADMINISTRATOR` with the full scope, 20 permissions including `admin:users` |
| `GET /api/v1/admin/users` again | **200**, three users, `page: {limit:50, offset:0, total:3, has_more:false}` |
| `POST …/{viewer}/roles` with CSRF, scope `{environment:[staging], server:[*]}` | **201**, `granted_by` = the administrator |
| The same `POST` **without** the CSRF header | **403** (CSRF, before routing — assumption A-08) |
| `POST …/roles` with `environment: ["nonsense"]` | **422** `validation_failed`, field `scope_definition.environment` |
| `POST /api/v1/admin/settings/read-only-mode {enabled:true, reason:…}` | **200** `{"read_only_mode":true}` |
| `GET /api/v1/admin/users` while read-only | **200** — reads keep working |
| `GET /api/v1/auth/session` while read-only | `read_only_mode: true` |
| `POST …/roles` while read-only | **423** `{"code":"read_only_mode"}`, audited `AUTHZ_DENIED` with `reason: read_only_mode` |
| `POST …/read-only-mode {enabled:false}` while read-only | **200** — the switch is exempt from its own gate |
| `POST …/roles` after disabling it | **201** |
| `DELETE …/{viewer}/roles/{id}` ×2 | **204** each, audited `ROLE_REVOKED` |
| `DELETE` the administrator's own last grant | **409** `{"code":"conflict"}` "The last Administrator grant cannot be revoked." |
| `PATCH` the administrator's own account to `DISABLED` | **422** `{"code":"not_permitted"}` "You cannot disable your own account." |

Audit rows read back from PostgreSQL afterwards: `ROLE_GRANTED` (×3, one of them
`SYSTEM`/`system:bootstrap` with `actor_id` NULL and
`granted_by_is_self_reference: true`), `ROLE_REVOKED`, `READ_ONLY_MODE_CHANGED` (×2,
with the operator's reason), and `AUTHZ_DENIED` (×3, each naming the permission, the
rule that refused it, the method and the path, attributed to a real `actor_id`).
`SESSION_TERMINATED` metadata carries the count and **no session identifier**.

Existing data preserved: the development database still holds the same three users
and its pre-existing audit rows; the only grants added were the bootstrap
administrator (left in place — it is the point of the command) and two test grants on
`viewer@example.invalid`, both revoked through the API afterwards.

### NOT executed — no result may be claimed

| Check | Why |
|---|---|
| `scripts/smoke-test.sh` | Never run. The stack itself now comes up healthy (issue 20), but its own gate is unexecuted |
| Image build and Trivy scan | same, and `trivy` not installed |
| `pre-commit run --all-files`; planted-secret test | `pre-commit` and `gitleaks` not installed (issue 26) |
| The GitHub Actions workflow | never triggered; `.github/workflows/ci.yml` is unproven |
| ~~`/readyz` returning 200 against a fully migrated database~~ | **Done in M1.3** — see the table above. Moved out of this list |
| A **real production-like** deployment of the authorisation layer | Everything was verified locally: one host, one PostgreSQL, one Redis, the mock IdP. No reverse proxy, no TLS, no non-superuser database role (issue 28) |
| A round trip against a **real** identity provider | No provider chosen (`OPEN-01`) and none exists to point at. Everything in M1.3 was verified against the mock IdP, which checks no password. The remaining unknowns are claim shapes, discovery quirks, redirect-URI registration and token-endpoint authentication |
| ~~`/auth/session`'s `permissions` array proven advisory by a rejected action~~ | **Done in M1.4** — `test_the_permissions_array_is_advisory`. Moved out of this list (issue 36 closed) |
| Session rotation on a **role change** | **Still not done, and now a documented gap rather than a pending task** — see issue 41. `rotate()` has no caller: a third party's grant change cannot issue them a new cookie, and nothing about authority is stored in the session, so there is nothing stale to invalidate |
| ~~`terminate_user_sessions` from an API~~ | **Done in M1.4** — §25.7, plus the implicit termination on `status: DISABLED` (§25.4). Both verified against real Redis |
| Concurrent-session limits, active-session list | `[RECOMMENDED]` in docs/SECURITY.md §5, not mandatory. Not built |
| E2E tests | none exist yet (M1.7) |
| Performance tests | none exist yet (M6.3); **no performance figure has been measured** |
| Backup/recovery rehearsal | nothing to back up yet (M6.4) |

## 8. External integrations

**None is configured. None has been connected. None is operational.**

| Integration | Status | Notes |
|---|---|---|
| OIDC identity provider | **Mock only — no real provider configured** | A **mock** OIDC provider (`infra/compose/mock-idp/`, `mocks` profile) is running and in use: discovery, JWKS/RS256, PKCE S256, single-use codes. It **checks no password** and is a development aid, so this is not an operational integration (CLAUDE.md §7). No real provider chosen (`OPEN-01`), no real client registered, no real credential created |
| PostgreSQL | **Running and migrated** | postgres 17.2-alpine via Compose on host port 5433. Schema still at revision `0002` — M1.3 added no migration. Holds two JIT-provisioned users and the auth audit trail. A separate `dcp_test` database serves the integration suite |
| Redis | **Running and in use** | redis 7.4.1-alpine via Compose on host port 6380. Now carries the session store, the single-use OIDC login state and the rate-limit counters (database `0`; the integration suite uses `1`). Verified by the live login flow |
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
No credential has been created.** Every value in `.env.example` is a placeholder,
including `DCP_OIDC_CLIENT_SECRET`, whose value is literally
`placeholder-client-secret` and is shared with the local mock IdP. The only
non-placeholder connection attempt made during M1.1 was a local psycopg probe
against `127.0.0.1:5432` using placeholder credentials, which failed authentication
as expected (see issue 23).

---

## 9. Next recommended task

**M1.5 — Server and application inventory**
([`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md#m15--server-and-application-inventory))

**Confirm `OPEN-02` before starting it.** It decides whether `applications` carries an
`environment` column; `AD-12` says no and that is what M1.2 built, but reversing it
after M1.5 means a data migration. This is the one blocking question.

**What M1.5 can build on:**

- `require_permission(resource_type, action)` in `app/authz/dependencies.py` — the
  only dependency, already fail-closed and already auditing denials. Inventory routes
  declare `("server", "read")`, `("server", "write")` and so on, and the
  route-coverage test will fail them if they forget.
- `Authorized.permits(resource_type, action, target)` — the **scoped** question, for
  use after the row is loaded, as [`docs/SECURITY.md`](docs/SECURITY.md) §20 requires.
  `Target` carries the four stored dimensions. This is the half of the matcher M1.4
  built and could not yet exercise against a real row, because no scoped resource
  exists; `servers` is the first.
- `app/authz/schemas.py` — scope validation, including the existence checks against
  `servers`, `applications` and `cloud_accounts` that §25.5 needs.
- The `0003` migration pattern for a hand-edited autogenerated revision.

**Things M1.5 must not skip, which M1.4 deliberately left for it:**

1. **Scope filtering inside the query, not after it**
   ([`docs/SECURITY.md`](docs/SECURITY.md) §20): the caller's readable set applied in
   SQL so unreadable rows are never counted in `total`. M1.4 has no scoped collection,
   so nothing proves this yet.
2. **`404` rather than `403` for an out-of-scope server, service, application or
   deployment** ([`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §2), decided per
   endpoint. The admin endpoints answer `403` because administration is not scoped;
   inventory is the opposite case.
3. **Reconcile `integration:manage` → `admin:integrations`** (issue 39) in the same
   migration as the first endpoint that requires it (§18.2).
4. **A read-class rate limit** ([`docs/SECURITY.md`](docs/SECURITY.md) §13), which no
   read endpoint has yet.

**Also still outstanding, and cheap to clear:** `scripts/smoke-test.sh` has never run
(issue 20), images are tag-pinned rather than digest-pinned (issue 21), and the secret
scanner has never been shown to fail on a planted secret (issue 26). The Docker
daemon is available, so all three are unblocked. The CSRF error code (issue 40) is a
one-word fix in an M1.3 module and would suit whoever next touches it.

## 10. Scope confirmation

**M1.4 implementation session (2026-10-14).** Scope was milestone M1.4. Added the
authorisation layer (`app/authz/matcher.py`, `service.py`, `dependencies.py`,
`schemas.py`, and `SystemSetting` in `models.py`), the administration module
(`app/admin/router.py`, `service.py`, `schemas.py`, `bootstrap.py`), migration `0003`,
the matcher and route-coverage suites, the admin integration suite, and the
frontend's advisory permission module plus the read-only banner.

Changes touching code outside M1.4, each forced by this milestone rather than
opportunistic: `app/main.py` mounts the admin router and its docstring now describes
the route-coverage gate; `/auth/session` reports the real `read_only_mode` instead of
a hardcoded `false`; `app/auth/service.effective_permissions` now derives the
advisory list from `authz.service.resolve_grants` rather than running its own copy of
the same join, so the advisory list and the real decision cannot drift on *which
grants exist*; `tests/test_db_units.py` expects the new model; and
`tests/integration/test_migrations.py` reads head and its predecessor from the script
directory instead of hard-coding `0002`/`0001`.

Deliberately **not** changed, though noticed: the CSRF error code (issue 40) is in
`app/core/csrf.py`, an M1.3 module, and the permission-name drift for integrations and
logs (issue 39) belongs to the milestones that first enforce those permissions. Both
are recorded in [§6](#6-known-issues) instead.

No dependency was added. `EmailStr` was considered for the pre-create body and
rejected — it needs the `email-validator` package, and
[`docs/TECH_STACK.md`](docs/TECH_STACK.md) §10 rung 6 applies: a bounded pattern is
enough for a value that is display and routing only, never an authorisation input.

Existing data was preserved: PostgreSQL and Redis were the already-running Compose
services, no volume was reset, no database was created. Migration `0003` was applied
to the existing `dcp` database, whose three users and 14 audit rows are intact; the
integration suite continued to use `dcp_test`, whose schema it drops and rebuilds per
session as it has since M1.2. No cloud resource was provisioned, no production system
contacted, and no real credential created or stored.

**Documentation session (earlier, 2026-10-13).** Documentation only: no code, no
migrations, no packages, no cloud resources, no credentials. Only `PRD.md` pre-existed
and it was read, not modified.

**M1.3 implementation session (2026-10-13).** Scope was milestone M1.3. Added the OIDC
client (discovery, JWKS with bounded rotation, PKCE S256, token exchange, ID-token
verification), the Redis session store, cookie assembly, the CSRF middleware, the
`/auth/*` router, JIT provisioning, the audit writer, Redis-backed rate limiting, a
shared Redis client, the mock OIDC provider in the `mocks` profile, and the frontend
session context, login page and logout. `OPEN-04` resolved as Redis-only, so **no
migration was added** and the schema is unchanged at `0002`.

Two dependencies added: `pyjwt[crypto]` (new) and `httpx` (moved from the dev group
to runtime). Justified against [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §10 in §3
above and in that document.

Changes touching code outside M1.3, each forced by this milestone rather than
opportunistic: `AppError` gained response headers so a `429` can carry `Retry-After`;
`vite.config.ts` now reads the `VITE_DEV_PROXY_TARGET` that Compose had been setting
unused since M1.1 (issue 34); the health panel moved out of `App.tsx` into
`components/BackendStatus.tsx` so `App` could become the session gate, and its test
file moved with it; one M1.2 error test now supplies CSRF credentials because the
middleware runs before routing (assumption A-08); and `test_no_user_is_seeded` moved
from `test_constraints.py` to `test_migrations.py`, where a freshly migrated schema is
in scope — it is a claim about the migrations, and the auth tests legitimately create
users in the shared session schema.

Specifically **not** implemented, to avoid pulling later milestones forward: no
permission matcher, no `require_permission` dependency and no route-coverage test
(M1.4); no user or role administration endpoints (M1.4); no domain endpoints (M1.5+);
no routing shell (M1.6). `rotate()` and `terminate_user_sessions()` were built because
docs/SECURITY.md §5 makes them mandatory session-store behaviour, but no endpoint
calls them yet, and that is stated rather than hidden.

Existing data was preserved: PostgreSQL and Redis were the already-running Compose
services, no volume was reset, no database was created, and the `dcp` schema stayed at
`0002`. The integration suite continued to use the existing `dcp_test` database, whose
schema it drops and rebuilds per session as it has since M1.2. No cloud resource was
provisioned, no production system contacted, and no real credential created or stored.

**M1.2 implementation session (2026-10-13).** Scope was milestone M1.2. Created the
13 Phase 1 tables, two migrations, partition helpers and their maintenance task, the
per-request session dependency, and the unit and integration test suites. Promoted the
`migrations` readiness check into `REQUIRED_CHECKS` and replaced its body with a real
head comparison. Added PostgreSQL and Redis service containers to CI.

Specifically **not** implemented, to avoid pulling later milestones forward: no OIDC
client, session handling or CSRF enforcement (M1.3); no permission matcher or route
dependencies (M1.4); no domain endpoints (M1.5+); and none of the six tables that
record operations, deployments or alerts (M3–M5). A test asserts those tables are
absent, so they cannot appear by accident.

A `dcp_test` database was created in the local Compose PostgreSQL for the integration
suite. No cloud resource was provisioned, no production system contacted, and no real
credential created or stored.

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

### M1.4 code-vs-documentation review (2026-10-14)

| Claim | Verified how |
|---|---|
| `AD-03` is described as the code behaves | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §9.2 and the `AD-03` row, [`docs/SECURITY.md`](docs/SECURITY.md) §4 rule 6, [`docs/DATABASE.md`](docs/DATABASE.md) §3.5 and §12, [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §2 and §25.5 all now say per-request, no cache, no counter — each amendment dated and attributed. `grep -rn permissions_version backend/` matches only two docstrings, both stating that it does not exist |
| Every §25 endpoint exists with the permission the contract names | Asserted in code by `test_admin_routes_declare_the_permissions_the_contract_names`, which transcribes the contract table rather than reading the router |
| §25.10 and `system_settings` are documented, not just built | [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §25.10, [`docs/DATABASE.md`](docs/DATABASE.md) §3.6 (plus the ERD and §20 status), [`docs/SECURITY.md`](docs/SECURITY.md) §27 already required the flag |
| The permission catalogue matches the matrix | `/api/v1/admin/roles` read back from the live stack: ADMINISTRATOR holds `admin:users`/`admin:roles` and **not** `deployment:approve`/`operation:approve`; APPROVER holds both approvals; VIEWER holds no `admin:*` and no `server:write`. Asserted by `test_approver_and_administrator_permissions_match_the_matrix` |
| The scope semantics the matcher implements are written down | `A-09` in [§3](#3-in-progress-milestone) and [`docs/SECURITY.md`](docs/SECURITY.md) §4; one matcher case per clause |
| Divergences are recorded, not hidden | Issue 39 (permission naming), issue 40 (CSRF code), issue 41 (session rotation on another user's role change), `OPEN-26` (pre-create keying) — all four are in writing, none is silently resolved |
| Error codes used exist in the contract | `forbidden`, `not_found`, `conflict`, `duplicate_resource`, `validation_failed`, `read_only_mode` were already in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §3; `not_permitted` was used by §25.4 without being listed, and §3 now lists it |

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

### M1.2 code-vs-documentation review (2026-10-13)

| Check | Result |
|---|---|
| All 13 Phase 1 tables from [`docs/DATABASE.md`](docs/DATABASE.md) exist | Yes, with every documented column, constraint and index |
| The five added entities are present and justified | Yes — `role_permissions`, `service_action_policies`, `approval_policies` created in M1.2; `deployment_targets`, `notification_channels`/`notification_deliveries` deferred to the milestones that write to them |
| Enum values match [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §9 | Yes — `ServiceState`, `ServerStatus`, `Environment`, `Provider` asserted by unit tests against the published sets |
| Soft deletion applied only where [`docs/DATABASE.md`](docs/DATABASE.md) §10 permits | Yes, and every unique index on those tables is partial on `deleted_at IS NULL` — asserted by a test, not by inspection |
| Role matrix matches [`docs/SECURITY.md`](docs/SECURITY.md) §4 | Yes, including that `ADMINISTRATOR` holds no `approve` permission and `VIEWER` holds only `read` |
| `audit_events` append-only (AD-11) | Enforced, **after the first implementation was measured not to work** — see issue 28 |
| No secrets in migrations or seed data | Confirmed: `cloud_accounts.credential_reference` is a reference column and is left NULL by the seed; no user and no credential is seeded |
| **Documentation amended** | [`docs/DATABASE.md`](docs/DATABASE.md) §8 and §16 (trigger-based immutability, `text("now()")` defaults, downgrade must drop the triggers), §20 (implementation status); [`docs/TESTING.md`](docs/TESTING.md) §4 and §16 (drift check, `TEST_DATABASE_URL`) |

Two documentation changes were **substantive, not cosmetic**: the audit-immutability
mechanism changed from grants to grants-plus-triggers because grants were measured to
be insufficient, and the `server_default` convention was tightened to `text("now()")`
because a bare string makes the drift check fail permanently and therefore get ignored.
