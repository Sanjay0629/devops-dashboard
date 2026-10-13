# PROGRESS — DevOps Control Plane

**Purpose:** preserve development state between AI coding sessions. Read this first; update it last.
**Last updated:** 2026-10-13
**Updated by:** documentation preparation session (documentation committed and pushed 2026-10-13)

> **Rules for this file**
> - Record only what has **actually happened**. Never mark anything complete because a plan says it should be.
> - Record real test output, with the date it was run. "Should pass" is not a result.
> - Record integrations as *configured* only when a real connection has been made and verified.
> - Keep entries dated and append-only where practical, so history is not lost.

---

## 1. Current project stage

**Stage: documentation preparation — in progress.**

**Application implementation has not started.** The repository contains requirements and design documentation only:

```
PRD.md
CLAUDE.md
PROGRESS.md
docs/ARCHITECTURE.md
docs/TECH_STACK.md
docs/SECURITY.md
docs/DATABASE.md
docs/API_CONTRACTS.md
docs/IMPLEMENTATION_PLAN.md
docs/TESTING.md
```

There is **no** `backend/`, `frontend/`, `infra/`, `docker-compose.yml`, `pyproject.toml`, `package.json`, Alembic directory, database, container image or CI workflow. No package has been installed. No schema exists. No credential exists.

The repository is under version control: branch `main`, tracking `origin/main` on GitHub, working tree clean. Initial commit `d7dad3d` contains the ten files above and nothing else. There is no `.gitignore` yet — M1.1 adds one.

---

## 2. Completed milestones

| Milestone | Status | Date | Evidence |
|---|---|---|---|
| Documentation set drafted (8 documents + this file) | **Drafted, awaiting review** | 2026-10-13 | Files listed in §1 exist; committed as `d7dad3d` and pushed to `origin/main` |

**No implementation milestone has been completed.**

---

## 3. In-progress milestone

**Documentation preparation and review.**

Outstanding before implementation can start:

- [ ] Review the eight documents against `PRD.md` and confirm or amend the decisions in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §18.
- [ ] **Resolve `OPEN-02`** — `PRD.md` §4.9 lists `environment` as an application attribute, but §6's `applications` table omits it while `deployments` carries it. The documents assume applications are environment-agnostic (`AD-12`). This changes the schema and the applications API, so it must be confirmed **before M1.5**.
- [ ] Decide `OPEN-01` (identity provider) and `OPEN-06` (secret store) — both affect M1.1 configuration.
- [ ] Decide `OPEN-04` (session store durability) before M1.3.
- [x] `git init`, initial commit, pushed to GitHub — done 2026-10-13 (`d7dad3d`).

---

## 4. Pending milestones

Per [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md). **All pending — none started.**

| Phase | Milestones | Status |
|---|---|---|
| 1 — Foundation | M1.1 skeleton + Compose + CI · M1.2 schema + migrations · M1.3 OIDC auth · M1.4 RBAC · M1.5 inventory · M1.6 dashboard shell · M1.7 consolidation | Pending |
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

### Implementation issues

None. There is no implementation.

---

## 7. Tests executed and their actual results

**No test has been written. No test has been run. No test command exists.**

| Suite | Status | Last run | Result |
|---|---|---|---|
| Backend unit | Does not exist | — | — |
| Backend integration | Does not exist | — | — |
| Database / migration | Does not exist | — | — |
| Provider adapter | Does not exist | — | — |
| Worker | Does not exist | — | — |
| Security suite | Does not exist | — | — |
| Frontend unit / component | Does not exist | — | — |
| Compose smoke | Does not exist | — | — |
| E2E | Does not exist | — | — |
| Performance | Does not exist | — | — |
| Backup / recovery rehearsal | Does not exist | — | — |

**No performance figure has been measured.** Every number in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §27 and `PRD.md` §10 is a design budget.

### Verification performed this session

| Check | Method | Result |
|---|---|---|
| `PRD.md` exists in the project root | Directory listing | Confirmed — 40 601 bytes |
| `PRD.md` read in full | Read all 1 442 lines in four passes | Confirmed |
| Pre-existing project files | Directory listing + recursive glob | Only `PRD.md` was present; nothing was overwritten or deleted |
| Cross-document consistency | Manual review ([§11](#11-consistency-review)) | Performed; findings recorded in §6 and §11 |

---

## 8. External integrations

**None is configured. None has been connected. None is operational.**

| Integration | Status | Notes |
|---|---|---|
| OIDC identity provider | **Not configured** | Provider not chosen (`OPEN-01`). Mock IdP planned for M1.3 |
| PostgreSQL | **Not configured** | Container planned in M1.1; no schema (M1.2) |
| Redis | **Not configured** | Container planned in M1.1 |
| Prometheus | **Not configured** | M2.1 |
| Node Exporter | **Not configured** | M2.1 (bundled sample target) |
| systemd exporter | **Not configured** | Source not selected (`TS-02`); M2.4 |
| windows_exporter | **Not configured** | Scope undecided (`OPEN-09`) |
| Grafana Alloy | **Not configured** | M3.1 |
| Grafana Loki | **Not configured** | M3.1 |
| Alertmanager | **Not configured** | M3.3 |
| Grafana | **Not configured** | M1.1 (`observability` profile) |
| AWS Systems Manager | **Not configured** | No account, no role, no SSM document, no credential. M4.3 |
| Azure management APIs | **Not configured** | No subscription, no managed identity, no Run Command script. M4.4 |
| Jenkins | **Not configured** | No instance, no token, no registered target. Mock planned M5.1 |
| Secrets manager | **Not configured** | Backend not chosen (`OPEN-06`) |
| Notification channels | **Not configured** | M3.4 |
| Restricted agent | **Not designed** | Adopt-vs-build undecided (`OPEN-05`). M6.1 |

**No cloud resource has been provisioned. No production account has been contacted. No credential has been created.**

---

## 9. Next recommended task

**M1.1 — Repository skeleton, Docker Compose core, health endpoint, CI**
([`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md#m11--repository-skeleton-compose-core-health-endpoint-ci))

It is the smallest useful increment: it ends with `docker compose --profile core up` producing a reachable `/healthz`, a passing test suite, and lint, format, type-check, dependency-audit and secret-scan gates enforced in CI. Every later milestone inherits that verification loop. It touches no authentication, no schema and no integration, so it cannot be blocked by an unresolved open decision.

**Before starting:**

1. ~~`git init` and commit the current documentation set.~~ Done 2026-10-13 (`d7dad3d`, pushed to `origin/main`).
2. Resolve `OPEN-06` (secret store) so `core/config.py` has a target interface — a working default is acceptable.
3. Choose a Python package manager (`TS-03`), which fixes the command prefix in [`docs/TESTING.md`](docs/TESTING.md) §16.

**Do not start M1.5 (inventory) until `OPEN-02` is confirmed** — it determines whether `applications` carries an `environment` column, and therefore the shape of both the schema and the applications API.

---

## 10. Scope confirmation for this session

This session was documentation-only. Specifically, it did **not**:

- implement any frontend or backend code;
- generate application source code of any kind;
- create database migrations or a database;
- install any package or dependency;
- provision any cloud resource;
- connect to any cloud account, production or otherwise;
- create any credential or secret;
- claim any integration is operational;
- delete or overwrite any pre-existing project file (only `PRD.md` existed, and it was read, not modified).

Created: `CLAUDE.md`, `PROGRESS.md`, and seven documents under `docs/`. All Mermaid diagrams, tables, JSON examples and checklists are documentation content; every JSON example uses placeholder data.

---

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
