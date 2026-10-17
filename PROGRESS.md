# PROGRESS — DevOps Control Plane

**Purpose:** preserve development state between AI coding sessions. Read this first; update it last.
**Last updated:** 2026-10-16
**Updated by:** the **M2.3 implementation session (target discovery and agent
health)**. Read [§3](#3-in-progress-milestone) first. The shortest version: registering
a server now makes it a scraped Prometheus target with no config edit, and
`agent_status` / `last_seen_at` are maintained from Prometheus's own `up` — proven
against real systems by the smoke test, which registers the exporter through the API,
stops it, watches the server go `UNREACHABLE`, restarts it and watches it recover.
Five decisions were closed with the user before code (`OPEN-14` → `file_sd` with **no
reload call**; a target policy; asynchronous registration-time verification; probe
source and who writes `last_seen_at`; the static dev target removed). The things most
worth knowing: **migration 0005** exists because a probe writing every 30 s would have
made every `If-Unmodified-Since` edit a spurious `409`; the smoke test took
seven runs and six failed — one a Dockerfile bug of mine, the rest the *script's* (Git
Bash rewriting `/var/…`, a silent `set -e`, a race, a leftover row) — each recorded in
[§7](#7-tests-executed-and-their-actual-results); and the changed-line coverage gate
crashed on a Windows decoding error (issue **65**, fixed, floor untouched).

Before it, the **M2.2 implementation session (host metrics endpoints and
charts)**. Read [§3](#3-in-progress-milestone) first. The shortest version: the
product now shows real per-server CPU, memory, disk, load, network and agent state
from a real Prometheus, with a value-or-a-reason rule enforced at every layer, and
four decisions were closed with the user *before* code (thresholds are catalogue
defaults only — `OPEN-28`; `stale` is a sample-age bound below Prometheus's lookback;
an environment comparison is one series per server and refuses over 20 rather than
truncating; the dashboard's `utilisation` section was deliberately **not** flipped).
The assumption most worth knowing was **measured, not reasoned**: `collected_at`
rests on `timestamp(up{…})` returning the *sample's* time, and the smoke test now
asserts that against a real Prometheus (6.7 s before evaluation; if it returned
evaluation time `stale` could never fire). Issue **59** (opened in M2.1 as an unidentified
flake) recurred and is now mitigated: the frontend suite was timing-sensitive under load.

Before it, the **M2.1 implementation session (Prometheus client and metric
templates)**, which then also resolved **`TS-01`** — the charting library — because
M2.2 was blocked on it and §9 said to decide it with a test in hand rather than
under that milestone's deadline. The answer is **`d3-shape` + `d3-scale`, used
directly with no React wrapper**; the reasoning, the measurements and the three
rejected alternatives are in
[`docs/TECH_STACK.md`](docs/TECH_STACK.md#25-charting-library) §2.5, and the gap test
the decision rests on is kept as a standing regression test.

**M2.1 itself** was the first milestone of Phase 2, and the first time this product
reads a system it did not also write. Read [§3](#3-in-progress-milestone) first. The two
things most worth knowing before touching anything:

1. **The changed-line coverage gate was reporting a pass it had not earned.** M2.1
   is the first backend diff it ever judged, and its first run printed
   `17/21 changed lines covered (81.0%)` while skipping the **entire new
   `app/metrics/` package** — `git diff` cannot see untracked files. The M1.7
   handover predicted the Cobertura path matching would be the risk; that part was
   fine, and the real defect was worse, because a new module is the most likely
   shape of an untested addition. Fixed, floor untouched, same diff then measured
   **388 lines at 100 %**. Issue **56**.
2. **`DCP_PROMETHEUS_URL` deliberately does not go through
   `app/core/ssrf.validate_registered_url`,** and the reasoning is written out in
   `app/metrics/client.py` rather than left implicit — chiefly that §19.4's
   denied-range check would reject the only addresses an internal Prometheus can
   have, and that gating it would force `DCP_SSRF_HOST_ALLOWLIST=prometheus` as a
   shipped default, widening the *user-supplied* surface to admit an
   *operator-supplied* destination. The allowlist is unchanged and still empty, so
   issue **45** is unaffected.

Also: **`OPEN-09` now has an implemented working default** (Windows is
`UNSUPPORTED`, expressed structurally so "supported but empty" cannot be spelled),
**docs/TESTING.md §11 step 8's Prometheus half is asserted** for the first time, and
issues **57** and **58** are new while **54**'s count was corrected from five to
eleven. The dashboard's `utilisation` section was **not** flipped: M2.1 ships no
metric endpoint, so it has no source until M2.2.

Before it, the **M1.7 implementation session (Phase 1 consolidation)**. The
milestone's own deliverables — a Compose smoke test and a first E2E suite — found
**four faults that had been latent for five milestones**, which is the argument for
consolidation milestones existing. Read [§3](#3-in-progress-milestone) first; the
short version is that *nothing applied the migrations*, so the documented path from
clone to working stack had never worked on a machine that did not already have a
populated volume. Issues **20** and **49** are RESOLVED, and three new ones (**52**,
**53**, **54**) are recorded. Before it, the issue 44 session — the **server edit form
and association management UI**, taken deliberately before M1.7 so that milestone's
first E2E suite is not written around a missing page. §14.4, §14.6 and §14.7 now have controls.
**No backend file changed**, and the evidence is that `npm run generate:api`
regenerated `frontend/src/api/schema.ts` with no diff. Read the **issue 44 row in
[§6](#6-known-issues)** first: it records the four decisions the contract leaves to a
client — how a cleared optional field is expressed, why immutable fields are absent
rather than disabled, how an environment change is confirmed, and why a `409` is
shown and never retried — plus the one limitation the advisory permission list
cannot avoid. Before it, the issue 51 fix session (order-dependent audit-row
assertions), which changed only that issue's rows here. Before that, M1.6
implementation session
(dashboard shell, database-only): the three decisions taken before the milestone —
add `openapi-typescript` first, build paging controls, leave the server edit form
alone — are implemented as agreed. The thing to read first is the **two shape
divergences from [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §13** that honest
data states forced. **Issue 51** — the pre-existing order-dependence in two M1.3/M1.4
integration tests that this milestone's extra audit volume exposed — is **RESOLVED**;
its row is worth reading before the M1.7 E2E suite, because the fix was not the
tiebreaker first proposed (see [§3](#3-in-progress-milestone) and [§6](#6-known-issues)).
Previously: M1.5 implementation session (server and application inventory), which
amended `A-09` and opened `OPEN-27`; before that, M1.4 (RBAC and authorisation
enforcement) and M1.3

> **Rules for this file**
> - Record only what has **actually happened**. Never mark anything complete because a plan says it should be.
> - Record real test output, with the date it was run. "Should pass" is not a result.
> - Record integrations as *configured* only when a real connection has been made and verified.
> - Keep entries dated and append-only where practical, so history is not lost.

---

## 1. Current project stage

**Stage: Phase 2 — Monitoring. M1.1 – M1.7 and M2.1 – M2.4 complete. M2.5 (stale-data detection) is next. As of M2.4 a registered unit on a scraped host shows its real systemd state, host-aware: a dead host is `UNKNOWN`, never `INACTIVE`. The dashboard's `utilisation` section is still
`not_configured`.**

**As of M2.1 (2026-10-16) the product reads something other than its own
database.** Prometheus and `node-exporter` are configured, running and scraped, and
the backend queries them through an allowlisted template catalogue it renders
itself — no endpoint accepts PromQL (AD-04). That makes Prometheus the first entry
in [§8](#8-external-integrations) that is not "declared, never started", and it is
the first time this codebase has been shown to work against a real external system
rather than against a mock it also wrote.

What it is **not**: there is still no metric *endpoint*. §15.1–§15.3 and every chart
are M2.2, so the dashboard's `utilisation` section still reports `not_configured`
with nulls and was deliberately left that way — flipping it would mean claiming a
source the API cannot yet serve. Of the dashboard's nine sections, still exactly two
have one.

The application has a running skeleton, a migrated PostgreSQL schema, working OIDC
authentication with server-side sessions, authorisation (one matcher, one
`require_permission` dependency, `AUTHZ_DENIED` auditing, and a route-coverage test
that fails the build if any route escapes a permission), and — as of M1.5 — a
**server and application inventory**: twelve endpoints, scope filtering applied
inside the SQL, cursor pagination, row-level idempotency, and five routed frontend
pages. As of M1.6 it also has a **dashboard shell over PostgreSQL**: endpoints §13.1
and §13.2, a scope-keyed Redis cache, and a landing page whose every section states
which source it came from or which milestone will give it one.

As of the **issue 44** change (2026-10-15) the inventory UI is no longer
read-and-register only. A server's mutable fields (§14.4) are editable at
`/servers/:serverId/edit`, and its application associations (§14.6, §14.7) are
managed from the server detail page — which is what M1.7's E2E suite was written
against, rather than around.

**M1.7 (2026-10-15) made the stack reproducible rather than adding a feature.** It
has a Compose smoke test that passes, a first Playwright suite that passes, a
changed-line coverage gate, and a `.env.example` that is checked against the code.
What it mostly did, though, was discover that the documented path from a clone to a
working stack had three independent breakages in it, all hidden by the same
accident: the only machine that had ever run the stack had a PostgreSQL volume that
outlived every `docker compose up`, so nobody had started from nothing since M1.2.

It still monitors nothing — see [§4](#4-pending-milestones). That is the point of
M1.6 rather than a gap in it: of the dashboard's nine sections exactly two have a
source (server status counts and the failed-unit count, both from PostgreSQL), and
the other seven report `not_configured` with `null` values rather than zeros.

M1.5 is where the authorisation layer first met a real scoped row, and it found a
defect: under `A-09` as M1.4 wrote it, the bootstrap administrator's full-scope grant
reached **no server at all**. See [§3](#3-in-progress-milestone).

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

| **M1.5** — server and application inventory, endpoints 14.1–14.7 and 18.1–18.5, scope-filtered queries, cursor pagination, idempotency, React Router and five pages | **Complete** | 2026-10-14 | 349 unit + 209 integration + 55 frontend tests pass against **real PostgreSQL 17.2 and real Redis**; the lifecycle driven over HTTP against the running Compose stack (register → replay → duplicate → patch → environment change → retire → 404) and the UI exercised in a real browser; all 14 checks in `scripts/check-all.sh` pass ([§7](#7-tests-executed-and-their-actual-results)) |

| **M1.6** — dashboard shell: endpoints 13.1 and 13.2, scope-keyed Redis cache, shared `StatusBadge`/`DataStateBanner`/`EmptyState`/`ErrorState`, paging controls, generated TypeScript API types | **Complete** | 2026-10-15 | 360 unit + 231 integration + 76 frontend tests pass against **real PostgreSQL 17.2 and real Redis**; both endpoints driven over HTTP against the running Compose stack and the counts cross-checked by hand against `SELECT count(*)`; `scripts/check-all.sh` passes all 15 checks ([§7](#7-tests-executed-and-their-actual-results)). Both security checks were confirmed to **fail** against a deliberately broken implementation before being relied on |

| **M1.7** — Phase 1 consolidation: Compose smoke test, first Playwright suite, changed-line coverage gate, `.env.example` check | **Complete** | 2026-10-15 | `scripts/smoke-test.sh` passed twice including once from empty volumes; 11 E2E tests, five consecutive clean runs; all 18 checks in `scripts/check-all.sh` pass. Its own deliverables found **four faults latent for five milestones**, chiefly that *nothing applied the migrations* ([§3](#3-in-progress-milestone)) |

| **M2.1** — Prometheus client, metric template catalogue, query guards, `GET /metrics/templates`, §26.1 integration status | **Complete** | 2026-10-16 | 496 unit + 237 integration + 115 frontend tests pass against **real PostgreSQL 17.2 and real Redis**; `scripts/check-all.sh` passes all 18 checks with nothing skipped; `scripts/smoke-test.sh` passes with **§11 step 8's Prometheus half now asserted** — the exporter target `up` and **all ten templates executed**, read **through the backend's own client against a real Prometheus and a real `node_exporter`** ([§7](#7-tests-executed-and-their-actual-results)). Changed-line coverage **388/388 (100 %)** after the gate's own blind spot was fixed (issue 56) |
| **M2.2** — host metrics endpoints §15.1–§15.3, threshold classification, metric cards, range charts, server/environment comparison | **Complete** | 2026-10-16 | 539 unit + 272 integration + 170 frontend tests pass against **real PostgreSQL 17.2 and real Redis**; `scripts/check-all.sh` passes all 18 checks; `scripts/smoke-test.sh` passes with step 18 extended to read **current values and a real range series through the new service against a real Prometheus and `node_exporter`**, with the scrape-timestamp assumption verified; both pages were then looked at in a real browser on the running stack ([§7](#7-tests-executed-and-their-actual-results)). Changed-line coverage 316/324 backend, 249/268 frontend |
| **M2.3** — Prometheus target discovery (`OPEN-14`), agent-health probe, `agent_status` / `last_seen_at`, migration 0005 | **Complete** | 2026-10-16 | 586 unit + 296 integration + 176 frontend tests pass against **real PostgreSQL 17.2 and real Redis**; `scripts/check-all.sh` passes all 18 checks; `scripts/smoke-test.sh` passes with step 18 **registering the exporter through the API, watching it become `HEALTHY`, stopping it until `UNREACHABLE`, and restarting it** — all read from a real Prometheus and `node_exporter`; the new UI row was viewed in a real browser through both transitions ([§7](#7-tests-executed-and-their-actual-results)). Changed-line coverage 213/215 backend |
| **M2.4** — systemd service monitoring (`TS-02`, `OPEN-29`): state normalisation, endpoints §16.1–§16.7, refresh job, honest failed-services count, services pages | **Complete, with the caveats in [§6](#6-known-issues) issues 69–80** | 2026-10-17 | 777 unit + 365 integration + 211 frontend tests pass against **real PostgreSQL 17.2 and real Redis**; `scripts/check-all.sh` passes all 18 checks with nothing skipped (changed-line coverage 585/638 backend and 69/73 frontend, both above the 80% floor and understated for the backend — issue 80); `scripts/smoke-test.sh` passes (20 steps, from empty volumes) with the new **step 18b driving real transitions on a real systemd** and asserting what the portal says: a killed unit is `FAILED`, a cleanly stopped one `INACTIVE`, a unit the host does not have `UNCOLLECTED`, a failing system bus `UNCOLLECTED` with the host still reachable, and **a stopped host `UNKNOWN` for every unit — none `INACTIVE`** ([§7](#7-tests-executed-and-their-actual-results)). **Not observed on a hosted CI runner, and the UI was not viewed in a real browser.** |

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

**None.** M2.4 is finished. M2.5 has not begun. See [§9](#9-next-recommended-task).

### What M2.4 implemented (2026-10-17)

A pure normalisation module, a read path, a 60-second refresh job, an honest dashboard count, three pages, and a container that runs systemd. **No migration**: the `services` columns M1.2 created were enough. Seven commits on `phase-M2.4`.

#### The decisions closed before code

1. **`TS-02` → `node_exporter`'s systemd collector with a unit allowlist**, chosen after **testing it against a real systemd 255** (pinned `node-exporter:v1.8.2`, non-root, system bus mounted read-only) rather than from documentation — the way `TS-01` was. No new component, port, scrape job or `file_sd` kind; `node_scrape_collector_success{collector="systemd"}` is the signal for a collector that is not working. `systemd_exporter` was rejected (same series, a second container and discovery kind, all 19 metrics documented UNSTABLE, last release 2025-03-14); the agent was rejected as out of proportion ([TECH_STACK.md §5.3](docs/TECH_STACK.md#53-systemd-metrics-integration)).
2. **`OPEN-29` → `sub_state`, `load_state`, enabled state and main PID are `null`.** The test found that **no exporter exposes them**, though ARCHITECTURE §6.1 and PRD §4.5 name them. Agreed with the product owner: `null` (never `0`, `""` or a guess), `active/exited` reads `ACTIVE`, and the UI says "not collected by this source". The restricted agent (`OPEN-05`) fills them later with no API change. `last_state_change_at` is `null` for the same reason; the exporter's start time is exposed as **`started_at`** under its own name.
3. **Lists read a cache; detail reads Prometheus** (agreed: no migration). `restart_count` is `null` on lists.
4. **§16.7 is routed and validated and answers `503 not_configured`** until Loki exists (M3.1). An empty page would read as "no errors". The permission is `logs:query`, the seeded spelling of `logs:read:system`.
5. **An action policy is a privilege**: assigning or changing one is Administrator-only and audited as `SERVICE_ACTION_POLICY_CHANGED` as well as `SERVICE_UPDATED`.

#### What was built

- `app/services/normalise.py` — [ARCHITECTURE §6.1](docs/ARCHITECTURE.md#61-monitoring-read) as a pure function with an explicit order: unsupported platform → monitoring gates → Prometheus failure → **the host (overrides unit state)** → collector/unit not reporting (`UNCOLLECTED`) → `active_state`. A malformed unit (none, two or an unknown label set to 1, a `NaN`) fails closed to `UNKNOWN`. `view_cached` is the same order for the list path.
- `queries.py`, `reader.py` — allowlisted PromQL (unit names come only from registered rows, validated with a **full match**, refused rather than escaped); absence stays absence; a unit registered on one host cannot leak into another's reading; scrape age is measured against **Prometheus's** clock, not ours.
- `router.py`, `service.py`, `schemas.py`, `listing.py` — endpoints 16.1–16.7. `displayed_state` is `view_cached` as a SQL `CASE`, so `?state=FAILED` filters on what a row *shows*; **a parity test runs 1 152 generated rows through both and fails if they ever disagree**.
- `refresh.py` + `dcp.monitoring.refresh_services` (beat, 60 s) — reads every registered unit on the hosts Prometheus scrapes (the one `select_targets` predicate), writes `services.last_observed_*`, never moves a row backwards, writes `UNKNOWN` over an old `FAILED` when Prometheus cannot be asked. The cache goes stale at 3× the interval, derived from it.
- **Dashboard `failed_services` was wrong since M1.6 and is fixed.** It counted the raw cache column, so a dead host's cached `FAILED` still counted, and an empty cache read as a real zero. It now counts what rows *show* and reports **`unobserved`** beside it, so `0` failed of `12` unread cannot be read as `0` of `12`. The card shows "N not observed".
- Frontend: `/services` (with a state filter sent to the backend), `/servers/:id/services`, `/servers/:id/services/:name`; every row shows its state **and its evidence** (collection time, or why none); an unreachable server renders `Unknown` + "Host unreachable", never `Inactive`; the four unsupplied fields read "Not collected by this source".
- `infra/compose/systemd-host/` + the `fixtures` Compose profile — Debian with systemd as PID 1, the *same pinned exporter binary*, the systemd collector, and four units. Its security exception is written down in [SECURITY.md §7](docs/SECURITY.md#7-network-isolation).
- `scripts/smoke-test.sh` **step 18b** with `SMOKE_SYSTEMD=auto|require|skip`; a fixture that cannot start ends the run as `PASSED, BUT INCOMPLETE … NOT RUN`, never as a plain pass.

#### What M2.4 found, and what became of each

| Found | Handling |
|---|---|
| **No exporter exposes `sub_state`/`load_state`/enabled/PID** | `OPEN-29`, above |
| **`TS-02` text said DCP generates the exporter's allowlist.** It cannot: it has no write path to a host's `node_exporter` flags and must not gain one | Corrected in TECH_STACK §5.3 and the plan before any code; the exporter-side allowlist is host configuration, DCP filters on read |
| **The unit-name pattern's `$` accepts a trailing newline** with `re.match` | `fullmatch` in the query renderer and the request schema; tested with `"a.service\n"` |
| **A stopped unit and a failed unit report different things; a start time of `0` means "not running"**, not 1970 | `INACTIVE` ≠ `FAILED`; `0` is `null`; both asserted against a real systemd |
| **The plan's "stop the exporter ⇒ `UNCOLLECTED`" cannot happen** with the collector inside `node_exporter`: a stopped exporter *is* an unreachable host to Prometheus | `UNCOLLECTED` is the collector failing (stopping the system bus); recorded in [TESTING.md §11](docs/TESTING.md#11-docker-compose-smoke-tests) |
| **systemd in a container needs `SYS_ADMIN`, `cgroup: host` and a writable `/sys/fs/cgroup`** — four weaker combinations were tried and exit at boot | Confined to an opt-in profile; documented as an exception (issue 69) |
| **M1.6's dashboard count read the raw cache column** | Fixed above |

### What M2.3 implemented (2026-10-16)

Discovery, an agent probe, one migration, four new settings, a volume, and one row in the UI.

#### The five decisions closed before code

1. **`OPEN-14` → DCP-generated `file_sd`, and no reload call.** The plan said "trigger a
   reload"; `file_sd` watches its files, so none is needed, and the flag that would
   provide one (`--web.enable-lifecycle`) also enables an unauthenticated `/-/quit`. It
   was **removed** from the Prometheus container. The file lives on a named volume the
   worker writes and Prometheus mounts read-only, with `refresh_interval: 15s` as the
   fallback for filesystems without change events.
2. **What a target may point at is a policy** (`discovery.target_problem`), applied at
   the write boundary and again when the file is built: port allowlist (default
   `9100`), no loopback / link-local / unspecified / multicast / reserved IP literals
   — including the IPv4-mapped IPv6 spelling of the metadata address — and no
   `.invalid`. The last one matters for the seeded `[DEMO]` servers, whose
   `*.demo.invalid:9100` would otherwise be scraped, fail, and be reported
   `UNREACHABLE`. **A DNS name can still resolve somewhere it should not**; that is the
   monitoring network's egress policy, recorded in SECURITY §19 as a limitation.
3. **Registration-time verification is asynchronous.** ARCHITECTURE §4 point 1 said
   registration validates that the label resolves to a series; a new target needs a file
   write plus a scrape (15–30 s), so a synchronous check would always fail. The point
   was **amended** and the reason written there. Corollary worth knowing: because DCP
   now writes `targets: [metrics_instance]`, the `instance` label *is* the stored value
   and cannot mismatch — so M2.2's blunt `no_data` now means "not loaded yet" or
   "Prometheus is unavailable", never "wrong label".
4. **The probe reads Prometheus's own `up`**, not a connection DCP opens to the host,
   and **writes `last_seen_at`** (the latest *successful* scrape, only ever forward) —
   the field M2.5's stale sweep reads. It emits `HEALTHY`, `UNREACHABLE` and `UNKNOWN`;
   `DEGRADED` and `NOT_INSTALLED` are **not emitted**, for the reason M2.1 declined
   `degraded`: nothing can decide them honestly.
5. **The static dev target is gone.** `prometheus.yml` no longer lists
   `node-exporter:9100`; a developer registers it like any host, and the smoke test
   does exactly that — which makes it a test of the acceptance criterion rather than of
   a hand-edited config.

#### The thing nobody asked about: migration 0005

The `updated_at` trigger from 0001 fires on every UPDATE of every row. A probe writing
`agent_status` / `last_seen_at` every 30 s would therefore have bumped `updated_at`
continuously, and §14.4 makes `PATCH /servers/{id}` conditional on
`If-Unmodified-Since`: an administrator opening the edit form and saving 30 s later
would have been refused with `409` because *Prometheus had been scraped*. Found while
reading the trigger, **before** it bit. The replacement trigger compares the row with
everything except those two columns (as `to_jsonb`, so a column added later is included
by default — the failure mode is a loud `409`, not a silent lost update) and only then
advances `updated_at`. Taken without a round trip because the alternative was shipping a
broken contract; it is easy to reverse (the downgrade restores the generic trigger) and
documented in DATABASE.md. `status` is deliberately *not* excluded: administrators edit
it too, and the trigger cannot tell who wrote it. **M2.5 owns that.**

#### What the code does

- **One predicate for the file and the probe** (`discovery.select_targets`: monitored,
  not retired or deleted, Linux, permitted). If they could disagree, `agent_status`
  would describe a host nothing scrapes. A server that stops being a target has its
  `HEALTHY` / `UNREACHABLE` reset to `UNKNOWN`; a status the probe does not own is never
  overwritten.
- **The file** is deterministic (same inventory, same bytes), written atomically (temp
  file in the same directory, then rename), skipped when unchanged, and built under a
  Postgres advisory lock so two regenerations cannot leave the older snapshot on disk.
  A failure while writing keeps the previous file — an empty one would drop every
  target at once. It holds `host:port`, `environment` and `server_id`; **never** a
  display name or owner team, which are operator-typed text (asserted with hostile
  values).
- **The probe** asks `max_over_time(up[W])`, which is 1 if any scrape in the last `W`
  seconds worked: that window *is* the hysteresis, and no state is kept between probes.
  It never asserts what it could not observe — if Prometheus cannot be asked, every
  monitored server becomes `UNKNOWN` rather than keeping a `HEALTHY` it can no longer
  vouch for.
- **Triggers.** After the inventory write has committed, best effort, with no arguments
  (CLAUDE.md §5) and `retry=False` so a dead broker cannot stall the request; a 5-minute
  beat entry heals a missed enqueue. Only edits touching `metrics_instance`,
  `monitoring_enabled`, `environment` or `status` enqueue anything, and an idempotent
  replay enqueues nothing.
- **A seeded row stays editable.** Only a *changed* `metrics_instance` is policed, so
  resubmitting a demo server's `.invalid` instance is not refused.

#### What was verified against the real thing

`scripts/smoke-test.sh` step 18, from a clean volume, after the exporter is registered
through the API: the target file lists it and lists neither a demo server nor free text;
the worker sees the tightened intervals (the Compose-passthrough half of issue 54);
`agent_up`, the scrape timestamp (**3.9 s before evaluation**, again confirming
`timestamp()` returns the sample's time), current values and a real range series all
come back through the new service; the probe marks it `HEALTHY` with a real
`last_seen_at`; **6 seeded demo servers stay `UNKNOWN`**; stopping the exporter makes it
`UNREACHABLE`; starting it makes it `HEALTHY` again. Then, in a real browser: the new
row read *"Unknown — waiting for the first scrape"*, then *Healthy* with a real last-seen
and `Stale: false`, then — about 34 s after stopping the exporter — *Unreachable* with
the last-seen time kept; and a registration naming the metadata address or port 22 was
refused with a field error that did not echo the value.

#### What was not done

- **No E2E path** (issue 60 still stands; the new row is covered by component tests and
  by the manual browser check, which is not a regression test).
- **The Windows exporter** (`OPEN-09`): Windows servers are not targets.
- **Alert rules for exporter absence** — M3.3, as the plan said.
- **A multi-host delivery mechanism** for the target file. A shared volume is a
  single-host arrangement (`OPEN-12`).

#### Two stale pieces of copy found by looking, again

The server detail's Health row said "Metrics collection is not implemented yet (M2)" —
false since M2.1 — and the dashboard's utilisation reason said the same, labelled
`(M2.1)`. Both now say what is actually missing (health *evaluation*, M2.5; a fleet
aggregate). Copy only; no behaviour changed.

### What M2.2 implemented (2026-10-16)

Three read endpoints, one pure threshold module, one chart component, two pages. No
migration (the schema stays at `0004`), no new dependency, no new setting.

#### The four decisions closed before code

1. **Thresholds are the catalogue defaults only** (CPU 80/90, memory 85/95, disk
   85/95; `null` for the other seven). No column, no setting. **`OPEN-28`** records the
   alternatives (per-environment settings, a per-server table) and says to decide
   before M3.3, whose alert rules need the same numbers and must not hold a second copy.
2. **`stale` is a sample-age bound of 60 s**, a constant in
   `app/metrics/thresholds.py`, deliberately *below* Prometheus's 5-minute lookback.
   Above the lookback a series simply vanishes and the reading is `unavailable`, so a
   bound at 300 s would have been unreachable. Host-level staleness (AD-14,
   per-environment) stays M2.5.
3. **An environment comparison is one series per server**, ordered by display name,
   and over 20 *in-scope* servers it is `422 query_limit_exceeded` rather than
   truncated. This was **observed, not assumed**: the integration suite's shared
   database held 75 servers by the time the environment test ran and the cap refused
   it — the behaviour the decision asked for.
4. **The dashboard's `utilisation` section was not flipped.** A fleet-wide aggregate is
   not in M2.2's task list and cuts against AD-04's per-server query posture.

#### What the code does

- **A reading is a value or a reason.** `app/metrics/readings.py` returns, for every
  template, a value with its threshold state, or one of `not_configured` /
  `unsupported` / `unavailable` / `stale` with a closed `unavailable_reason`. A NaN, an
  empty result, two samples for one instance, a Prometheus 5xx, a timeout and a
  Windows server each produce a *reason*; none produces a zero. A `stale` reading
  carries no value either.
- **`collected_at` is the host's latest scrape**, from `timestamp(up{…})`
  (`templates.render_scrape_timestamp`), because an instant query stamps every result
  with its own evaluation time. Age is computed from Prometheus's two timestamps, so
  this host's clock cannot skew it.
- **Ranges come back on the full requested grid** with `null` at every hole; a series
  that is all holes is `unavailable`, not a flat line. The rate window widens to the
  step. One range query serves a whole comparison, mapped back by `instance` label.
- **One failing query does not discard its siblings**, and a *bug* is not mistaken for
  an outage: only `PrometheusUnavailableError` becomes a data state, asserted by a test.
- **Scope** is `metrics:read` against the server target: after the row loads for a
  single server, inside the SQL (`readable_servers`) for a comparison. Out of scope is
  `404`, identical to an unknown id. `excluded_count` counts requested ids that did not
  resolve; it is always `0` for an environment selection, because a real count would
  disclose how many servers exist outside the caller's scope (issue 64).
- **§15.3 is a `POST` that reads**, so it is exempt from read-only mode
  (`allow_in_read_only`); the docstring that said "exactly one route" was updated.
- **Frontend.** `LineChart` is geometry from `linePath` plus axes, UTC ticks
  (`ticks.ts`), threshold lines labelled in text, a crosshair that follows the pointer
  *and* the arrow keys, a legend with dash patterns (identity is not colour alone), and
  a "View as table" fallback rendered only when opened. Categorical colours are eight
  `--series-*` tokens in fixed order; a server keeps its slot for the life of the page;
  at most eight lines are plotted and the rest are listed as not plotted. An isolated
  reading between two holes is drawn as a dot, because a one-point subpath draws
  nothing and would otherwise lose it.

#### What was verified against the real thing

`scripts/smoke-test.sh` step 18 now runs, inside the backend container against a real
Prometheus and `node_exporter`: the scrape-timestamp query (age 6.7 s — a strictly
positive age is the signature of a sample time; evaluation time would be exactly 0),
`readings.current` for three templates (all `ok`, all dated), and `readings.history`
over ten minutes at 15 s — **41 points, 40 holes, none zero-filled** on a cold stack,
a real leading gap produced by a real Prometheus rather than a fixture. Then both pages
were opened in a real browser against the running stack with the exporter registered as
a server: the cards showed real values with UTC collection times and the charts drew
the one real point after an empty stretch.

#### What was not done

- **No E2E path.** §12 path 3 is not written, so no Playwright run covers the new
  pages; the browser check above was manual and is not a regression test (issue 60).
- **No fleet aggregate on the dashboard**, per decision 4 (issue 61).
- **`stale` has never been observed against a real Prometheus**; scripted tests only
  (issue 62).

#### One thing found by looking

The app shell's banner still said "Milestone M1.6 … Metrics … not implemented yet" —
copy that had been wrong since M1.7 and was false once this milestone shipped. Found
only by opening the page. Updated, along with the README's claims that the product
"monitors nothing" and has no Prometheus integration.

### What M2.1 implemented (2026-10-16)

Phase 2's first milestone, and the first time this product reads a system it did not
write. Six new backend files, two real Compose services, two new settings, no
migration — `metrics:read` and `admin:integrations` were already seeded by migrations
`0002` and `0004`, so the schema stays at `0004`.

#### The two decisions taken before any code, both recorded rather than assumed

**1. `OPEN-09` — Windows is `UNSUPPORTED`, and that is expressed structurally.**
A `MetricTemplate` holds a mapping of OS family to PromQL, and there is no way to
spell "supported but has no query". Windows families are therefore simply **absent**,
and `render()` raises `UnsupportedOsFamilyError` for them. The reasoning is that
`windows_exporter` cannot run in a Linux container, so any Windows PromQL shipped
now would be a string that has never been executed against anything — which is worse
than an honest refusal, because it reads as coverage. A WINDOWS server will show the
`unsupported` data state, not an empty chart and not a Linux query pointed at a
Windows host. **The decision stays open**; this is its working default, now
implemented and tested rather than merely written down.

**2. `DCP_PROMETHEUS_URL` does not pass through `app/core/ssrf.validate_registered_url`.**
This was the question most worth getting wrong, because mandatory rule 9 says
outbound destinations are checked against the host allowlist and a literal reading
covers this. The answer, argued in full in `app/metrics/client.py`'s module
docstring, is no, for three reasons that point the same way:

- `validate_registered_url` is **write-time validation of a URL that arrived in an
  API request** — its own docstring says so, and its callers are §18.2's
  `health_check_config` and `repository_reference`. `DCP_PROMETHEUS_URL` arrives in
  the process environment from the deployment platform, exactly like
  `DCP_DATABASE_URL`, `DCP_REDIS_URL` and `DCP_OIDC_ISSUER`, none of which is
  allowlisted either. An allowlist cannot constrain whoever also sets the allowlist.
- [SECURITY.md §19](docs/SECURITY.md#19-ssrf)'s rule 4 — the denied-range check over
  loopback, link-local and RFC1918 — would **reject the only addresses an internal
  Prometheus can have.** The local stack reaches it at `prometheus:9090` on a private
  Docker network. That rule exists so a *user*-supplied destination cannot be steered
  at the cloud metadata endpoint; pointed at an operator-configured monitoring host
  it protects nothing and breaks everything.
- Gating it would force `DCP_SSRF_HOST_ALLOWLIST=prometheus` as the shipped default
  (the compose default and `.env.example` must agree, which
  `scripts/check_env_example.py` rule 5 enforces). That list governs the
  **user-supplied** surface, so admitting an operator-supplied destination to it
  would make `http://prometheus:9090` a registrable application health-check target
  as a side effect. **That is issue 45 in reverse** — there an empty allowlist made a
  legitimate write impossible; here a populated one would make an illegitimate write
  possible — and it is a net loss of control.

So the allowlist is **unchanged and still empty by default**, issue 45 is unaffected,
and what §19 asks of a fetch is enforced where it belongs: scheme, no embedded
credentials, no fragment and no whitespace at **load time** (a shared field validator
in `app/core/config.py`, which now also covers the two OIDC URLs — both previously
accepted a credential and a fragment); `https` required outside the `local`
environment; redirects not followed; the response size bounded at 16 MB; and a
deadline on every call. One test asserts the destination comes from settings and
nowhere else, by recording the URL the transport was actually handed.

#### What the code does

**`app/metrics/templates.py` — the catalogue.** The ten templates AD-04 names, LINUX
only. Two details worth knowing before editing it:

- **Escaping is two layers in one specific order.** A value bound for
  `instance=~"…"` is regex-escaped *first* and PromQL-string-escaped *second*,
  because the regex escape introduces backslashes the string literal must then
  carry. The other order emits `"node\-exporter"`, which is not a valid PromQL
  escape sequence; doing only one of the two lets a `.` in a hostname match any
  character, which silently attributes another host's metrics to this server.
- **An empty instance list is refused, not rendered.** `agent_up` carries
  `job="node"`, so an empty selector would produce `up{job="node",}` — which parses
  and returns *every host in the estate*. That is the cardinality abuse AD-04 exists
  to prevent, reached by passing an empty list, so it fails closed.

The instance label is re-validated against `METRICS_INSTANCE_PATTERN` at render time
even though §14.2 validated it on the way in, because a row can also arrive from a
seed, a migration or a direct `UPDATE`. Doing that turned up a defect in the pattern
itself: it was `$`-anchored, and in Python `$` also matches immediately before a
trailing newline, so `"host:9100\n"` satisfied it. Now `\Z`. The six sibling
patterns in the same module have the same slip and were **left alone** (issue 57):
they are stored rather than interpolated, they belong to M1.5's write boundary, and
a tightening of stored-value validation deserves its own diff.

**`app/metrics/client.py` — the read path.** Instant and range reads only. Three
choices that are not the obvious ones:

- **`PrometheusUnavailableError` is a plain exception, not an `AppError`.** If it
  were an `AppError`, a Prometheus 503 escaping a caller would be rendered as a DCP
  503 for a page that is otherwise fine. PRD §4.4 wants the section marked
  unavailable instead, and that only holds if the exception *cannot* be rendered. A
  test asserts both the reason and the non-`AppError`-ness across six upstream
  statuses.
- **The retry is one attempt, for `ConnectError`/`ConnectTimeout` only.** Every call
  here is a read and there is no state-changing provider call in the module to
  blind-retry, so retrying is safe in principle. A *read* timeout is still not
  retried, because the deadline §15.2 publishes is 10 s and retrying past one would
  quietly make it 20. One call also counts as one breaker failure regardless of the
  retry, or the retry would halve the effective threshold.
- **`NaN` and the infinities parse to `None`.** Prometheus returns `"NaN"` from a
  rate window with one sample and `"+Inf"` from a division by zero. Both are ordinary
  answers rather than corruption, `float("nan")` serialises to invalid JSON, and `0`
  would read as "this host is idle" — the substitution PRD §4.2 forbids.

The circuit breaker is five consecutive failures to open, a 30 s cooldown, exactly
one half-open probe, and a failed probe re-opening for a full cooldown rather than
leaving the circuit half-open and probing on every request. Thresholds are module
constants, not settings, deliberately: two more settings would each need a
`.env.example` entry *and* a Compose passthrough to have any effect (issue 54), and
nothing has asked to tune them. It is per-process and marked as such — issue 58.

**`app/metrics/service.py` — guards and status.** Every bound transcribed from
§15.2/§15.3. One error class, `QueryLimitExceededError`, with the crossed maximum in
`details`; `from >= to` is the exception, because ordering is a field relationship
and §8's per-field shape is the useful answer for it. M2.1 ships **no endpoint that
takes a range** — §15.1–§15.3 are M2.2 — so these are written and tested ahead of
their caller on purpose: the milestone that adds the endpoints should not also be the
one deciding the bounds under deadline.

**`app/metrics/router.py` — two routes.** `GET /metrics/templates` (§15.4,
`metrics:read`) and `GET /integrations/status` (§26.1, `admin:integrations`). Both
reject *every* query parameter, since both take none; answering §15.4 with the full
catalogue while `?metrics=cpu_utilisation` was ignored is precisely the §6 failure, a
typo that looks like a working filter. §26.1 was built here rather than deferred
because the plan lists "an integration-status entry for Prometheus" as an M2.1 task,
and an entry with no endpoint to reach it is dead code. It diverges from the
contract in **one** place, recorded in docs/API_CONTRACTS.md §26.1 rather than taken
silently: there is a single `Cloud accounts` row instead of one row per account,
because there is no provider adapter to probe until M4.1 and N rows would carry one
fact N times.

#### What was deliberately not done

- **The dashboard's `utilisation` section was not flipped.** It still reports
  `not_configured` with nulls. M2.1 gives it no source — there is no §15.1 — and
  marking it configured would be claiming one.
- **`degraded` is not emitted on the §26.1 Prometheus row.** It was written, and then
  removed when its own test proved it unreachable: a successful probe resets the
  breaker's consecutive-failure count, so any flap verdict built on that count is
  always false. Saying it honestly needs failure-rate history over a window nobody
  has specified. The row carries both timestamps and the previous failure's reason
  instead, so an operator can see that an `ok` Prometheus failed a minute ago.
- **No target discovery, no `agent_status`, no registration-time `metrics_instance`
  verification** (M2.3, `OPEN-14`); **no systemd** (M2.4, `TS-02`); **no charts**
  (M2.2, `TS-01`). The Prometheus target list is static and the config file says so.

#### The two faults the verification found

**1. The changed-line coverage gate had a blind spot, and M2.1 is the first backend
diff it ever judged.** Its first run reported **`17/21 changed lines covered
(81.0%)` — a pass — while skipping the entire new `app/metrics/` package.**
`scripts/check_coverage_diff.py` finds changed lines with `git diff`, which cannot
report a file git has never been told about, so every untracked file was invisible;
the 21 lines were from `app/core/config.py`, an existing file the branch happened to
touch. The M1.7 handover predicted the Cobertura *path matching* would be the thing
to watch on `app/` paths — it was fine, the existing suffix rule matched
`backend/app/core/config.py` to the report's `app/core/config.py` correctly. The
defect was elsewhere and worse, because a new module is the most likely shape of an
untested addition. Fixed by folding `git ls-files --others --exclude-standard` into
the diff; **the floor was not lowered.** The same diff then measured 388 lines, and
the tests the gate asked for were written: final result `388/388 (100.0%)`. Issue 56.

**2. `METRICS_INSTANCE_PATTERN` accepted a trailing newline**, described above.
Issue 57 records the six siblings that still do.

### What M1.7 implemented (2026-10-15)

#### The four faults it found, which is the point of the milestone

Each was found by a deliverable of this milestone, not by reading code. Each had
been present for several milestones. None was reachable on a machine that already
had a populated volume, which is why five milestones passed over them.

**1. Nothing applied the migrations.** A fresh volume produced a stack of
*healthy* containers over an **empty database**. The container healthcheck is
`/healthz` (liveness); the migration gate is on `/readyz`, which `--wait` does not
consult. So `docker compose up --wait` reported success, `GET /readyz` answered
`503 migrations: false`, and a login could not reach a `users` table. It had worked
since M1.2 only because the first developer ran `alembic upgrade head` by hand and
the volume survived. **Found by `scripts/smoke-test.sh` step 19** (`down -v`, then
`up` again), which is the step that exists precisely to prove there is no hidden
local state. Fixed with a one-shot `migrate` service; `backend`, `worker` and `beat`
all wait on `service_completed_successfully`. No `AD-nn` governs who applies
migrations — this is the local stack's answer only, and a real deployment still runs
it as its own job, one runner rather than N replicas racing.

**2. The root `.env` was never read.** Compose takes its *project directory* from
the compose file's location, so `-f infra/compose/docker-compose.yml` looks for
`infra/compose/.env` and ignores the one at the repository root. The README's very
first instruction, `cp .env.example .env`, was a **no-op**, and anything a developer
edited there — a port, a password — was silently discarded. Every `${VAR}` in the
compose file has a default, which is why the stack always came up correctly and
nobody noticed. Measured directly: `DCP_LOG_LEVEL=WARNING` in a root `.env` still
produced `INFO` in the container, and `WARNING` once `--env-file .env` was passed.
The README now passes it.

**3. Fixing (2) immediately exposed a stale value.** With `.env` finally being read,
`DCP_OIDC_REDIRECT_URL` from `.env.example` — `http://localhost:8000/…`, the value
for running the backend *natively* — overrode the compose default of
`http://localhost:5173/…`, and the login callback landed on the backend, which
serves no SPA. The two files had disagreed for five milestones with no consequence,
because one of them was never read. **Found by following the README on a fresh
clone**, not by any test. `.env.example` now carries the Compose value, and
`scripts/check_env_example.py` grew a fifth rule that fails when the two sources
disagree — demonstrated to catch it by reverting the value.

**4. An unguarded response race in five views.** `ServerList`, `ApplicationList`,
`ApplicationDetail`, `ServerDetail` and the issue-44 association picker all did
`setResult(await load())` with **nothing tying a response to the request that asked
for it**. Change a filter and two requests are in flight; whichever *resolves* last
wins, and the unfiltered one is slower precisely because it returns more rows. So
the list could settle on the answer to a question the operator had already changed,
and stay there. Not theoretical: **§12.2 of the new E2E suite failed about three
runs in eight**, typing a hostname into the search box and getting all 34 rows back,
and it grew *more* likely as the table grew. The dashboard was worse — it refreshes
on a timer, so overlapping requests there are certain rather than possible. Fixed
once, where all five route through, by `useLatestResponse` (a ticket per request;
an answer that is no longer the newest is discarded). Its own test fails against the
previous implementation — verified by deleting the guard and watching
`expected 'unfiltered' to be 'filtered'`.

#### A trap that cost an hour, and will cost the next person one too

**Vite's file watcher does not see edits through the Windows bind mount.** The
`frontend` container mounts `frontend/src` read-only and runs the dev server. After
writing the fix for fault 4, the E2E suite kept failing at the same rate — because
the container was still serving the **pre-fix modules**. The fix only took effect
after `docker compose restart frontend`: before the restart, 3 of 8 probe attempts
showed the unfiltered list; after it, 0 of 8. This means **`npm run test:e2e` can
validate code that is not the code in the working tree**, which is a worse failure
mode than a flake. Recorded as **issue 53**; the E2E section of the README says to
restart the frontend container after changing `src/`.

#### Issue 49, which was two different faults wearing one number

PROGRESS attributed both `worker` and `beat` reporting `unhealthy` to one cause
("a hostname mismatch"). Measured, they were unrelated:

- **`worker`** used exec-form `CMD`, so compose's `$$HOSTNAME` (its escape for a
  literal `$HOSTNAME`) reached celery **verbatim** — there is no shell in exec form
  to expand it — and it pinged a node called `celery@$HOSTNAME`, which exists
  nowhere. The health log showed five retries of `No nodes replied within time
  constraint`, exit 69, on a worker that was answering the whole time: `celery
  inspect ping` by hand returned `celery@afb4637f73ce: OK`. Fixed with `CMD-SHELL`.
- **`beat`** declared **no healthcheck at all**, so it inherited the backend image's
  `HEALTHCHECK curl -fsS http://localhost:8000/healthz`. Beat serves no HTTP, so
  that check could only ever fail — the log showed `Failed to connect to localhost
  port 8000`, for ever.

Beat's is **disabled** rather than replaced, and that was a measurement rather than
a shrug. Beat is the container's main process, so Docker's own running state already
carries "the scheduler is alive"; `celery inspect` reports workers and never beat;
and the schedule file was sampled every 60s and went **270s with no resync** against
a `maxinterval` of 300s, so a staleness check would need a window above five minutes
to avoid calling a healthy scheduler sick — too coarse to be worth the claim, and
unverified right at the boundary that matters. What *can* be checked is checked
where the evidence exists: the smoke test asserts beat's container state and its own
`beat: Starting...` line, and the worker round-trip proves the broker path.

#### What the smoke test covers, and what it does not

`scripts/smoke-test.sh` is numbered to match [`docs/TESTING.md`](docs/TESTING.md)
§11. It covers steps **1–7 and 9–12** and brings up `core` **plus `mocks`**, because
§11.7 is a real login and §11.5 reads inventory through an authenticated request.
Two honest gaps are printed by the script rather than hidden:

- **§11.8 (observability profile) is not asserted.** Prometheus and Loki are
  placeholders until M2.1/M3.1; asserting anything would be presenting a mocked
  integration as operational (CLAUDE.md §7).
- **§11.12 is partial.** "Resource limits present in the production overlay" has
  nothing to check, because **there is no production overlay** — `infra/compose/`
  holds one compose file and the mock IdP. What exists is checked instead: non-root
  (four services), no privileged flag, no added capabilities, `no-new-privileges` on
  every service, and eight services declaring a CPU limit. Recorded as **issue 52**.

It also creates `dcp_test` now, via `infra/compose/postgres-init/`, because the
script ends in `down -v` — which destroyed the hand-created test database and made
the next `pytest -m integration` fail for reasons that looked nothing like the
cause.

#### The E2E suite, and two things that nearly made it worthless

Playwright, `frontend/e2e/`, covering §12 paths **1, 2 and 8**. Paths 3–7 and 9 need
metrics, services, logs, alerts, deployments or approvals and are **not stubbed**: a
test named "server health inspection" that asserted an empty panel would report a
working feature.

1. **A direct API assertion must send an `Origin` header.** `app/core/csrf.py`
   checks origin **before** the route's permission dependency, and also answers
   `403 forbidden` — so `expect(status).toBe(403)` on a request without one passes
   on a *CSRF rejection* and proves nothing about permissions. This was made and
   caught: the 403 arrived carrying `"CSRF validation failed."`. Path 8 now asserts
   the refusal is **not** a CSRF refusal.
2. **The Viewer is granted the same scope as the administrator.** If the Viewer's
   scope were narrower, "a Viewer cannot register a server" would pass because the
   row is out of reach rather than because `VIEWER` lacks `server:write` — and it
   would keep passing if the permission matrix broke.

The suite seeds its own preconditions and is idempotent (inventory, an administrator
via the bootstrap CLI, then a Viewer granted `VIEWER`), and **reuses saved sessions
when they still authenticate**, because signing in is rate limited: the `/auth/*`
class is 10 per 300s and fails closed, and an unconditional login started answering
`429` after a few local re-runs — surfacing as "the mock identity provider heading
was not found", which looks nothing like its cause. `retries: 0`, deliberately:
this project has already paid for a flaky suite once (issue 51).

**Accessibility: contrast is checked here for the first time.** jsdom has no layout,
so the component tests can only ever get "incomplete" from the contrast rule and
disable it explicitly. The E2E suite runs `axe-core` in a real browser across the
dashboard, the server list and the registration form, with no rules disabled.

#### The coverage gate, and how it gates

`scripts/check_coverage_diff.py` implements `OPEN-22`'s working default — **80 % on
changed code** — and gates **per-diff**, which is the part worth being explicit
about:

- It reads a **Cobertura** report plus `git diff`, so one script serves both ends of
  the stack (vitest now emits Cobertura alongside lcov).
- It diffs from the **merge base** to the **working tree**, not `base...HEAD`. The
  first version used `base...HEAD` and silently measured *nothing* for an
  uncommitted change — which is exactly when a contributor runs it locally.
- **Pull requests only.** A push to `main` has no meaningful diff to measure
  against, and gating it would either block the merge commit or compare against an
  arbitrary parent.
- Lines the report does not mention are not counted (a blank line, a comment, an
  `if TYPE_CHECKING:` import), and a diff touching no measurable file passes with a
  message saying so, rather than becoming noise people learn to override.
- Not `diff-cover`: that would be a new dependency for about eighty lines of XML and
  `git diff` parsing (TECH_STACK §10 rung 7). `--cov-fail-under` is a *total*, which
  is the thing TESTING §19 explicitly declines to gate on.

**It failed on this milestone's own diff, at 60 %**, naming the load expressions in
`ApplicationList.tsx` and `ApplicationDetail.tsx` — two pages M1.5 shipped with no
component tests at all. Writing a gate and exempting the first thing it caught would
be pointless, so `Applications.test.tsx` was written (7 tests) and the diff went to
**100 %**. Overall frontend line coverage rose from 88.75 % to 93.77 % as a
side effect, which is not the point and is not a target.

#### The demonstration-mode banner needed no code

It was wired to `demo_mode` in M1.3 and unit-tested there (`SessionProvider.test.tsx`,
"is never silent"). M1.7 verified it rather than rebuilding it, and added the
assertion no unit test can make: the banner **rendering** against a real stack
started with `DCP_DEMO_MODE=true`. The smoke test asserts the flag on the API; the
E2E suite asserts the DOM.

#### What M1.7 did not do

- **No backend code changed at all.** Not one Python file; the backend's 360 unit
  and 231 integration tests are identical to M1.6's, which is the evidence.
- **No migration.** The schema is still at `0004`.
- **Issues 21, 26, 40 and `TS-01` were left alone**, as scoped. They are still open.
- **The `e2e` CI job has not been observed running on GitHub Actions**, only
  locally. See [§7](#7-tests-executed-and-their-actual-results).

### What M1.6 implemented (2026-10-15)

The three decisions taken before the milestone were implemented as agreed:
`openapi-typescript` first, paging controls built once and used in three places, and
the server edit form (issue 44) deliberately left alone for its own change before
M1.7.

#### Honest data states, which is what this milestone is actually about

Of the dashboard's nine sections, **two have a source** and seven do not:

| Section | State at M1.6 | Why |
|---|---|---|
| `servers` (total, pending, managed, stale, unreachable, retired) | `ok`, `postgresql` | Counts of the `servers.status` column |
| `failed_services` | `ok`, `postgresql` — a **real zero** | `services` exists and is empty; it is the allowlist, so "no registered unit is failed" is a fact |
| `server_health` (healthy/warning/critical/unknown) | `not_configured`, all `null` | No collector until M2.1 |
| `utilisation` | `not_configured`, all `null` | No Prometheus |
| `active_alerts` | `not_configured`, all `null` | No `alert_events` table |
| `recent_deployments` / `recent_operations` / `recent_health_changes` | `not_configured`, `items: null` | No tables at all (M5.1 / M4.2 / M2.5) |
| §13.2 per-server `health_state` | `null`, never `healthy` | Nothing has looked at the host |

`failed_services` is the one section permitted to say `0`, and it says so *because*
it has a source. Everything else carries `null` plus an `unavailable_reason` naming
the milestone that will fill it.

#### Two shape divergences from §13's example payload

Both are recorded in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §13 in the same
change, as CLAUDE.md §3 requires. Neither was a free choice — §4 gives one
`data_state` per section, and the example payload could not be produced honestly.

1. **`healthy`/`warning`/`critical` moved out of `servers` into a sibling
   `server_health` section.** The example drew them beside `total` and `managed`, but
   those are counts of a stored column and these are a health rollup with no source.
   One envelope cannot carry one `data_state` for both. Field names are unchanged.
2. **`recent_*` are sections carrying `items: null`, not bare `[]`.** An empty array
   asserts "nothing happened recently". There is no table to ask, and §4 rule 2 plus
   PRD §4.2 forbid a substitute for an absent value as firmly for an empty list as
   for a zero.

A third, smaller one: `health_status` on §13.2 filters a field with no source, so it
returns an empty page rather than being ignored. Dropping it silently would return
the whole list looking like the healthy subset.

#### The cache, and the scope key

`DCP_DASHBOARD_CACHE_TTL_SECONDS` (default 15, `0` disables — the milestone's
documented rollback), auto-refresh via `VITE_DASHBOARD_REFRESH_SECONDS` (default 30).
`collected_at` is stamped after the reads and displayed for every section, so a
cached answer shows its real age.

**The cache key includes the caller's resolved scope**, digested from the
`server:read` grants that feed `readable_servers` — allows and denies both. This was
the milestone's stated security risk ("a cache key that ignores scope leaks counts
across users"), so it has tests that were *demonstrated to catch it*: with
`_scope_fingerprint` stubbed to a constant,
`test_two_scopes_produce_different_cache_keys`,
`test_an_added_deny_changes_the_cache_key` and the end-to-end
`test_a_scope_change_is_not_served_from_the_previous_cache_entry` all fail. The
end-to-end one narrows a live caller's grant inside the TTL and asserts the
*narrowed count*, not merely that something changed.

Counts are scope-filtered **inside the query** by reusing
`app/authz/queries.readable_servers` — no second translation of scope
(docs/SECURITY.md §4 rule 7). The six shared server filters were extracted into
`app/inventory/queries.server_filter_conditions` so §14.1 and §13.1/§13.2 cannot
drift apart on, say, how `provider` resolves through `cloud_accounts`.

#### Generated API types (closes issue 43)

`openapi-typescript` as a **dev** dependency, so it adds nothing to the bundle.
`backend/app/openapi.py` prints the schema without starting the app or opening a
socket; `scripts/generate-api-types.sh` pipes it into the committed
`frontend/src/api/schema.ts`; `npm run generate:api` runs it; a new `contracts` CI job
and a `check-all.sh` step regenerate and fail on drift. The hand-written interfaces in
`frontend/src/api/inventory.ts` are deleted and replaced by aliases onto the generated
`components['schemas']`.

It paid for itself immediately. The generated types are accurate about Pydantic
fields with defaults being *optional*, which the hand-written ones claimed were always
present, and `tsc` then found two places in `ServerDetail.tsx` that would have
rendered `undefined`.

§10 rung analysis, as the dependency rules require: rung 1 is satisfied by
[`docs/TECH_STACK.md`](docs/TECH_STACK.md) §11, which names the generator as the
mechanism this project uses; rungs 2–5 fail because nothing in the stdlib or the
installed set turns an OpenAPI schema into TypeScript. `axe-core` was added on the
same basis — TECH_STACK §9 already names `axe` assertions as the accessibility gate,
and `eslint-plugin-jsx-a11y` is a linter that cannot evaluate rendered DOM. Both are
dev-only and neither reaches the bundle.

#### Frontend

`pages/inventory/states.tsx` is **deleted**, not left beside the new components, as
its own docstring asked. `StatusBadge`, `DataStateBanner`, `EmptyState`/`LoadingState`
and `ErrorState` replace it, and all five existing pages were rewired onto them.

`StatusBadge` is the single owner of status visuals: glyph + word + colour, so status
is never conveyed by colour alone (PRD §8), and `null` renders "Unknown" rather than
falling through to healthy. The dashboard is the landing route in place of M1.5's
redirect to `/servers`. Summary cards link through to the filtered server list.

**Paging controls (closes issue 46).** `useCursorPaging` keeps the stack of visited
cursors — keyset paging gives "next" for free and no "previous" at all — and `Pager`
renders it. Used by the dashboard status table, the server list and the application
list; the server list's "narrow the filters to see them" message is gone. There is no
"page 3 of 9", because §5 returns `total: null` on purpose.

#### What M1.6 did not do

- **Issue 44** (server edit form, association management) — out of scope by decision;
  it belongs in its own change before M1.7's E2E suite locks the gap into Phase 1.
- **No charting library.** `TS-01` is still unselected and M2.2 is where charts live.
- **No Prometheus and no metric collection.**
- **No migration.** The schema is still at `0004`; this milestone only reads.

### What M1.5 implemented (2026-10-14)

All four decisions recorded below as "Decided for M1.5" were implemented as agreed.
One of them had a consequence nobody could have seen from the documents, and it is
the most important thing in this section.

#### The A-09 defect, and the amendment it forced

**Read this before anything else in M1.5.** M1.4 recorded assumption `A-09`: "a grant
that constrains a dimension the **target does not have** is a denial, not a pass."
M1.4 could not test that against a real row, because no scoped resource existed —
PROGRESS said so at the time. M1.5 added the first one, and the rule turned out to
deny everything:

- A **server** has no `application` dimension. The bootstrap CLI grants
  `{"environment": [...], "cloud_account": ["*"], "server": ["*"], "application": ["*"]}`,
  so under `A-09` the deliberately omnipotent first administrator could not list,
  read, register or edit a single server.
- **Registration** was worse: before the row exists there is no id either, so
  `server: ["*"]` failed too, and no scope could ever authorise a create.

This was not a corner case — it was every realistic grant against every endpoint the
milestone exists to ship. **`A-09` was amended, narrowly: a dimension scoped `["*"]`
constrains nothing, so it is satisfied by a target that does not carry that
dimension.** A grant naming a *specific* application still reaches no server — that
is `A-09`'s own worked example and the case that carries the security meaning — and
rule 4 is evaluated before the loop and is untouched, so `environment: ["*"]` still
does not reach production.

This is a change to a security rule and it is **more permissive than what M1.4
wrote**, so it is flagged rather than buried: it was taken because the milestone
could not be delivered without it, it is written into
[`docs/SECURITY.md`](docs/SECURITY.md) §4 and `app/authz/matcher.py`, and it is
pinned by four new matcher cases including "the bootstrap administrator's full scope
reaches a staging server" and "a wildcard still does not make production implicit".
If the product owner prefers a different resolution, the matcher is the one place to
change and the cases say exactly what the current behaviour is.

A second consequence of the same rules could **not** be resolved this way and is open
— see `OPEN-27` below.

#### The endpoints

- **§14.1–§14.7 and §18.1–§18.5**, twelve endpoints across
  `app/inventory/router_servers.py` and `router_applications.py`. **§14.8
  (connectivity check) is deliberately absent**: it is a synchronous probe through a
  provider adapter and the first adapter is M4.1, so shipping it would mean shipping
  a stub that looks operational (CLAUDE.md §7). §18.6 and §18.7 are absent for the
  same reason (health runner, deployments).
- **Scope filtering inside the query** (`app/authz/queries.py`), which
  [`docs/SECURITY.md`](docs/SECURITY.md) §20 requires and M1.4 explicitly left for
  this milestone. It is a *translation* of the matcher, not a second decision, and
  `tests/integration/test_scope_filtering.py` cross-checks the two across 28
  grant/effect combinations against real rows — if a rule changes in one and not the
  other, that test fails.
- **`404`, not `403`, for an out-of-scope server** (§2), because on a scoped
  resource a `403` on a specific id confirms the id exists.
- **Cursor pagination** (`app/core/pagination.py`): keyset over `(sort key, id)` as a
  row-value comparison, signed with a key derived from the configured database URL.
  `page.total` is `null` by §5. Pagination stability under a concurrent insert is
  tested directly.
- **Row-level idempotency** per the decision below, with replay → `200` +
  `idempotent_replay: true` and key reuse with a different body → `409`.
- **A read-class rate limit** (`require_read_budget`, 600/min per identity, failing
  **open**) — the fourth thing M1.4 listed for this milestone.
- **Audit** on every mutation, with `SERVER_ENVIRONMENT_CHANGED` as its own event and
  requiring Administrator, since it moves the authorisation boundary for every future
  operation on that host.
- **Frontend**: React Router plus `ServerList`, `ServerDetail`, `ServerForm`,
  `ApplicationList`, `ApplicationDetail`, with honest data states throughout — health,
  open alerts, provider capabilities and deployment targets each say *which* kind of
  "no data" they are rather than rendering a zero.
- **A seed script** (`python -m app.inventory.seed`), refusing to run outside
  `local`, marking every row `[DEMO]` / `.invalid` / `provider_metadata.synthetic`.

#### New assumptions and open questions

- **`OPEN-27` — how is a resource with no scope dimensions scoped?** An application
  has no environment, no cloud account and no server (AD-12), so the dimension rules
  have nothing to evaluate it against, and the `["*"]` amendment above does not help:
  the bootstrap grant names four *specific* environments, not `["*"]`. **Working
  default taken: applications are not scope-filtered** — `server:read` is the whole
  check for §18.1 and §18.3. That costs nothing operationally (an application row is
  metadata; every privileged action targets a server or a deployment target, each
  scope-filtered on its own dimensions; writes need Administrator-only
  `admin:integrations`), but it is a product decision, not an implementation one.
  Recorded in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §30 with the two
  alternatives.
- **`A-10` — the stored shape of `health_check_config`.** §18.2 names the field and
  §18.6 shows the checks it drives, but neither pins the shape. Chosen:
  `{"checks": [{"name", "url", "environment", "expected_status"}]}`, every URL
  allowlist-validated at write time (docs/SECURITY.md §19). Recorded in
  [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §18.2.
- **Assumption 4 in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §31 was amended**
  ("all list endpoints are scope-filtered") to match `OPEN-27`.

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
- [x] **`OPEN-02` — RESOLVED 2026-10-14 as environment-agnostic**, confirming
      `AD-12`, which is what M1.2 already built: `applications` has no `environment`
      column and keeps none. Environment lives on `servers`, `deployment_targets` and
      `deployments`, and "application health in production" is a query over its
      production servers. Settled **before** M1.5 began, which is the only reason it
      cost nothing — the per-environment alternative would have meant a data
      migration plus new §18 response shapes, and would have split one deployable
      into two application rows across staging and production, taking
      `application_id` scope grants and alert routing with it. Closed in all four
      contract documents.
- [x] `TS-02` (systemd metrics source) and `TS-01` (charting library) — before M2. Both resolved: `TS-01` 2026-10-16, `TS-02` 2026-10-17.
- [ ] `OPEN-15` — the audit-immutability hardening M1.2 could not finish: a
      non-superuser application role separate from the migration owner. Scheduled
      M6.2; see issue 28.

### Decided for M1.5 — agreed 2026-10-14, **implemented 2026-10-14**

Four questions put to the product owner before M1.5 started. `OPEN-02` was the only
blocker; the other three have schema or dependency consequences that should not be
decided unilaterally. Recorded here rather than only in a conversation, because the
session that implements them may be a different one.

- [x] **`OPEN-02`: applications are environment-agnostic.** See the closed entry
      above. No migration owed, §18 response shapes stand as written.
      **Implemented:** `applications` still has no `environment` column, and
      `ApplicationDetail` groups its servers by environment instead, which is what
      "where does this run" actually means under AD-12.

- [x] **Inventory idempotency lives on the row, not in a side table.**
      [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §7 requires an
      `Idempotency-Key` on `POST /servers` and `POST /applications`, with a replay
      returning the original resource and `idempotent_replay: true` — but
      [`docs/DATABASE.md`](docs/DATABASE.md) §14 only provides key storage for
      `operations` and `deployments`, and `servers` has `registered_at` but **no
      requester column**. Decided: **migration `0004` adds `idempotency_key` and
      `created_by` to `servers` and `applications`, unique per requester**, which is
      exactly the pattern §14 already uses. Retention is the row's lifetime, which
      satisfies §7's "at least 24 hours", and a replay reads the original row.
      Rejected: a shared `idempotency_keys` table (a second place idempotency lives,
      plus a retention sweep); and relying on the duplicate-detection unique index to
      answer `409`, which would diverge from §7's `200`.
      **Documentation debt this creates, to be paid in the same commit:**
      [`docs/DATABASE.md`](docs/DATABASE.md) §4.2, §4.3 and §14 must describe the two
      new columns and say that inventory creates are deduplicated this way.
      **Implemented** as migration `0004`, and the documentation debt is paid in the
      same change. Two things worth knowing that the decision did not anticipate:
      (a) the partial index is on `created_by IS NOT NULL AND idempotency_key IS NOT
      NULL` and deliberately **not** on `deleted_at IS NULL`, because a key must stay
      claimed after a retire or a replayed create would register a second server —
      both halves are tested; (b) the request body is not stored, so "replay with an
      identical body" compares the request against the stored row's *current*
      columns, which means a key replayed after the resource was edited reads as a
      different body and is refused. That is the safe direction but it is stricter
      than §7 describes; see issue 42.

- [x] **React Router is added, and M1.5 builds its five pages.** No milestone owned
      routing: M1.5's expected files list `ServerList`, `ServerDetail`, `ServerForm`,
      `ApplicationList` and `ApplicationDetail`, which need navigation between them,
      while M1.6's task list is the dashboard and `StatusBadge` only. Nothing is
      installed and [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §84 says only "React
      Router or equivalent". Decided: add it, and justify it against
      [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §10 in the PR — rung 5 fails because
      nothing installed does this, and rung 4 (the History API) stops being enough at
      five deep-linkable routes with list→detail drill-through, params and nested
      layouts. Rejected: a hand-rolled router, which grows into a router anyway as
      M1.6–M3 add pages; and deferring the pages to M1.6, which leaves M1.5's
      definition of done unmet and makes M1.6 large.
      **This is the first new frontend dependency since M1.1**, so
      [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §6 gets an entry in the same change.
      **Implemented:** `react-router-dom` pinned at `7.18.4`, with the §10 rung
      analysis written into [`docs/TECH_STACK.md`](docs/TECH_STACK.md) **§2.6** —
      that is where routing is listed, rather than §6, which is infrastructure and
      integrations. The version matters: every release up to and including 7.17.0
      carries open-redirect and SSR advisories, and `npm audit --audit-level=high`
      fails on them. It reports 0 vulnerabilities at 7.18.4.

- [x] **`integration:manage` is renamed to `admin:integrations` in M1.5's
      migration.** Issue 39. §18.2 is the first endpoint to require it, the contracts
      name it `admin:integrations` in four places
      ([`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §18, §24, §26 and
      [`docs/SECURITY.md`](docs/SECURITY.md) §4), and no user holds a grant through
      it today — so the rename is free now and gets more expensive with every
      endpoint that references it. Rejected: amending the four documents to the
      seeded name, which moves the documents to match the code rather than the
      reverse. The log permissions (`logs:read`/`logs:query` vs
      `logs:read:application`/`logs:read:system`) stay as they are until M3.2, which
      is the milestone that first enforces the distinction.
      **Implemented** in migration `0004` as an `UPDATE` keyed on the old name, so
      the existing `role_permissions` row followed the permission id rather than
      having to be recreated. Verified against the live catalogue, not just the
      migration source: `admin:integrations` is present, `integration:manage` is
      gone, and ADMINISTRATOR still holds it. The live `/auth/session` for
      `admin@example.invalid` lists `admin:integrations`.

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
| 1 — Foundation | ~~M1.1 skeleton + Compose + CI~~ **done** · ~~M1.2 schema + migrations~~ **done** · ~~M1.3 OIDC auth~~ **done** · ~~M1.4 RBAC~~ **done** · ~~M1.5 inventory~~ **done** · ~~M1.6 dashboard shell~~ **done** · ~~M1.7 consolidation~~ **done** | **Phase 1 COMPLETE** 2026-10-15 |
| 2 — Monitoring | ~~M2.1 Prometheus client~~ **done** · ~~M2.2 host metrics + charts~~ **done** · ~~M2.3 target discovery~~ **done** · ~~M2.4 systemd monitoring~~ **done** · M2.5 stale detection | **In progress.** M2.1 – M2.3 complete 2026-10-16, M2.4 2026-10-17; M2.5 ([§9](#9-next-recommended-task)) is next |
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
| AD-04 / AD-05 | Backend renders PromQL and LogQL from allowlisted templates; clients never send query strings | Closes injection and scope-bypass; limits ad-hoc querying. **AD-04's PromQL half is now implemented and in use** (M2.1): `app/metrics/templates.py` is the closed catalogue, no route accepts a query, and a 20-case injection suite covers the one reachable vector — a `metrics_instance` escaping its string literal. Its other half, the `metrics_instance` identity join, is implemented as a *rendering* rule but **nothing verifies the label resolves to a series yet** (M2.3). AD-05's LogQL half is untouched (M3.2) |
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
| **AD-12** applications environment-agnostic | No `environment` column; `ApplicationDetail` groups servers by environment instead. `OPEN-02` closed 2026-10-14 |
| **AD-14** derived server status | `status` defaults to `PENDING`; `STALE`/`UNREACHABLE` are absent from the update schema entirely, so an operator cannot assert an observation. `stale` is computed and reports `null` when `last_seen_at` is `NULL` |
| **docs/SECURITY.md §20** scope inside the query | `app/authz/queries.readable_servers` builds the `WHERE` clause; `tests/integration/test_scope_filtering.py` cross-checks it against the matcher over 28 grant/effect combinations on real rows |
| **docs/API_CONTRACTS.md §2** out-of-scope ⇒ 404 | `app/inventory/service.get_server`; asserted by id, not just by absence from a list |
| **docs/API_CONTRACTS.md §5** cursor pagination | `app/core/pagination.py` — signed keyset cursors, tamper and cross-sort replay both `422 invalid_cursor`; stability under a concurrent insert tested |
| **docs/API_CONTRACTS.md §6** filter/sort allowlists | `app/core/filters.py` plus `Literal` query types; an unknown parameter name is `422` naming the allowed set, rather than being silently ignored |
| **docs/API_CONTRACTS.md §7** idempotency | `servers.idempotency_key` / `applications.idempotency_key` + `created_by` (migration 0004); replay → `200` + `idempotent_replay`, reuse with a different body → `409` |
| **docs/API_CONTRACTS.md §14/§18** inventory | `app/inventory/router_servers.py`, `router_applications.py`; twelve endpoints, every mutation audited |
| **docs/SECURITY.md §13** read class | `require_read_budget`, 600/min per identity, failing **open** — the documented asymmetry against `/auth/*`, which fails closed |
| **docs/SECURITY.md §19** SSRF at write time | `app/core/ssrf.py`; `health_check_config` and `repository_reference` URLs are allowlist-checked on write, empty allowlist admits nothing. Request-time DNS checking lands with the first fetcher |
| **CLAUDE.md §7** synthetic data is marked | `app/inventory/seed.py` — `[DEMO]` prefix, `.invalid` hostnames, `provider_metadata.synthetic`, and it refuses to run outside `local` |

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
| 17 | ~~`TS-01` (charting library)~~ **RESOLVED 2026-10-16** — `d3-shape` + `d3-scale` used directly, and its functional gate (a data gap must render as a gap, not a zero) was **tested rather than assumed**, which is what it was waiting for. ~~`TS-02` (systemd metrics source)~~ **RESOLVED 2026-10-17** — `node_exporter`'s systemd collector with a unit allowlist, **tested against a real systemd rather than chosen from documentation**. The test found that no exporter exposes `sub_state`, `load_state`, enabled state or main PID; that is `OPEN-29`, agreed as `null` for M2.4. |
| 18 | No exact dependency versions are recorded anywhere, deliberately — versions belong in resolved lockfiles, not in prose. Nothing has been compatibility-tested. |
| 19 | The documentation set contains 236 relative links; all were verified to resolve to existing files and headings on 2026-10-13. Re-verify after any heading change — moving the `**[MANDATORY]**` labels out of `docs/SECURITY.md` headings during review was needed precisely because they altered 24 anchors. |

### Implementation issues (found during M1.1, 2026-10-13)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 20 | **RESOLVED 2026-10-15 (M1.7). `scripts/smoke-test.sh` has now been executed and passes**, twice, including once from empty volumes — so the M1.1 criterion "every service starts healthy" is signed off by its own gate for the first time. It did not pass on the first attempt: it failed at step 19 (`down -v` then `up`) with "migrations are not at head after a volume wipe", which is how the missing migration step was found (see [§3](#3-in-progress-milestone)). The script now covers docs/TESTING.md §11 steps 1–7 and 9–12; step 8 belongs to M2.1/M3.1 and §11.12 is partial because no production overlay exists (issue 52). Previously: **FURTHER RESOLVED 2026-10-13 (M1.3).** The full `core` stack **plus** the `mocks` profile was started and used: `postgres`, `redis`, `backend`, `worker`, `beat`, `frontend` and `mock-idp` all reached `healthy`, and login/logout were driven through them in a real browser. `scripts/smoke-test.sh` itself is **still unexecuted**, so the M1.1 criterion is not formally signed off by its own gate. Previously: the Docker daemon became available during M1.2, and `postgres` and `redis` were started and verified healthy (PostgreSQL 17.2, `redis-cli ping` → PONG). Originally: **the Compose stack has never been started.** No Docker daemon on the implementation machine. `docker compose --profile core config` validates (exit 0), but `up` is unexecuted, so "every service starts healthy" is **unverified**. `scripts/smoke-test.sh` has likewise never run. | Blocks the M1.7 smoke gate only | Run `scripts/smoke-test.sh` and record the result. The stack itself is known to come up. |
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
| 39 | **PARTLY DECIDED 2026-10-14** — the integration half is settled: `integration:manage` is renamed to `admin:integrations` in M1.5's migration (see [§3](#3-in-progress-milestone)). The log half stays open until M3.2. **The seeded permission catalogue does not use the names the contracts do, and M1.4 only reconciled the two it needed.** [`docs/SECURITY.md`](docs/SECURITY.md) §4 and [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §18/§24/§26 name `admin:integrations`, `logs:read:application` and `logs:read:system`; the M1.2 seed created `integration:manage`, `logs:read` and `logs:query`. Migration `0003` added the contract-named `admin:users` and `admin:roles` (needed by §25) and narrowed `admin:manage` to system settings, but left the integration and log permissions alone — renaming permissions nothing enforces yet would be churn, and the log split carries a real security distinction that the milestone enforcing it should design. | Naming drift; no behavioural effect today, because only ADMINISTRATOR holds any of them | Reconcile `integration:manage` → `admin:integrations` in M1.5 (the first endpoint to require it, §18.2) and `logs:read`/`logs:query` → `logs:read:application`/`logs:read:system` in M3.2. [`docs/SECURITY.md`](docs/SECURITY.md) §4 now names both forms so the divergence is visible rather than latent. |
| 39b | **RESOLVED 2026-10-14 (M1.5)** — `integration:manage` → `admin:integrations`, migration 0004, verified against the live catalogue. The log half (`logs:read`/`logs:query` → `logs:read:application`/`logs:read:system`) is still open and belongs to M3.2. | — | See issue 39 above |
| 40 | **A CSRF rejection answers `code: "forbidden"`, not `code: "csrf_failed"`.** [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §3 defines `403 csrf_failed` as a distinct code; `app/core/csrf.py` emits `forbidden` with the message "CSRF validation failed." Found while exercising the admin endpoints over HTTP without the header. | Contract divergence; a client switching on `code` cannot tell CSRF failure from permission failure | One-word fix in `app/core/csrf.py` plus the M1.3 CSRF tests that assert the current code. **Not done here**: `app/core/csrf.py` is M1.3's module and outside this milestone's scope (CLAUDE.md §3). |
| 41 | **A privilege change affecting *another* user does not rotate their session identifier,** which [`docs/SECURITY.md`](docs/SECURITY.md) §5 lists among the rotation triggers ("role change affecting the user"). Rotation requires issuing a new cookie, which can only happen on a request from that user's own browser, so a third party's grant change cannot rotate it. Nothing is cached in the session, so there is no stale authority to invalidate — the change takes effect on their next request regardless — and terminating their sessions instead would contradict [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §25.5's "without a re-login". `session_store.rotate()` therefore still has no caller. | Documented gap, no known exploit: the session record holds a `user_id` and nothing about authority | Either mark the session for rotation on its next use (a flag the session store would honour in `require_session`), or amend §5 to say rotation applies to the caller's own privilege change. Needs a decision; not invented here. |

---

### Implementation issues (found during M1.5, 2026-10-14)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 42 | **An idempotent replay is judged against the row's *current* columns, not the original request body.** Migration 0004 stores the key and the requester but not the payload, so `create_server` and `create_application` compare the incoming body against the stored row. A key replayed after the resource was edited therefore reads as "a different body" and is refused with `409`, where [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §7 describes `409` only for a genuinely different request. | Contract divergence in one direction only — it never silently returns something the caller did not ask for | Either store a hash of the canonical body in a third column, or accept and document the narrower rule. Needs a decision; the safe behaviour is in place meanwhile. Documented in [`docs/DATABASE.md`](docs/DATABASE.md) §14. |
| 43 | **RESOLVED 2026-10-15 (M1.6).** `openapi-typescript` is wired up as a dev dependency, `frontend/src/api/schema.ts` is generated and committed, `npm run generate:api` regenerates it, and the `contracts` CI job plus a `scripts/check-all.sh` step fail on drift. The hand-written interfaces are deleted. The generator immediately caught two nullability claims the hand-written types got wrong (see [§3](#3-in-progress-milestone)). Previously: **TypeScript API types are still hand-written.** [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §11 and CLAUDE.md §5 require them to be generated from the backend OpenAPI schema into a committed file, with CI failing on drift. `frontend/src/api/inventory.ts` is hand-written instead, because wiring `openapi-typescript` would be a **second** new frontend dependency in a milestone that agreed to exactly one (React Router), and that was not a decision to take unilaterally. The shapes are checked against the real responses by the backend suites rather than by the compiler. | Drift risk between frontend and backend; no behavioural fault today | Add `openapi-typescript` (or equivalent) plus the CI drift gate, and delete the hand-written interfaces. It is a small, self-contained change and it should be the next thing done to this file. |
| 44 | **RESOLVED 2026-10-15.** `ServerEditForm` (§14.4) at `/servers/:serverId/edit`, and an `AssociateApplication` control plus per-row **Remove** buttons (§14.6, §14.7) on the server detail page. Registration and edit share one file and two input components rather than being two forms. **No backend file changed** — `npm run generate:api` regenerated `schema.ts` with no diff, which is the check that proves it, and the two closed sets the edit form offers are now *typed from* the generated contract (`Exclude<ServerUpdate['environment'], null \| undefined>`), so a backend enum change breaks the build instead of leaving an option the API rejects. **Four decisions the contract leaves to a client, each taken deliberately and each with a test:** (a) **immutable fields are not rendered at all**, not rendered disabled — §14.4 answers `422 not_permitted`, so there is nothing to type into; the hostname is still *shown*, as text, because it identifies the host. (b) **An emptied optional box sends `null`, not an omission.** The backend dumps the body with `exclude_unset=True`, so absent means "leave it" and `null` means "unset it"; omitting a cleared field would make clearing impossible. `display_name` is excluded from that rule and marked `required` instead, because its column is `NOT NULL`. (c) **An environment change is confirmed explicitly** — Save stays disabled until a checkbox naming both environments is ticked — and carries the warning that it requires Administrator and audits `SERVER_ENVIRONMENT_CHANGED`. A `404` that follows one is annotated with both of its possible causes rather than one, because §2 answers `404` and not `403` for anything out of scope. (d) **A `409` from `If-Unmodified-Since` is shown with a "discard my changes and reload" button and never retried or overwritten.** The assertion that holds this is the `fetch` call count: a silent re-send without the precondition would look identical on screen. Association candidates are **not** filtered against the server's existing ones — §14.6 answers `409` with a message that says exactly that, and a client-side exclusion would be a second, divergent copy of a backend rule. Previously: **no UI for editing a server or managing associations**, only registration, read and retire, while [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) M1.5 listed "edit forms" and "association management" among the frontend tasks. | Was an incomplete M1.5 frontend task, stated rather than hidden | **One limitation, recorded rather than fixed:** the "your session does not list Administrator" note reads `admin:users` from the advisory `permissions` array, mirroring the backend's own stand-in for "is an Administrator" — and that array is **scope-blind** (§12.4). An Administrator scoped away from the *destination* environment therefore sees no warning and learns it from the `404`. Narrowing it would need a scope-aware pre-check in the UI, which is a second copy of the matcher and worse than the `404`. Tests: `ServerEditForm.test.tsx` (18) and `ServerDetail.test.tsx` (11), including the authorisation paths — hidden controls, and a test that calls `removeAssociation` directly *after* the UI has hidden the button to show the client places no gate of its own in front of the API |
| 45 | **There is no UI for registering an application.** §18.2's body carries `health_check_config`, whose URLs are validated against the SSRF host allowlist at write time — and `DCP_SSRF_HOST_ALLOWLIST` is empty by default, so a form offering a URL box would only ever produce a rejection. Applications are registered through the API until the allowlist is configurable. | Deliberate omission | Revisit when the allowlist has a configuration surface (M4/M6). |
| 46 | **RESOLVED 2026-10-15 (M1.6).** `useCursorPaging` + `Pager`, used by the dashboard status table, the server list and the application list. Previously: **`page.has_more` is reported but there is no "next page" control.** The backend mints a cursor and the list pages say more results exist; the UI asks the operator to narrow the filters instead. | Usability gap, no correctness fault | Paging controls belong with M1.6's list components. |
| 47 | **The cursor signing key is derived from `DCP_DATABASE_URL`**, not from a key of its own, so rotating the database password invalidates in-flight cursors (the client sees `422 invalid_cursor` and re-fetches one page). Chosen over a new mandatory secret, which PRD §11 forbids, and over a per-process random key, which would break cursors across instances and restarts. | Accepted, documented in `app/core/pagination.py` | Add an optional `DCP_CURSOR_SIGNING_KEY` with this as the fallback, if it ever matters. |
| 48 | **Three migration tests needed a rebuilt schema.** `test_full_downgrade_to_base_leaves_no_tables`, `test_the_migrations_seed_no_user` and `test_seed_revision_is_idempotent` run a real downgrade, and revision 0002 deletes the reference rows it seeded — which it cannot do while an inventory test's server references the seeded cloud account (`ON DELETE RESTRICT`, deliberately). The migration is behaving correctly; the tests' precondition was "nothing has created inventory rows", true by luck until M1.5. | Was blocking the integration suite | Fixed: a `rebuilt` fixture drops and recreates the schema before those three tests. |

### Implementation issues (found during M1.6, 2026-10-15)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 49 | **RESOLVED 2026-10-15 (M1.7), and it was two unrelated faults rather than the one cause recorded here.** `worker` used exec-form `CMD`, so compose's `$$HOSTNAME` reached celery as the literal string `$HOSTNAME` — exec form has no shell to expand it — and it pinged `celery@$HOSTNAME`, a node that exists nowhere; the health log showed five retries of `No nodes replied within time constraint` (exit 69) while `celery inspect ping` by hand answered `celery@afb4637f73ce: OK`. Fixed with `CMD-SHELL`. `beat` declared **no healthcheck at all** and therefore inherited the backend image's `curl localhost:8000/healthz`, which a scheduler serving no HTTP can never satisfy; it is now explicitly disabled, after measuring that the schedule file goes 270s without a resync against a 300s `maxinterval` and so cannot support an honest staleness check. The smoke test asserts beat's container state and its `beat: Starting...` line instead. Both services now report correctly in `docker compose ps`, which unblocked issue 20. Previously: **`worker` and `beat` report `unhealthy` in `docker compose ps`, and have since before M1.5.** Recorded here because it was invisible: no entry existed for it. Their healthcheck is `celery inspect ping -d celery@$$HOSTNAME`, so the likely cause is a hostname mismatch in the check rather than a sick worker — the containers run, and `/readyz` reports `broker: true` throughout. **Not investigated and not fixed**: nothing in M1.6 uses Celery, and fixing it here would have been scope creep. | Local signal only; no known functional impact, but an `unhealthy` container that is actually fine trains people to ignore the column | Verify whether the worker answers `celery inspect ping` under its real node name, and correct the healthcheck. Belongs with M1.7's Compose smoke test (issue 20), which is the milestone that asserts the stack is healthy |
| 50 | **A dashboard write is not reflected until the cache entry expires** (up to `DCP_DASHBOARD_CACHE_TTL_SECONDS`, default 15 s). Retiring a server and immediately reloading shows the old total. This is the cache behaving as specified, not a defect, but it is a real behaviour an operator will notice and it is not written in the contract beyond the TTL field. | Accepted, by design | None needed. If it ever matters, invalidate on write — which means deleting every dashboard key for every scope, so it is a real cost rather than a one-liner. `DCP_DASHBOARD_CACHE_TTL_SECONDS=0` disables caching entirely. The integration tests that write then read use an `uncached` fixture rather than asserting the cache away |
| 51 | **RESOLVED 2026-10-15. Two pre-existing integration tests were order-dependent and failed roughly one run in six.** `tests/integration/test_admin_roles.py::test_granting_production_scope_requires_holding_it` (M1.4) and `tests/integration/test_auth_flow.py::test_audit_rows_are_correlated_with_the_request` (M1.3). Both took `rows[0]` from a query filtered only by `action_type` and ordered by `occurred_at DESC`, assuming the newest row of that type was their own. **The original diagnosis of a tie was wrong, and so was the proposed fix.** A probe test run last in the session found **zero** `(action_type, occurred_at)` groups with more than one row across five full suite runs; rows of one action type are 200–700 ms apart. The real mechanism is an *inversion*: `occurred_at` is the **transaction** timestamp while the insert happens later (measured gap 7–30 ms, varying fourfold between rows), so ordering by it is not insertion order and an outlier gap reorders two rows. The recovered failure confirms it — `assert 'empty_scope' == 'delegation_escalation'`, where the count assertion on the preceding line had passed, so exactly one new row existed and `rows[0]` was still an older test's. A tiebreaker would **not** have fixed this: under an inversion `occurred_at DESC` still decides and `id DESC` is never consulted, and `app/db/types.py` documents uuid7 as monotonic *per millisecond only*. Scale was also understated: probing straight after `test_admin_roles` shows **49** `AUTHZ_DENIED` rows, not the 5 the end-of-run probe sees (`test_migrations.py` recreates the schema mid-session). | Was a flaky suite. No production defect: the ordering was a test-side assumption, not application behaviour | Each query now names the row it means instead of trusting the ordering: `_denied_rows` takes the per-test `caller`; `SESSION_TERMINATED` selects on the administrator and bootstrap `ROLE_GRANTED` on the grant it created; `_audit_rows` takes `actor_id` or `correlation_id`, with `_correlation_of` mirroring `audit.service.correlation_uuid` — correlation is the only handle on `LOGIN_FAILED` and `identity_conflict` rows, which are written with `actor_type` `SYSTEM` and no `actor_id`. `inventory_helpers.audit_rows` was **not** affected (every caller already filters by `target_id` and asserts a single row) and only gained a note. Three global `before + 1` counts became "exactly one row for this request", which is narrower, not looser. `id DESC` added as a secondary only. **Verified: 16 consecutive clean runs** — 8 on the fix branch off `main` (209 passed each) and 8 with the M1.6 suite present (231 passed each) — plus `scripts/check-all.sh` green end to end. Application code untouched (AD-11). The change was written on `fix-issue-51-audit-row-ordering` off `main`, because both test modules predate M1.6, and **reached `main` with M1.6 itself** — merged into `phase-M1.6` at `900bf5c`, then `main` by PR #8 at `d3e9544`, so that branch needs no PR of its own |

### Implementation issues (found during M1.7, 2026-10-15)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 52 | **There is no production Compose overlay**, so docs/TESTING.md §11.12's "resource limits present in the production overlay" has nothing to assert. `infra/compose/` holds one compose file (the local development stack) and the mock IdP. The development file does declare limits — eight services carry a CPU limit — and the smoke test asserts those, plus non-root, no privileged flag, no added capabilities and `no-new-privileges`, so the half of §11.12 that is checkable is checked. | Verification gap in a step that reads as fully covered | Write the overlay when there is a deployment target to write it for (Phase 6, alongside `OPEN-07`'s recovery objectives). Until then the smoke test prints `NOTE: no production overlay exists` rather than passing silently |
| 53 | **Vite's file watcher does not see edits through the Windows bind mount, so the E2E suite can validate code that is not in the working tree.** The `frontend` container mounts `frontend/src:ro` and runs the dev server; a changed module is not picked up, and the container keeps serving the previously built graph. Measured while fixing the response race (issue 54's cousin, see [§3](#3-in-progress-milestone)): with the fix on disk the probe still showed the unfiltered list 3 times in 8, and 0 times in 8 after `docker compose restart frontend`. This is worse than a flake — a green E2E run can be evidence about stale code. | **Trust in the E2E suite.** No production impact | `docker compose restart frontend` after changing `frontend/src`, which the README's E2E section now says. A real fix is either polling (`usePolling` in the Vite server config, which costs CPU) or running the dev server on the host rather than in the container. Not chosen here: both are workflow changes that deserve their own decision, and CI is unaffected because it builds from a fresh checkout |
| 55 | **The E2E suite exhausts the `/auth/*` rate limit after about three consecutive runs at the default setting.** The suite signs in three times per run (two in global setup, one in the §12.1 test that is *about* signing in), and the auth class allows 10 per 300s and **fails closed**. The fourth or fifth consecutive run therefore gets `429` from `/auth/login`, which surfaces as "the mock identity provider heading was not found" — a message that looks like the IdP is down. Verified: a five-run series on a default stack went pass, pass, pass, pass, fail with the limiter key at `11`. Global setup already reuses saved sessions when they still authenticate, which makes the steady-state cost zero; the problem is only after a stack recreate, when every saved session is stale. | Local iteration only. CI runs once against a fresh stack (three logins) and is unaffected | Start the stack with `DCP_AUTH_RATE_LIMIT_MAX=100` for E2E work, which the compose file passes through and docs/TESTING.md §16 documents. Deliberately **not** raised by default: the limit is a real control (AD-19) and a test environment that disables it stops exercising the thing. If the suite grows more logins, give it an identity per spec rather than raising the ceiling |
| 54 | **`x-backend-env` in the compose file enumerates the settings it passes through, so a setting absent from that list keeps its application default whatever `.env` says.** **The count was understated: it is eleven, not five.** Measured on 2026-10-16 by importing `Settings` and regexing the compose file: `DCP_OIDC_SCOPES`, `DCP_OIDC_CLOCK_SKEW_SECONDS`, `DCP_OIDC_HTTP_TIMEOUT_SECONDS`, `DCP_OIDC_METADATA_TTL_SECONDS`, `DCP_OIDC_JWKS_MIN_REFRESH_SECONDS`, `DCP_OIDC_LOGIN_STATE_TTL_SECONDS`, `DCP_READ_RATE_LIMIT_MAX`, `DCP_READ_RATE_LIMIT_WINDOW_SECONDS`, `DCP_SERVER_STALE_AFTER_SECONDS`, `DCP_SSRF_HOST_ALLOWLIST`, `DCP_SSRF_INSECURE_HOSTS`. The original five were the non-OIDC ones; the six OIDC tunables were missed. All are documented in `.env.example` and all have correct defaults, so nothing is broken; a developer who sets one in `.env` sees no effect and no warning. Found by `scripts/check_env_example.py`, which checks that the *names* exist, not that they are plumbed — and structurally cannot check the second half. | Configuration surprise; no behavioural fault today | **M2.1 did not add to the list**: `DCP_PROMETHEUS_URL` and `DCP_PROMETHEUS_TIMEOUT_SECONDS` were given both halves, and smoke-test step 18 asserts the passthrough end to end by reading `client.settings.prometheus_url` inside the backend container — which is the only check that catches a missing passthrough. The eleven remain. Either add them to `x-backend-env`, or switch the backend services to `env_file` and stop enumerating. The second is smaller but makes the container's configuration surface implicit, which is a real trade-off and not a milestone's call to make in passing. The checker could compare the two lists once the decision is made |
| 56 | **RESOLVED 2026-10-16 (found and fixed by M2.1). `scripts/check_coverage_diff.py` could not see untracked files, so a brand-new module was not judged at all.** The gate's docstring promises it measures "the change you are about to push" against the working tree, and it does — through `git diff`, which cannot report a file git has never been told about. M2.1 is the first backend change the gate ever judged, and on the first run it printed **`17/21 changed lines covered (81.0%)` — a pass — while silently skipping the entire new six-file `app/metrics/` package.** The 21 lines were from `app/core/config.py`, an *existing* file the same branch happened to touch. A new module is the most likely shape of an untested addition, so this was the one case the gate most needed to work. **The Cobertura path matching itself was fine** (`backend/app/core/config.py` matched the report's `app/core/config.py` by the existing suffix rule), which is what the M1.7 handover flagged as the thing to watch; the real defect was elsewhere. | **The gate reported a pass it had not earned** — the same failure class as a skipped suite reading as a passing one, which is what `scripts/check-all.sh` exists to prevent | `untracked_lines()` lists `git ls-files --others --exclude-standard`, filters it through the existing `is_measured()` and counts every line of each file as changed. **The floor was not lowered.** After the fix the same diff measured **388 lines**, and the tests the gate then asked for were written: the final run is `388/388 (100.0%)`. Staged additions already appeared in `git diff`, so this closes exactly the pre-`git add` window and nothing else. The script has no test suite of its own — `scripts/` is not covered by any report — so its evidence is the before/after line counts recorded here |
| 57 | **The sibling input-validation patterns in `app/inventory/schemas.py` are `$`-anchored, which in Python also matches immediately before a trailing newline.** So `"web1.example.com\n"` satisfies `_HOSTNAME_PATTERN`, `"org/repo\n"` satisfies `_ORG_REPO_PATTERN`, and the same holds for all four `PROVIDER_RESOURCE_ID` patterns — six patterns in total. M2.1 fixed **only** `METRICS_INSTANCE_PATTERN`, moving it to `\Z`, because that value is interpolated into a PromQL string literal and the renderer relies on the pattern as its first layer of defence. The other six are stored, not interpolated, so the consequence today is that a value with a trailing newline can be *stored* — a typo, not an injection. | Low. No current sink interpolates any of the six into a query, a command or a header. It is a latent hazard rather than a live one, and it is the kind that becomes live the moment one of these values is used to build something | Change the six to `\Z` in one small, strictly-tightening change. Left out of M2.1 on purpose (CLAUDE.md §3: note it and move on) because they belong to M1.5's write boundary, not to this milestone, and a tightening of stored-value validation deserves its own diff and its own tests rather than riding along in a metrics change. Worth doing before anything starts building strings out of a hostname |
| 58 | **The circuit-breaker state that §26.1 reports is per-process, so with more than one backend replica the page reports whichever replica answered.** `app/metrics/client._BREAKER` is a module-level instance. `last_success_at`, `last_error_at` and `circuit_state` are therefore that worker's view, and two polls of `/api/v1/integrations/status` can legitimately disagree. In the local stack there is exactly one backend container, so it is currently invisible. | Accepted for now, and marked in code with a `ponytail:` comment naming the ceiling. It is also not concurrency-exact *within* a process: two coroutines can both pass `allow()` in the half-open state, which costs one extra probe against a monitoring server | A Redis-backed breaker, which the project already has the client for, shared per integration. Worth doing when there is more than one replica or when §26.1 gets a UI — a status page that flickers between `ok` and `open` depending on which worker answered is worse than one that is occasionally stale. Related: §26.1's `degraded` state is **not emitted at all** today, because deciding it honestly needs failure-rate history over a window that nobody has specified (see [§3](#3-in-progress-milestone)) |
| 59 | **MITIGATED 2026-10-16 (M2.2) — it recurred, was reproduced and was fixed the way this row predicted; see the end of this row.** **One frontend vitest test failed once, under load, and was not identified.** Observed during M2.1's verification: a `scripts/check-all.sh` run reported `Test Files 1 failed | 12 passed (13)` / `Tests 1 failed | 114 passed (115)`, which also failed the frontend changed-line coverage gate as a **consequence** (a failed vitest run leaves no usable Cobertura report, so the gate exits 2). **The test's name was not captured** — the run's output was filtered to the per-check summary lines, and by the time that was noticed the output was gone. It did not reproduce: five subsequent runs were clean (one plain `vitest run`, three `vitest run --coverage`, and one more full `check-all.sh`, all 115 passed). The run that failed was the most loaded of the set — it followed a two-minute backend integration suite with the whole Compose stack running, on a Windows machine — and the frontend suite sets no `testTimeout`, so vitest's 5 s default and Testing Library's 1 s `waitFor` default both apply to nine test files that use `waitFor`. A contention-induced timeout is the plausible mechanism, which is a hypothesis and not a diagnosis. | **Unknown, and deliberately recorded as unknown.** M2.1 changed no frontend source — the only frontend file in its diff is the regenerated `src/api/schema.ts` — so a new defect introduced by this milestone is the least likely explanation; but "probably a flake" is exactly the conclusion that let issue 51 live for five milestones | **Capture the output next time rather than hunting for it now.** Concretely: run `check-all.sh` with its full output kept (it is already one stream; the mistake was filtering it at the call site), and if it recurs, the file and assertion will be in it. If it recurs more than once, the candidates are the nine `waitFor` files, and the fix is an explicit `testTimeout` in `frontend/vite.config.ts` rather than a retry — a retried flake is a flake that still fails in CI. Worth noting for M2.2, which adds chart tests to the same suite. **Update, M2.2:** it recurred — three of five full parallel runs failed, with a *different* test each time. `ServerForm.test.tsx` (about 45 characters typed per test through `userEvent`; 18–23 s as a file under load) hit vitest's 5 s limit, and once that was raised, `findBy*`/`waitFor` hit Testing Library's separate 1 s default in two more tests (one this milestone's own). Each passes alone, which confirms contention. Fixed as predicted, with no retry: `testTimeout: 15_000` in `frontend/vite.config.ts` and `configure({ asyncUtilTimeout: 5_000 })` in `frontend/src/test/setup.ts`; four consecutive full runs then passed 170/170. A passing assertion is not slowed; a failing one takes 5 s to fail. **Mitigated, not eliminated** — pasting rather than typing in `ServerForm.test.tsx` is the real cure and touches tests M2.2 did not own |

### Implementation issues (found when CI first ran, 2026-10-17)

**CI had never executed against this repository before PR #15** (M2.1 – M2.3 were merged on local checks alone), and its first run — on M2.4 — failed six of ten jobs. **None of the six was M2.4's logic**; five distinct causes were found, each reproduced on Linux from a clean `git archive` checkout (so a file that exists only in the working tree could not hide), fixed on branch `ci-fixes`, and re-verified the same way. **The second run (on `ci-fixes`) still failed four jobs and found two more causes** (issues 86 and 87), which the first fixes had been hiding: a pipeline with several faults reports only the first one each job reaches. The lesson is the one M1.1's caveat already gave: **a pipeline nobody has run is a hypothesis.**

| # | Issue | Severity | Handling |
|---|---|---|---|
| 81 | **Every shell script was committed `100644`** (`core.fileMode` is `false` on Windows, so git never saw the mode), so `./scripts/smoke-test.sh` and `./scripts/generate-api-types.sh` exited **126** in CI — the `compose` and `contracts` jobs never started their work | High (two jobs red) | `git update-index --chmod=+x` on the four `.sh` files. **A new script added from Windows will do this again**; `git add --chmod=+x` is the habit, and a guard in `check-all.sh` (list `scripts/*.sh` not `100755`) would make it a failure instead of a CI surprise |
| 82 | **`aquasecurity/trivy-action@0.28.0` does not exist** — the tag is `v0.28.0` — so both image jobs failed at "Set up job", two seconds in, scanning nothing | High (two jobs red) | Corrected to `@v0.28.0`. Not SHA-pinned (AD-16 asks for that for actions too) |
| 83 | **The workflow-level `DCP_DATABASE_URL` / `DCP_REDIS_URL` (pointing at `localhost`, for the unit suite and the schema export) leak into Docker Compose.** Compose interpolates `${DCP_DATABASE_URL:-…}` from the job's shell, so in the `e2e` and `compose` jobs they replaced the defaults and gave every container a URL to a PostgreSQL that is not in it: `migrate` exited 1, connection refused (reproduced exactly) | High (two jobs red) | Both jobs blank the two variables at job level; empty is "unset" to `:-`. Verified by running the whole smoke test, from a clean checkout, with the workflow's variables set and blanked |
| 84 | **`test_rate_limit_fails_closed_when_redis_is_unreachable` assumed nothing answers on `localhost:6379`.** True on a laptop; false in the `backend` job, which runs a Redis service container exactly there (503, not 429) — and false for any developer running their own Redis. The harness's "unreachable" placeholder was a real, commonly used port | Medium (one job red) | The unit suite now points PostgreSQL and Redis at `127.0.0.1:1`, a closed port (`tests/conftest.UNREACHABLE`). The two assertions that named the old host were moved with it, so they still test something — left alone they would have passed vacuously |
| 85 | **Both production images carried fixable HIGH/CRITICAL CVEs: 52 (backend, Debian 12.9) and 38 (frontend, Alpine 3.20.5)**, all OS packages from base images pinned in December 2024. Python dependencies were clean (`pip-audit` and Trivy agree). The Trivy gate was right to fail | High (a real finding, not a CI fault) | Base tags bumped to current patch releases (`python 3.12.15-slim-bookworm`, `node 22.23.3-alpine`, `nginx 1.30.6-alpine`, the stable line); Trivy 0.56.2, `--severity HIGH,CRITICAL --ignore-unfixed`: **0 and 0**. **A pinned base rots**: this gate will fail again as these age, which is the gate working — base images belong in the weekly dependency PRs ([TECH_STACK.md §9](docs/TECH_STACK.md)), and digests are still not pinned (AD-16) |

| 86 | **`docker compose up --wait` fails on the hosted runner's Compose 2.38.2 for a service with no healthcheck** — `container dcp-beat-1 has no healthcheck configured`, exit 1 — while the Compose 5.6 used for development accepts it and says nothing. M1.1 had deliberately *disabled* beat's healthcheck (a check could only overclaim). This failed the `e2e` job and, with issue 81 fixed, the `compose` job | High (two jobs red) | **Reproduced with the official `docker-compose` v2.38.2 binary** (checksum verified against the release's published SHA-256). beat now declares `grep -qa 'celery.*beat' /proc/1/cmdline`: PID 1 is celery beat — the same fact as Docker's running state, claimed no more strongly, with a negative control showing it can fail. The comment records why. **The whole smoke test then passed through a shim routing `docker compose` to 2.38.2**, including the systemd fixture and every other `--wait`. Lesson: **develop against the Compose version CI runs** (Docker Compose 2.38.2, Docker 28.0.4 on `ubuntu-24.04`) |
| 87 | **`aquasecurity/trivy-action@v0.28.0` is itself unusable**: its nested `aquasecurity/setup-trivy@v0.2.1` no longer resolves, so the image jobs still failed at "Set up job" after issue 82's tag fix (which had only exposed this) | High (two jobs red) | Bumped to `v0.36.0`, which pins its nested actions by commit SHA; rescanned with Trivy 0.70.0 (that action's default): **0 and 0** on both images |

**Verified on Linux, from a clean checkout, with the workflow's environment and real service containers:** 777 unit and 365 integration tests pass, migrations apply up/down/up on PostgreSQL 17.2, `pip-audit` is clean, the TypeScript API types regenerate **byte-identical** to the committed file, and `scripts/smoke-test.sh` passes (all 20 steps, including the systemd fixture in step 18b) — **also when `docker compose` is the hosted runner's own 2.38.2**. **The E2E suite passes 11/11 against a stack built the way the `e2e` job builds it** (clean checkout, the workflow's variables blanked, the bumped base images; Playwright from the Windows host). **Still not observed on a hosted runner:** the `e2e` job itself, the `compose` job's runtime, and whether the systemd fixture can start there (issue 69).

### Implementation issues (found during M2.4, 2026-10-17)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 69 | **The systemd fixture cannot start on GitHub's hosted runner, so step 18b is NOT RUN in CI — observed 2026-10-17.** On PR #17 (`ci-fixes-2`) all ten jobs passed, but the `compose` job's log ends **`SMOKE TEST PASSED, BUT INCOMPLETE`** with step 18b listed NOT RUN: `SMOKE_SYSTEMD=auto` did what it was built to do and the job stayed green *without* the step. The fixture needs `cap_add: SYS_ADMIN`, `cgroup: host` and a writable `/sys/fs/cgroup`, runs without `no-new-privileges`, and can modify the host's cgroup tree; it has now been observed to start on exactly one Docker host (WSL2, Docker 29.8, cgroup v2) and to fail to start on the other (Docker 28.0.4 on `ubuntu-24.04`, where the cause has **not** been established — the job log was read by the user, not by this session). **Consequence: in CI, unit state is covered only by the unit and integration suites against scripted Prometheus answers** — which prove the portal's reading of a series, not that a real systemd produces it. The real-systemd evidence is the local smoke run and the `TS-02` scratch runs, not a CI result. | Medium | Left at `auto` deliberately (`require` would turn the hosted run red for a fixture the runner cannot run). **To close it:** read the step's `docker compose ... logs systemd-host` output in the job log (the script prints it before the NOT RUN banner) to learn *why* it cannot start, then either adapt the fixture to what the runner allows or run 18b on a self-hosted runner. Until then, do not describe M2.4's systemd behaviour as verified in CI |
| 70 | **The fixture's base image is the floating tag `debian:bookworm-slim`** and `apt-get` runs at build time, so it is not digest-pinned (AD-16) and `scripts/pin-digests.sh` does not read Dockerfile `FROM` lines | Low | Fixture only, never shipped. Pin when the script learns Dockerfiles |
| 71 | **`services.updated_at` moves on every refresh** (the trigger fires on each `UPDATE`, every 60 s per monitored unit). Nothing reads it for concurrency — §16.5 has no `If-Unmodified-Since` — so it costs only the meaning of that column | Low | Recorded in `app/services/refresh.py`. If §16.5 ever gains the precondition, do what migration 0005 did for `servers` |
| 72 | **There is no screen to register, edit or deregister a service.** §16.3, §16.5 and §16.6 exist and are tested over HTTP, but a unit can only be registered through the API (the smoke test does exactly that) | Medium | Not in the plan's M2.4 frontend scope ("service list and detail"). Needs a form and its error mapping |
| 73 | **`UNCOLLECTED` merges three causes**: the collector is not enabled, the collector is failing (no system bus), and the unit is not on the host. The fix is exporter-side in all three, but an operator cannot tell a typo in a registered name from a broken exporter | Low | By design for now; `sub_state` and friends cannot help either (`OPEN-29`). Worth a per-host rollup in M2.5 |
| 74 | **The dashboard's failed-services count is scoped by `server:read`**, as it was in M1.6, not by `service:read` | Low | Every seeded role that holds one holds both. Revisit if roles become configurable |
| 75 | **Lists show `restart_count: null`** (the cache holds a state and a time); only §16.4 knows it | Low | Agreed trade-off (no migration). Add cache columns if the failed-service view needs it |
| 76 | **The refresh job rewrites every monitored row every 60 s**, because `last_observed_at` is the product. Thousands of services is thousands of `UPDATE`s a minute; unmeasured | Low | Batched 500 per statement. Measure at M6.3 |
| 77 | **`server_reachable` can disagree for up to a minute**: lists derive it from the agent probe (30 s), the detail page from the live `up` | Low | Both are `UNKNOWN`-safe; only the explanatory line can lag |
| 78 | **The smoke test needs a fresh database.** Step 18's registration answers `422` if a server already claims `node-exporter:9100` — found when it was first run against a dev stack with leftover rows. Also, the frontend build context is ~200 MB over the WSL file bridge (3 minutes) | Low | Run after `down -v`. A `.dockerignore` for `node_modules` would fix the second |
| 79 | **The 18b step takes several minutes** (a scrape is 15 s and each transition waits for one, plus the unreachable window) | Low | Unmeasured on a CI runner; the deadlines are generous multiples |
| 80 | **The changed-line coverage gate under-reports async handler code.** `coverage` is configured without `concurrency = ["thread", "greenlet"]`, so lines that run after a SQLAlchemy async call lose their trace. Verified: a passing paging test walks `services/router.py` lines 122–130, yet the gate lists them uncovered; re-measured with a greenlet-aware config they are covered. M2.4's gate numbers (585/638 backend, 91.7%) are therefore a **floor**, not the truth — and earlier milestones' were understated the same way | Low | Not fixed here (a project-wide measurement change, out of M2.4's scope). The fix is one line in `[tool.coverage.run]`; it can only raise the reported numbers |

### Implementation issues (found during M2.3, 2026-10-16)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 65 | **RESOLVED 2026-10-16 (found and fixed by M2.3). `scripts/check_coverage_diff.py` crashed on Windows instead of giving a verdict.** It ran `git diff` with `text=True` and no encoding, so the output was decoded with the locale code page (cp1252), which cannot decode some bytes of a perfectly good UTF-8 diff; the reader thread died, `stdout` came back as `None`, and the script ended with `AttributeError: 'NoneType' object has no attribute 'splitlines'`. Both coverage gates failed in `check-all.sh` for a reason unrelated to coverage. It had not appeared before because earlier diffs happened not to contain the offending byte. | Medium: a gate that crashes is not a gate, and the failure looks like a coverage failure | **Fixed:** `encoding="utf-8", errors="replace"` (only line *numbers* are read from the diff). The floor was not touched. |
| 66 | **The frontend changed-line gate cannot see a JSX-only edit inside an existing component.** This milestone's `ServerDetail.tsx` and `StatusBadge.tsx` changes were reported as "no coverable line changed", because v8 coverage attributes those lines to a block already counted. They *are* tested (`ServerDetail.test.tsx`, six new cases). | Low | Open, understood. The gate remains useful for new files and new logic; it should not be read as saying a JSX change is covered. |
| 67 | ~~**During a Prometheus outage every monitored server is reported `UNKNOWN`.**~~ **Partly resolved 2026-10-17 (M2.5 commit 2, D2):** the *status sweep* no longer ages anything during an outage (it measures against the last time Prometheus answered). `agent_status` still flaps to `UNKNOWN` for the outage's duration, deliberately (`app/metrics/agents.py`). | Low | Open for `agent_status` only |
| 68 | **The target file is delivered over a shared Docker volume**, which only works when the worker and Prometheus share a host. | Medium for production | Open, tied to `OPEN-12` (production topology). |

### Implementation issues (found during M2.2, 2026-10-16)

| # | Issue | Severity | Handling |
|---|---|---|---|
| 60 | **No E2E coverage for the metrics pages.** [TESTING.md §12](docs/TESTING.md#12-end-to-end-tests-playwright) path 3 (server health inspection) needs a stack with Prometheus up and an exporter registered, which the Playwright suite does not yet seed. The pages were exercised manually in a real browser on 2026-10-16 only. | Medium | Open. Step 18 of the smoke test covers the backend half against real systems; the pages have 11 + 8 component tests. |
| 61 | **The dashboard `utilisation` section is still `not_configured`**, and its reason text still says "(M2.1)". Decision 4 of this milestone. | Low | Open by decision. Needs either a fleet aggregate (a design decision against AD-04) or a sample of in-scope servers. |
| 62 | **`stale` has never been seen against a real Prometheus.** Scripted tests cover the boundary exactly (60 s inclusive, 61 s stale); a real host that stops being scraped vanishes (`unavailable`) before it can be stale, so the state is reachable only by a lagging Prometheus. The bound is a module constant, not a setting. | Low | Open. M2.5 owns host-level staleness and should decide whether this per-reading bound survives it. |
| 63 | **An environment comparison over more than 20 in-scope servers is refused, not aggregated**, so "compare production" fails on a large estate. Chosen deliberately (never silently truncate); a limit of the first design, not of the contract. | Medium on a large estate | Open. A per-environment aggregate (and which — a mean hides one hot host) needs a product decision. |
| 64 | **`excluded_count` is `0` for an environment selection by design**, so a caller cannot tell "I may read none of production" from "production is empty". | Low | Accepted: the alternative discloses how many servers exist outside the caller's scope. Recorded so it is not rediscovered as a bug. |

---

## 7. Tests executed and their actual results

All results below were produced by running the commands and reading the output.
Nothing here is inferred.

### Executed — M2.4 (2026-10-17)

All against **real PostgreSQL 17.2 and real Redis** (Compose, WSL2 Docker 29.8) except where stated.

| Command | Result |
|---|---|
| `uv run pytest -m "not integration"` | **777 passed** |
| `uv run pytest -m integration` | **365 passed** (69 of them new this milestone: `test_services_api.py` 58 incl. the SQL-vs-Python parity test, `test_services_refresh.py` 8, and 3 dashboard) |
| frontend `vitest run` | **211 passed** (axe on the list and detail pages) |
| `uv run ruff check`, `ruff format --check`, `mypy` | clean |
| frontend `eslint`, `prettier --check`, `tsc --noEmit`, `npm run build` | clean |
| `./scripts/check-all.sh` | `scripts/check-all.sh` passes all 18 checks with nothing skipped (changed-line coverage 585/638 backend and 69/73 frontend, both above the 80% floor and understated for the backend — issue 80) |
| `./scripts/smoke-test.sh` (WSL, from empty volumes) | **PASSED, exit 0, all 20 steps including 18b** |

**Step 18b, as it ran:** registered `systemd-host:9100` and four units through the API; an unregistered unit (`systemd-journald.service`) was `404`; `dcp-fixture-app` read `ACTIVE` with `restart_count` 0, a `started_at`, and the four unsupplied fields `null`; the unit that fails at boot read `FAILED` and the unit the host does not have read `UNCOLLECTED`. `systemctl kill -s KILL` ⇒ `FAILED` and `systemctl stop` of another ⇒ `INACTIVE`. After the refresh task the failed-service view listed **exactly** the two failed units and the dashboard counted them. Stopping the system bus ⇒ `UNCOLLECTED` with `server_reachable: true`; starting it ⇒ `FAILED` again. Stopping the host ⇒ **all four units `UNKNOWN`** on the detail page and in the list, `server_reachable: false`, and **none** listed `INACTIVE` or `FAILED`.

**Executed — `TS-02` itself (scratch runs, 2026-10-17, not kept in the repository as scripts):** the pinned exporter against a real systemd 255 showed the series shapes in [TECH_STACK.md §5.3](docs/TECH_STACK.md#53-systemd-metrics-integration), including the three that drove decisions: an unknown unit emits no series, a not-running unit reports start time `0`, and a failing bus leaves the exporter at HTTP 200 with `collector_success=0`. Step 18b is the standing version of that test.

**Mutation checks (tests proven able to fail):** skipping the host check when the collector fails in `normalise`, accepting a start time of `0`, ignoring `UNREACHABLE` in the SQL `CASE`, and dropping the SQL `UNSUPPORTED` rule each made the suites fail (the last two by the parity test and the state-filter test); rendering `UNKNOWN` as `Inactive` in the frontend failed three tests. One mutation (accepting two active states) survived because the following check already rejects it — redundancy, not a gap.

### Not executed in M2.4, and therefore not claimed

- **Step 18b on a hosted runner.** CI ran (all ten jobs green on PR #17 after fixes — [§6, issues 81–87](#6-known-issues)), but the systemd fixture could not start there, so 18b was NOT RUN and the job ended `PASSED, BUT INCOMPLETE` (issue 69). Unit state is therefore verified against a real systemd **only locally**.
- **The new pages in a real browser.** They are tested in jsdom with axe, and the backend behind them is tested end to end, but nobody has looked at `/services` against the live stack.
- **A real (non-fixture) host.** `TS-02` was tested against a systemd in WSL and in a container. A production host installing `node_exporter` with `--collector.systemd` is documented, not exercised.
- **The 18b step on a Docker host other than WSL2.**

### Executed — M2.3 (2026-10-16)

On Windows 11 against the Compose stack (PostgreSQL 17.2 on `127.0.0.1:5433`, Redis on
`127.0.0.1:6380`).

#### 1. `./scripts/check-all.sh` — ALL CHECKS PASSED (18/18), final run

```
backend: unit tests         586 passed, 296 deselected in 70.31s
backend: integration        296 passed, 586 deselected in 230.29s
frontend: vitest            Test Files 18 passed (18) · Tests 176 passed (176)
coverage: backend diff      213/215 changed lines covered (99.1%), floor 80%
coverage: frontend diff     no coverable line changed against origin/main  (issue 66)
docs: link check            438 relative links, 0 problems
(ruff, mypy, bandit, pip-audit, eslint, prettier, tsc, build, npm audit,
 api types, .env.example)                                            all ok
```

Versus M2.2: unit 539 → 586 (+47), integration 272 → 296 (+24), frontend 170 → 176 (+6).
**An earlier run in the same session failed three checks**: both coverage gates (issue
65, a decoding crash in the gate itself) and the docs link check (a wrong anchor,
`API_CONTRACTS.md#14-servers`, which is `#14-server-inventory`). The two uncovered
backend lines the gate names are `await discovery.request_regeneration()` and
`_check_metrics_target(...)`, both executed by the integration suite.

#### 2. Mutations that were caught

| Mutation | Result |
|---|---|
| trigger no longer excludes `agent_status` / `last_seen_at` | `test_a_probe_does_not_move_updated_at_but_a_person_does` and `test_an_edit_survives_a_probe_that_landed_after_the_form_loaded` fail |
| reserved-domain (`.invalid`) rule removed | `test_only_eligible_servers_become_targets` and the `demo.demo.invalid` write-boundary case fail |
| unobserved hosts default to `HEALTHY` | `test_when_prometheus_cannot_be_asked_nothing_stays_healthy` fails |

Each file was restored and compared byte for byte with a backup before the suite was
rerun; the `.invalid` and trigger mutations were both reverted and grepped.

#### 3. `./scripts/smoke-test.sh` — SMOKE TEST PASSED

```
18. Prometheus scrapes the host exporter and the templates query it (§11.8)
  registering node-exporter:9100 through the API
  target file lists the registered server
  demo servers and free text are absent from it
  worker sees the configured probe interval and unreachable window
  scrape timestamp: … (3.9s before evaluation)
  current: cpu_utilisation=91.24, load_average=0.96, agent_up=1.00
  history: 41 points, 40 holes, none zero-filled
  waiting for the probe to mark the registered server HEALTHY
  HEALTHY, last seen 2026-10-16T07:54:53.905000Z
  6 seeded demo servers stay UNKNOWN (never scraped, never asserted)
  stopping the exporter
  UNREACHABLE after the configured window
  HEALTHY again once the exporter returned
SMOKE TEST PASSED
```

**It took seven runs to get here; six failed, and all but the first were the script's
rather than the product's** — which is why each is written down:

1. `useradd … \n && mkdir …` — a Dockerfile edit that wrote a literal `\n` instead of a
   line continuation; the image did not build. Caught because the smoke test builds.
2. The target-file poll never saw the file. I first suspected `cat … | grep -q` under
   `set -o pipefail` (`grep -q` closing the pipe early makes `cat` exit non-zero, so a
   match reads as a failure) and fixed that — a real hazard, but **not the cause**, and
   the run failed identically.
3. The cause: **Git Bash on Windows rewrites a bare `/var/lib/…` argument into
   `C:/Program Files/Git/var/lib/…`** before docker sees it. Found by reproducing the
   exact command by hand against a live stack. Fixed with `sh -c '…'`, which a quoted
   command string escapes.
4. A leftover row of my own in a reused volume (`metrics_instance` already claimed) —
   not a bug; the previous failed run's teardown had not yet removed my manual stack.
5. A `grep -o` with no match under `set -e`, which ended the script **with no message at
   all**; replaced with a JSON assertion that says what it saw.
6. The M2.2 history assertion raced the new world: series now begin when the server is
   *registered*, so the first `rate()` point is a little way off; it now polls like the
   other readings.

#### 4. In a real browser, on the running stack

Logged in as the bootstrap administrator and registered `node-exporter:9100`. The new
"Monitoring agent" row read, in order: *Unknown — "Not observed yet — waiting for the
first scrape"*; *Healthy*, with a real `Last seen` and `Stale: false`; and, about **34 s
after stopping the exporter**, *Unreachable* with the last-seen time kept. A registration
naming `169.254.169.254:9100` and one naming port 22 were each refused with a field
error (`That address cannot be a scrape target.` / `That port is not one Prometheus may
scrape.` + `allowed: ["9100"]`) and neither echoed the value. **Manual, not a regression
test (issue 60).**

### Not executed in M2.3, and therefore not claimed

- **The Playwright E2E suite.** Not run; no path covers the new row.
- **GitHub Actions.** Nothing is pushed. The compose job now runs the longer step 18
  (registration, two exporter state changes) and its CI duration is **unmeasured**; the
  `promtool check config` step now prints a `WARNING` that `/prometheus-targets/node.json`
  does not exist (exit 0), which is expected — the file is generated at runtime.
- **A real `DEGRADED` / `NOT_INSTALLED`**, which nothing emits, and **a Windows host**.

### Executed — M2.2 (2026-10-16)

On Windows 11 against the Compose stack (PostgreSQL 17.2 on `127.0.0.1:5433`, Redis on
`127.0.0.1:6380`).

#### 1. `./scripts/check-all.sh` — ALL CHECKS PASSED (18/18), final run

```
backend: ruff lint / ruff format / mypy / bandit        ok
backend: unit tests         539 passed, 272 deselected, 1 warning in 43.91s
backend: integration        272 passed, 539 deselected, 1 warning in 152.52s
backend: pip-audit                                      ok
frontend: eslint / prettier / tsc                       ok
frontend: vitest            Test Files 18 passed (18) · Tests 170 passed (170)
frontend: build / npm audit                             ok
contracts: api types                                    ok
config: .env.example                                    ok
coverage: backend diff      316/324 changed lines covered (97.5%), floor 80%
coverage: frontend diff     249/268 changed lines covered (92.9%), floor 80%
docs: link check            428 relative links, 0 problems
```

Versus M2.1: unit 496 → 539 (+43), integration 237 → 272 (+35), frontend 115 → 170
(+55, of which 12 are TS-01's `linePath` gate and 43 are M2.2's).

**The first full run failed four checks, and the failures are the useful part.**
Bandit flagged two `assert server.metrics_instance is not None` (B101) — replaced by
passing the instance explicitly rather than asserting it, which is also what mypy
wanted. The frontend suite failed on timing (issue 59). The frontend changed-line
gate then failed as a *consequence* — a failed vitest run leaves no Cobertura report —
exactly the cascade issue 59 describes. And `contracts: api types` failed only because
`schema.ts` was modified and uncommitted (the check diffs against the index); the
regenerated file was byte-identical to the one in the working tree (md5 compared
before and after), and the check passed once the file was staged.

#### 2. The scope and boundary tests were mutated, and failed

| Mutation | Result |
|---|---|
| single-server `authorized.permits(...)` removed | `test_scope_is_enforced_on_a_single_server_read` and `test_range_is_scope_checked_too` fail |
| comparison `readable_servers(...)` predicate removed | `test_out_of_scope_servers_are_dropped_and_counted` and `test_an_environment_selection_returns_what_the_caller_may_read_there` fail |
| threshold `>=` → `>` | `test_the_boundaries[80.0-warning]` fails |
| page maps `null` → `0` (`value ?? 0`) | `draws the null point as a gap through the real chart` fails |
| card renders a value whatever the state | the stale, unavailable and unsupported card tests fail (3) |

Each file was restored and the suite rerun green. One slip worth recording: the
restore of `thresholds.py` was first attempted with `git checkout`, which does nothing
for an untracked file, so the mutation stayed in place until it was reverted by hand
and the file grepped. It was caught because the grep was done, not assumed.

#### 3. `./scripts/smoke-test.sh` — SMOKE TEST PASSED, step 18 extended

```
18. Prometheus scrapes the host exporter and the templates query it (§11.8)
  agent_up: 1.0 · cpu_utilisation: 91.29 …
  scrape timestamp: 2026-10-16T05:59:12.920000+00:00 (6.7s before evaluation)
  current: cpu_utilisation=90.76, load_average=1.97, agent_up=1.00
  history: 41 points, 40 holes, none zero-filled
  an unscraped instance returns no series, not a zero
stack returned to the same healthy state from empty volumes
SMOKE TEST PASSED
```

The 6.7 s is the finding: `timestamp()` returned the sample's time, not the evaluation
time, which is what `collected_at` and `stale` rest on. The 40 holes are a cold stack's
real leading gap. This run ends in `down -v`, which removed the stack's volumes, so
PostgreSQL and Redis were brought back up before the second `check-all.sh`.

#### 4. In a real browser, on the running stack

Logged in through the mock IdP as the bootstrap administrator, registered
`node-exporter:9100` as a server (the registration call was a `fetch` from the page,
because clicks on the form missed their fields — the same endpoint the form uses), and
opened `/servers/:id/metrics` and `/compare`. Real values appeared (CPU 76.3 %, memory
21.6 %, 6.0 GiB available, uptime 53 m, agent *Up*) with UTC collection times; the CPU
and memory charts showed one real point at the right edge after an empty stretch, with
the threshold lines labelled in text; the comparison drew the same. **This is a manual
observation, not a regression test (issue 60).**

### Not executed in M2.2, and therefore not claimed

- **The Playwright E2E suite** (`npm run test:e2e`). It needs the stack up and seeds its
  own users; it was not run, and it has no test for the new pages (issue 60).
- **The `compose`, `e2e`, `env-example` and other jobs on GitHub Actions.** Nothing has
  been pushed; the M2.1 handover's request to watch CI still stands, and the effect of
  M2.2's smoke-test extension on CI time is unmeasured.
- **A `stale` reading against a real Prometheus** (issue 62), **a Windows host**
  (`windows_exporter` cannot run in a Linux container; `OPEN-09`), and **any browser
  other than the in-app one**.

### Executed — M2.1 (2026-10-16)

Three commands, in this order, on Windows 11 against the Compose stack
(PostgreSQL 17.2 on `127.0.0.1:5433`, Redis 7.4.1 on `127.0.0.1:6380`).

#### 1. `uv run pytest -m integration` — **237 passed**, 496 deselected, 57.80 s

```
cd backend && TEST_DATABASE_URL=postgresql+psycopg://dcp:...@127.0.0.1:5433/dcp_test \
  TEST_REDIS_URL=redis://127.0.0.1:6380/1 uv run pytest -m integration
237 passed, 496 deselected, 1 warning in 57.80s
```

Run with `-m integration` and never as a module subset, per the trap M1.7 recorded:
two pytest sessions against `dcp_test` at once have the session-scoped `migrated`
fixture drop the schema under each other. **Six of the 237 are new**
(`tests/integration/test_metrics_api.py`): a Viewer reading §15.4, a caller with no
grant refused with `forbidden` **and asserted not to be a CSRF refusal**, an unknown
query parameter answered `422`, the Viewer/Administrator split across the two
adjacent routes, §26.1 reporting `not_configured` for an unconfigured Prometheus, and
the catalogue asserted not to leak any PromQL fragment.

#### 2. `./scripts/check-all.sh` — **18/18 passed, nothing skipped**

| Check | Result |
|---|---|
| backend: ruff lint | ok |
| backend: ruff format | ok (117 files) |
| backend: mypy | ok — `no issues found in 72 source files`, strict |
| backend: bandit | ok |
| backend: unit tests | **496 passed**, 237 deselected, 27.38 s |
| backend: integration | **237 passed**, 496 deselected, 104.32 s |
| backend: pip-audit | No known vulnerabilities found |
| frontend: eslint | ok |
| frontend: prettier | ok |
| frontend: tsc | ok |
| frontend: vitest | **115 passed**, 13 files |
| frontend: build | ok |
| frontend: npm audit | 0 vulnerabilities |
| contracts: api types | ok — regenerated, no drift |
| config: .env.example | `.env.example is complete` — 42 keys, 31 settings, 28 compose vars |
| coverage: backend diff | **388/388 changed lines covered (100.0 %)**, floor 80 % |
| coverage: frontend diff | `no coverable line changed against origin/main` |
| docs: link check | ok — 410 relative links in 13 files, 0 problems |

`ALL CHECKS PASSED`, exit 0. Figures from the **final** run, which is the one that
includes every change in the diff.

**It was run three times, and the middle run failed — recorded rather than
discarded.** That run reported `Tests 1 failed | 114 passed (115)` from the frontend
suite, which also failed the frontend coverage gate as a consequence (a failed vitest
run leaves no usable Cobertura report). **The failing test's name was not captured**,
because the invocation was filtered down to the per-check summary lines. It did not
reproduce in five subsequent runs — one plain `vitest run`, three
`vitest run --coverage`, and the final `check-all.sh` above — all 115 passing. The
failing run was the most loaded of the set, following a two-minute backend integration
suite with the whole Compose stack up; the frontend suite sets no `testTimeout`, so
vitest's 5 s default and Testing Library's 1 s `waitFor` default apply to the nine
test files that use `waitFor`. That is a plausible mechanism, not a diagnosis, and
M2.1 changed no frontend source. Issue **59**, with what to capture if it recurs.

**Unit tests went 360 → 496, integration 231 → 237.** The new unit tests are **121 in
`tests/metrics/`** — 63 in `test_templates.py` (the ten pinned queries and a 20-case
injection suite, both parametrized), 38 in `test_client.py`, 13 in `test_guards.py`
and 7 in `test_status.py` — plus 15 config cases for the two new settings and the
tightened URL validator. Frontend stays at 115: M2.1 changed no frontend source, and
the only frontend file in the diff is the regenerated `src/api/schema.ts`, which the
coverage gate correctly reports as *not in its report* rather than as uncovered.

**The backend coverage gate's first real verdict was wrong, and that is the finding,
not the number.** Its first run printed `17/21 changed lines covered (81.0%)` — a
pass — having skipped the whole new package, because `git diff` cannot see untracked
files. After the fix the same diff measured 388 lines; the tests the gate then asked
for were written, and the floor was left at 80 %. Full account in issue 56.

#### 3. `./scripts/smoke-test.sh` — **PASSED**, exit 0, §11 steps 1–12 with step 8 partial

The step that matters for this milestone is the new **step 18**, which is §11.8's
Prometheus half — the one smoke-test step M1.7 deliberately left uncovered. It starts
`prometheus` and `node-exporter`, waits for both healthchecks, and then runs its
assertions **inside the backend container through `app/metrics/client.py` and
`app/metrics/templates.py`**, so what passed is the code path the API will use rather
than a `curl` that happens to produce the same JSON. Verbatim from the run:

```
=== 18. Prometheus scrapes the host exporter and the templates query it (§11.8)
Container dcp-node-exporter-1 Healthy
Container dcp-prometheus-1 Healthy
  querying http://prometheus:9090
  agent_up: 1.0 at 2026-10-15T20:19:06.076000+00:00
  memory_utilisation: 22.388924416681533 at 2026-10-15T20:19:06.101000+00:00
  uptime_seconds: 3745.12299990654 at 2026-10-15T20:19:06.123000+00:00
  load_average: 1.11 at 2026-10-15T20:19:06.146000+00:00
  cpu_utilisation: 45.08246242549748 at 2026-10-15T20:19:06.195000+00:00
  memory_available_bytes: 6342983680.0
  disk_utilisation: 7.688657601093052
  disk_available_bytes: 997979009024.0
  disk_io: 215238.36449438668
  network_throughput: 512.4316634233936
  an unscraped instance returns no series, not a zero
  WINDOWS resolves to unsupported, not to a Linux query
  prometheus: exporter up and templated queries return data
```

**Run twice.** The first full run passed with step 18 asserting five templates; step
18 was then widened to cover all ten (see below) and the whole script re-run, which
is the run quoted above. The intermediate check of the widened step alone is not
what is being claimed here.

Four things in that output are worth more than the fact that it passed.

- **The poll budget is there for a reason that was observed, not assumed.** In the
  run quoted above all five timestamps fall within 120 ms, because Prometheus had
  already been scraping when step 18 started. In the **first** run, against a
  Prometheus that had just been created, `cpu_utilisation` arrived 15 seconds after
  the other four (`20:14:10` against `20:13:55`) — its `rate()` window waiting for a
  second sample. That is exactly the case the 150-second deadline covers, and it is
  why the step polls rather than querying once.
- **All ten templates were executed against live data**, and the two with label
  filters are the interesting ones: `network_throughput` saw two interfaces
  (`eth0`, `lo`) and one after the virtual-interface filter, so the filter is
  demonstrably doing its job rather than merely parsing. `disk_utilisation` resolved
  through three `ext4` filesystems — the container's bind mounts, which report the
  Docker host's disk.
- **`an unscraped instance returns no series`** is the honest-absence path:
  `cpu_utilisation` rendered for `nowhere.invalid:9100` came back empty, not zero.
- The queries ran against `http://prometheus:9090` read out of
  `client.settings.prometheus_url` **inside the container**, which is the only check
  that a new setting's Compose passthrough actually works — the half
  `scripts/check_env_example.py` structurally cannot see (issue 54).

**What step 18 asserts, and the one place it deliberately does not.** Five templates
(`agent_up`, `memory_utilisation`, `uptime_seconds`, `load_average`,
`cpu_utilisation`) are asserted to return a value in range; the other five are
asserted only to be **valid PromQL**, with whatever value they produce printed. The
split is not laziness. `node-exporter` runs in a container with no host mounts, so
which filesystems, disks and interfaces exist is an environment detail: all five
answer on the development machine, but a runner with a different storage driver could
legitimately expose none of them, and a smoke test that failed there would be wrong
about the code. Prometheus answers a syntactically invalid query with `400`, which
the client maps to `provider_error`, so "no exception raised" *is* the syntax
assertion — and syntax is the real risk for exactly those five, since they are the
templates carrying label filters and `without` clauses.

**One filter is rendered and valid but not shown to exclude anything here.** The
pseudo-filesystem exclusion (`tmpfs|overlay|squashfs|…`) matched three filesystems
before filtering and three after, because this exporter's container exposes no
`tmpfs` mount to drop. The expression is correct and executed; that it *removes* the
right things is untested, and will be first exercised against a real registered
server.

### Executed — `TS-01`, the charting-library decision (2026-10-16)

The decision was made **with the test in hand**, which is what PROGRESS §9 asked for
and what [TECH_STACK.md §2.5](docs/TECH_STACK.md#25-charting-library) means by "the
gap-rendering criterion must be tested, not assumed". The gate was written first, its
own failure modes were proven, and only then were candidates run through it. Every
number below was measured, not estimated.

#### The gap gate, and the proof that it can fail

`frontend/src/components/chart/linePath.test.ts`, **12 tests, all passing.** It
asserts on the geometry of the rendered SVG path — subpath count *and* vertex count
— because the two defects have opposite signatures and a test that checks only one
misses the other:

| Defect | Signature | What an operator reads |
|---|---|---|
| Coercion (`null` → `0`) | continuous line, **gains** a vertex on the baseline | a silent host as an idle one |
| Bridging (`connectNulls`, `spanGaps`) | continuous line, **loses** a vertex | invented data as measured data |

**The gate was then run against a deliberately broken version of the product code**,
which is stronger evidence than the synthetic paths in its own `describe('the gate
itself')` block. Removing `.defined()` from `linePath`:

```
 × breaks the line at a missing sample
 × plots no vertex on the baseline
 × keeps the two runs on either side of the hole
 × draws leading and trailing holes as holes too
 × draws every-other-sample loss as separate points, not a trend line
 × renders nothing for a series with no data at all
AssertionError: continuous line with 5 vertices for 4 real points: the gap was
  coerced to a value (PRD §4.2) — path: M0,100L100,100L200,200L300,100L400,100
 Test Files  1 failed (1)
```

**6 of the 12 failed**, naming the rule and showing the coerced path. Restored, all
12 pass.

#### The candidates, through the same gate

| Candidate | Gate | Bundle (gzipped) | Notes |
|---|---|---|---|
| **`d3-shape` + `d3-scale`** (chosen) | **PASS** — `M0,100L100,100M300,100L400,100`, 2 runs, 4 vertices | **14,926 B** | Gap rule is ours, in one function |
| Recharts 3.10.1 | PASS by default — `M5,36.67L102.5,36.67M297.5,36.67L395,36.67` | **129,306 B** | Rejected: size, and `role="application"` + 13 tabbables |
| visx 4.0.0 | PASS with `defined` | 18,480 B | Rejected: a React wrapper over the two packages above |
| uPlot 1.6.32 | **UNTESTABLE** | not measured | Rejected: canvas, and `TypeError: matchMedia is not a function` under jsdom — it cannot even be imported |
| Hand-rolled, zero deps | PASS | **348 B** | Adopted for the geometry; has no tick selection, which M2.2 needs |

Each candidate was also run through the gate **with its defect switched on**, so the
gate's discrimination is evidence rather than assertion: Recharts with
`connectNulls` → `FAIL: the gap was bridged`; Recharts fed `null → 0` → `FAIL: … 5
vertices for 4 real points … coerced`; visx without `defined` → the same coercion
failure.

#### Bundle impact on the application

```
before: dist/assets/index-CeSHwmyG.js  313,223 B raw   94,583 B gzipped
after:  dist/assets/index-CeSHwmyG.js  313,223 B raw   94,583 B gzipped
```

**Byte-identical, same content hash.** Nothing imports `linePath` yet, so the
dependency is tree-shaken out entirely; the +14,926 B arrives when M2.2 wires it into
a page. Measured against the precedent this repository already set —
`react-router-dom`'s +18 kB gzipped, recorded in TECH_STACK §2.6 as "modest" — that
is below the bar, where Recharts' +129 kB would have **more than doubled the whole
application bundle**.

#### Supply chain

Ten runtime packages, each `package.json` read rather than the top two assumed:
`d3-shape`, `d3-scale`, `d3-array`, `d3-color`, `d3-format`, `d3-interpolate`,
`d3-path`, `d3-time`, `d3-time-format`, `internmap`. **All ISC.** Four dev-only
`@types` packages come with them (two named, two transitive), and the lockfile was
checked to confirm nothing a rejected candidate pulled in survived it. `npm audit
--audit-level=high` reports 0 vulnerabilities, which matters because it is a gate in
`scripts/check-all.sh` and in CI.

#### Full suite after the change

`./scripts/check-all.sh` — **ALL CHECKS PASSED, 18/18, exit 0, nothing skipped.**
Backend 496 unit + 237 integration (unchanged: no backend file was touched),
frontend **127 passed** (115 + the 12 new) across 14 files, `tsc --noEmit` clean under
`strict` + `exactOptionalPropertyTypes` + `noUncheckedIndexedAccess`, eslint and
prettier clean, `npm audit --audit-level=high` **0 vulnerabilities**, and the build
confirming the unchanged bundle (`index-CeSHwmyG.js`, gzip 95.00 kB).

**The frontend changed-line coverage gate judged `linePath.ts` at 10/10 (100.0 %)** —
which it could only do because of the fix recorded in issue 56 earlier the same day.
Before that fix the file would have been untracked and therefore invisible to the
gate, and this decision's one piece of product code would have shipped unjudged.

### Executed — `./scripts/check-all.sh`, 18/18 passed (M1.7, 2026-10-15)

Run from `D:/devops-dashboard` against the live Compose stack, both variables set so
nothing skipped. The suite is **18 checks** now rather than 15: `.env.example`
completeness and the two changed-line coverage gates are new.

| Check | Result |
|---|---|
| backend: ruff lint | ok |
| backend: ruff format | ok |
| backend: mypy | ok |
| backend: bandit | ok |
| backend: unit tests | **360 passed**, 231 deselected, 1 warning, 23.90s |
| backend: integration | **231 passed**, 360 deselected, 1 warning, 63.83s |
| backend: pip-audit | No known vulnerabilities found |
| frontend: eslint | ok |
| frontend: prettier | ok |
| frontend: tsc | ok |
| frontend: vitest | **115 passed**, 13 files |
| frontend: build | ok |
| frontend: npm audit | 0 vulnerabilities |
| contracts: api types | ok — regenerated, no drift |
| **config: .env.example** (new) | `.env.example is complete` |
| **coverage: backend diff** (new) | `no coverable line changed against origin/main` |
| **coverage: frontend diff** (new) | **26/26 changed lines covered (100.0%)**, floor 80% |
| docs: link check | ok — 400 relative links in 13 files, 0 problems |

`ALL CHECKS PASSED`, exit 0.

**The backend numbers are identical to M1.6's and issue 44's — 360 and 231.** That
is the evidence for "no backend code changed": this milestone touched no Python
file, and the suites agree. The frontend went from 108 to 115 (the seven
`Applications.test.tsx` tests the coverage gate asked for); 108 was itself 105 plus
the three `useLatestResponse` tests.

The backend coverage gate reporting "no coverable line changed" is the correct
answer, not a skip — and it is the reason that gate has not been exercised against a
real backend diff yet. The frontend one has: it **failed at 60 %** before the
application tests were written.

### Executed — `./scripts/smoke-test.sh`, PASSED (M1.7, 2026-10-15)

**Run twice, and the first run failed**, which is worth recording because the
failure is the milestone's main finding.

Run A — failed at step 19 with `FAIL: migrations are not at head after a volume
wipe`. Steps 1–18 had passed. Nothing applied the schema, so a stack from empty
volumes was healthy and unusable (see [§3](#3-in-progress-milestone)).

Run B, after the `migrate` service was added — **`SMOKE TEST PASSED`**, exit 0, and
this run exercised the from-nothing path end to end:

```
=== 11. Resource limits are declared for the core services (§11.12, partial)
8 services declare a cpu limit
NOTE: no production overlay exists; its limits cannot be checked
=== 12. Worker and beat are connected, and ping round-trips (§11.9)
ping -> pong
beat state=running, scheduler started
=== 13. Mock IdP login completes end to end (§11.7)
callback landed on: http://localhost:5173/dashboard
logged in as admin@example.invalid
=== 14. Demonstration mode is reported by the API (§11.6)
demo_mode: true
=== 15. Granting the first administrator through the application
bootstrap exit 0 (0 granted, 2 already present)
=== 16. Seeding synthetic inventory
seed_created applications=3 servers=6
=== 17. Seeded inventory is visible through the API (§11.5)
server list returned 6 rows, visibly synthetic
=== 18. A database outage degrades honestly, with no zeros (§11.10)
readyz=503, healthz=200, and no zero was substituted for an unknown
=== 19. down -v then up reaches the same healthy state (§11.11)
stack returned to the same healthy state from empty volumes
SMOKE TEST PASSED
```

A third run after the final compose changes also passed, starting from a populated
volume (`bootstrap exit 2`, `seed 0/0`, 45 rows) — so both the first-run and the
re-run paths are evidenced.

### Executed — E2E suite, 11/11, five consecutive runs (M1.7, 2026-10-15)

`npm run test:e2e` against the Compose stack, serially, nothing else running, with
the stack started using the **E2E overrides docs/TESTING.md §16 documents** —
`DCP_AUTH_RATE_LIMIT_MAX=100` and `DCP_DASHBOARD_CACHE_TTL_SECONDS=0`:

```
--- run 1 ---  11 passed (54.0s)
--- run 2 ---  11 passed (43.3s)
--- run 3 ---  11 passed (41.8s)
--- run 4 ---  11 passed (42.1s)
--- run 5 ---  11 passed (40.9s)
```

**The override matters and the figure would be dishonest without it** (issue 55).
At the default limit of 10 logins per 300s the suite exhausts the `/auth/*` rate
limiter after roughly three consecutive runs: a separate five-run series on a
default stack passed, passed, passed, passed, then failed, and the limiter key read
`11`. That is the control working as designed — it fails closed — not a defect, but
it means "five clean runs" is only meaningful with the configuration stated. A
single run on a fresh stack, which is what CI does, uses three logins and is well
inside the default.

**Two earlier measurements were discarded and should not be read as results.** One
batch was run while two other background suites were competing for the machine; it
produced 1 and 9 unit-test failures and several E2E failures, all timeouts in files
the change did not touch, and none of them reproduced serially. A second batch ran
against a **stale frontend bundle** (issue 53). The five runs above are the first
clean serial measurement of the current code.

Composition: 5 authorisation tests (§12.8, including the direct-API half), 3
critical-path tests (§12.1, §12.2), 3 `axe-core` passes over the dashboard, the
server list and the registration form — in a real browser, with the contrast rule
**enabled**, which jsdom cannot do.

### Executed — developer setup from a fresh clone (M1.7, 2026-10-15)

The M1.7 acceptance criterion. **Precisely what this was:** the branch was cloned
with `git clone --branch phase-M1.7` into a new empty directory outside the working
tree, `.env` was created from `.env.example`, and the README's quick-start command
was run verbatim. Docker volumes and the Compose project were destroyed first, so
the database and Redis were genuinely empty.

**This was *not* a clean machine**, and the claim is not made. The host already had
Docker, git, Python and Node installed, and Docker's image layer cache was warm, so
the build was faster than a first-ever build would be. What was fresh: the checkout,
the volumes, the containers and the `.env`. What was not: the host toolchain and the
image cache. A genuinely clean machine remains unverified.

Results, all read from the running stack:

```
Container dcp-migrate-1   Exited (0)
Container dcp-postgres-1  Healthy      dcp-redis-1     Healthy
Container dcp-backend-1   Healthy      dcp-worker-1    Healthy
Container dcp-beat-1      Healthy      dcp-frontend-1  Healthy
Container dcp-mock-idp-1  Healthy

/readyz  -> {"status":"ok","checks":{"database":true,"broker":true,"migrations":true}}
alembic  -> 0004 (head)
SPA      -> <div id="root">
dcp_test -> created automatically by infra/compose/postgres-init/
login    -> landed: http://localhost:5173/dashboard
session  -> signed in as: admin@example.invalid | roles: [] | demo_mode: True
```

`roles: []` is the documented behaviour, not a fault: every account starts with no
roles and there is no bootstrap administrator by design.

**The first attempt at this exposed fault 3** — the login redirect went to
`:8000`, which serves no SPA, because `.env.example` carried the native-development
value. The run above is after that fix.

### Not executed, and therefore not claimed

- **The `e2e` and `env-example` CI jobs have never run on GitHub Actions.** Both are
  configured in `.github/workflows/ci.yml` and both pass locally, but no push has
  exercised them. The acceptance criterion says "E2E suite passes in CI"; what is
  evidenced is "passes locally against the same stack CI builds". The first PR will
  settle it.
- **The coverage gate has never run on a backend diff**, because this milestone
  changed no backend file.
- **A genuinely clean machine** — see above.
- `pre-commit`, `gitleaks` and `trivy` are still not installed locally (issue 26).

### Executed — `./scripts/check-all.sh`, 15/15 passed (issue 44, 2026-10-15)

Run from `D:/devops-dashboard` against the live Compose stack, both variables set so
nothing skipped. The integration suite was also run on its own first, as
`uv run pytest -m integration`, before the full script — **never concurrently**
(issue 51's first trap: the session-scoped `migrated` fixture drops the schema under
a second session).

| Check | Result |
|---|---|
| backend: ruff lint | ok |
| backend: ruff format | ok |
| backend: mypy | ok |
| backend: bandit | ok |
| backend: unit tests | **360 passed**, 231 deselected, 1 warning, 18.76s |
| backend: integration | **231 passed**, 360 deselected, 1 warning, 54.31s |
| backend: pip-audit | ok |
| frontend: eslint | ok |
| frontend: prettier | ok |
| frontend: tsc | ok |
| frontend: vitest | **105 passed**, 11 files |
| frontend: build | ok |
| frontend: npm audit | 0 vulnerabilities |
| contracts: api types | ok — regenerated, **no drift** |
| docs: link check | ok — 397 relative links in 13 files, 0 problems |

`ALL CHECKS PASSED`, exit 0.

The backend numbers are **identical** to M1.6's (360 / 231), which is the point: this
change touched no backend file. The frontend went from 76 to **105** — 29 new tests
in two new modules, `ServerEditForm.test.tsx` (18) and `ServerDetail.test.tsx` (11),
both including an `axe-core` pass.

Standalone integration run, before the script:

```
231 passed, 360 deselected, 1 warning in 58.49s
```

**One flake found and fixed during this session, in the new tests rather than in the
application.** Two association tests selected a candidate from the `<select>` after
waiting only for the *server* payload, while the options come from §18.1 — a second
request. They passed when the file ran alone and one failed inside the full
`check-all.sh` run. Both now wait for the option itself; `npx vitest run` was then
run **three consecutive times, 105 passed each**, before the full script. Worth
knowing before M1.7's E2E suite: a page that fetches twice has two things to wait
for, and waiting on the first reads as a passing test.

### Executed — `./scripts/check-all.sh`, 15/15 passed (M1.6, 2026-10-15)

Run from `D:/devops-dashboard` against the live Compose stack, with both variables set
so nothing skipped:

```
TEST_DATABASE_URL=postgresql+psycopg://dcp:placeholder-password@127.0.0.1:5433/dcp_test
TEST_REDIS_URL=redis://127.0.0.1:6380/1
```

| Check | Result |
|---|---|
| backend: ruff lint | ok |
| backend: ruff format | ok |
| backend: mypy | ok — 66 source files |
| backend: bandit | ok |
| backend: unit tests | **360 passed**, 231 deselected, 1 warning, 18.68s |
| backend: integration | **231 passed**, 360 deselected, 1 warning, 50.84s |
| backend: pip-audit | ok |
| frontend: eslint | ok |
| frontend: prettier | ok |
| frontend: tsc | ok |
| frontend: vitest | **76 passed**, 9 files |
| frontend: build | ok |
| frontend: npm audit | 0 vulnerabilities |
| **contracts: api types** (new) | ok — regenerated, no drift |
| docs: link check | ok — 386 relative links in 13 files, 0 problems |

`ALL CHECKS PASSED`, exit 0. The suite is 15 checks now rather than 14: the API-type
drift gate was added by this milestone.

**The caveat on the integration number, now settled.** When this milestone was
written the integration suite was **not reliably clean**: across roughly twelve full
runs on 2026-10-15, two failed, each time one of two *pre-existing* M1.3/M1.4 tests
with an order-dependent audit-row assertion, never a dashboard test. That cause was
fixed later the same day (issue 51, now RESOLVED) and the suite has since run **16
consecutive times clean**, 8 of them with this milestone's tests present at 231
passed. Sixteen clean runs against a roughly one-in-six failure rate is strong but
not proof on its own; the stronger argument is structural — the assertions no longer
depend on row ordering at all.

#### Verified by hand, over HTTP against the running stack (2026-10-15)

Logged in through `http://localhost:5173` as `admin@example.invalid` (ADMINISTRATOR,
full scope) via the mock IdP.

- `GET /api/v1/dashboard/summary` → `total 6, pending 1, managed 3, stale 1,
  unreachable 1, retired 0`. Cross-checked against the database directly
  (`SELECT count(*) … GROUP BY status` over `deleted_at IS NULL`): **identical**.
- `failed_services.count` → `0`, `data_state: "ok"`, cross-checked against
  `SELECT count(*) FROM services WHERE last_observed_state = 'FAILED'` → `0`.
- Six sections reported `not_configured` with `null` values, each naming its
  milestone.
- `?environment=production` → `total 3, managed 2, stale 1`; database agrees.
- `?bogus=1` → `422`. `?environment=nope` → `422`.
- `GET /api/v1/dashboard/health?limit=2` → 2 rows, `has_more: true`, a signed cursor;
  following it returned the next two rows, different servers.
- Two consecutive summary calls returned an **identical `collected_at`**, i.e. the
  second was served from cache.
- `?health_status=healthy` → `items: []` with `health.data_state: "not_configured"`.

#### Verified in a real browser (2026-10-15)

- `/` lands on `/dashboard`, not `/servers`.
- Counts on the page match the API and the database.
- Every unavailable card reads "Not configured — … (M2.1/M3.3/M5.1/M4.2/M2.5)".
  **No zero appears anywhere except `Failed services`, which has a source.**
- The status table shows `✕ Unreachable`, `◐ Stale`, `◌ Pending`, `● Managed` —
  glyph *and* word *and* colour — and the Health column shows `? Unknown` for all six
  servers, never "Healthy".
- Open alerts reads "Not configured", not `0`.
- Selecting Environment = production narrowed the cards to 3/2/1/0 and the table to
  the three production servers (PRD §4.2 acceptance criterion).
- `collected_at` is displayed for the summary and for the status table.
- `/servers` and `/applications` render correctly on the replacement components after
  `pages/inventory/states.tsx` was deleted.

#### The two security checks were shown to fail before being relied on

Rather than asserting that the tests pass, each was run against a deliberately broken
implementation first:

- `_scope_fingerprint` stubbed to a constant (a scope-blind cache key) →
  `test_two_scopes_produce_different_cache_keys`,
  `test_an_added_deny_changes_the_cache_key` and
  `test_a_scope_change_is_not_served_from_the_previous_cache_entry` **all failed**.
  Restored → all pass.
- `Card` changed to render `{value ?? 0}` (the `null`-becomes-zero regression PRD §4.2
  warns about) → `renders an unavailable metric as unavailable, never as 0` and
  `names the milestone that will provide each missing section` **both failed**.
  Restored → all pass.

### Executed — `./scripts/check-all.sh`, 14/14 passed (M1.5, 2026-10-14)

| Check | Command | Result |
|---|---|---|
| Backend lint | `uv run ruff check .` | **pass** — "All checks passed!" |
| Backend format | `uv run ruff format --check .` | **pass** — 99 files |
| Backend types | `uv run mypy` (strict) | **pass** — "no issues found in 61 source files" |
| Backend security lint | `uv run bandit -q -r app` | **pass** — no findings |
| Backend unit tests | `uv run pytest -m "not integration"` | **349 passed**, 209 deselected, 1 warning, 18.72s |
| Backend integration tests | `uv run pytest -m integration` | **209 passed**, 349 deselected, 1 warning, 42.96s |
| Backend dependency audit | `uv run pip-audit` | **pass** |
| Frontend lint | `npx eslint .` | **pass** |
| Frontend format | `npx prettier --check .` | **pass** |
| Frontend types | `npx tsc --noEmit` | **pass** |
| Frontend tests | `npx vitest run` | **55 passed** (6 files), 7.45s |
| Frontend build | `npm run build` | **pass** — 57 modules, 293.54 kB JS / 90.73 kB gzip |
| Frontend audit | `npm audit --audit-level=high` | **pass** — 0 vulnerabilities (see the react-router version note in [§3](#3-in-progress-milestone)) |
| Documentation links | `python scripts/check_doc_links.py .` | **pass** — 363 links, 13 files, 0 problems |

Both `TEST_DATABASE_URL` and `TEST_REDIS_URL` were set (the existing `dcp_test`
database on the Compose PostgreSQL, Redis database `1`), so **nothing skipped**.

New in M1.5: **79 backend unit tests** (request-schema validation, cursor
encode/decode and tamper rejection, the query-parameter allowlist, four new matcher
cases for the `A-09` amendment) and **77 integration tests** (the server lifecycle,
duplicate detection, applications, and the scope cross-check, which is 28 of them).
Frontend: **16** new tests across `ServerList` and `ServerForm`.

Three pre-existing tests were changed, each because M1.5 made a claim they encoded
no longer the whole truth:

| Test | Why it changed |
|---|---|
| `test_soft_deletable_tables_have_partial_unique_indexes` | The new idempotency indexes are unique and partial but deliberately **not** on `deleted_at IS NULL` — a key must stay claimed after a retire. The test now asserts both rules separately instead of one loosened rule |
| The three downgrade tests in `test_migrations.py` | Issue 48 — they need a rebuilt schema now that another suite creates inventory rows |
| `test_an_idempotency_key_is_required` (new, but worth noting) | FastAPI reports the header alias as written, `Idempotency-Key`, not lowercased |

#### Verified by hand against the running Compose stack

Driven over HTTP as `admin@example.invalid` (the M1.4 bootstrap administrator),
through the frontend dev server's proxy so the whole origin and cookie path is the
real one:

| Step | Result |
|---|---|
| `/readyz` after rebuilding the backend image | `200 {"status":"ok","checks":{"database":true,"broker":true,"migrations":true}}` — the M1.4 leftover is cleared |
| `GET /servers?limit=3&sort=display_name` | 3 of 6 seeded rows, `next_cursor` present, `total: null`, `demo_mode: true` |
| `GET /servers?environment=production` | 3 rows, all production |
| `GET /servers?enviroment=x` (typo) | `422`, `unknown_field`, listing the 14 allowed parameters |
| `POST /servers` | `201`, `status: PENDING`, `idempotent_replay: false` |
| `POST /servers` again, same key and body | `200`, same id, `idempotent_replay: true`, **no second audit row** |
| `POST /servers` again, new key, same resource id | `409 duplicate_resource` |
| `PATCH /servers/{id}` `{"environment":"production"}` | `200`, and **two** audit rows: `SERVER_UPDATED` and `SERVER_ENVIRONMENT_CHANGED` |
| `DELETE /servers/{id}` then `GET` | `204`, then `404` |
| Audit trail for that server | `SERVER_REGISTERED → SERVER_UPDATED → SERVER_ENVIRONMENT_CHANGED → SERVER_RETIRED`, all `SUCCESS`, environment recorded |
| `GET /applications/{id}` | one application across 3 servers in 2 environments — PRD §4.9 in real data |
| `POST /applications` with `http://169.254.169.254/...` in `health_check_config` | `422` at `["body","health_check_config","checks","0","url"]` |
| `PATCH /applications/{id}` `{"slug":"renamed"}` | `422` — slug is immutable |
| `/auth/session` | `admin:integrations` present in the advisory list, `integration:manage` gone |

#### Verified in a real browser

The Compose stack with both profiles, driven through the UI: a deep link to
`/servers` while signed out renders the login page and returns to `/servers` after
authenticating; the demonstration banner is shown; the server list renders the six
seeded `[DEMO]` rows with their five distinct statuses; the detail page reports
**"Never seen"**, **"Unknown — never seen"** and **"Not configured — … (M2/M3/M4)"**
rather than zeroes; the applications list and nav work.

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
| Performance tests | none exist yet (M6.3); **no performance figure has been measured** — including for the inventory list query, whose scope predicate and keyset page are untimed against anything larger than six rows |
| Inventory at realistic scale | The seeded set is **6 servers and 3 applications**. Cursor paging, the scope predicate and the `ilike` search are all correct on that; none has been run against thousands of rows, and the `lower(hostname)` index is not a trigram index, so a non-prefix search would not use it |
| A generated-then-committed TypeScript API client | Issue 43 — the types are hand-written, so the §11 drift gate does not exist and cannot have passed |
| Backup/recovery rehearsal | nothing to back up yet (M6.4) |

## 8. External integrations

**One is now configured, connected and read: Prometheus (M2.1, 2026-10-16).** It is
a Compose service on a developer machine, not a production monitoring server, and
the exporter it scrapes measures a container rather than a host — so what is proven
is that the client, the template catalogue and the join label work against a *real*
Prometheus, not that any production estate is being monitored. Everything else in
this table is unchanged: **not configured, not connected, not operational.**

| Integration | Status | Notes |
|---|---|---|
| OIDC identity provider | **Mock only — no real provider configured** | A **mock** OIDC provider (`infra/compose/mock-idp/`, `mocks` profile) is running and in use: discovery, JWKS/RS256, PKCE S256, single-use codes. It **checks no password** and is a development aid, so this is not an operational integration (CLAUDE.md §7). No real provider chosen (`OPEN-01`), no real client registered, no real credential created |
| PostgreSQL | **Running and migrated** | postgres 17.2-alpine via Compose on host port 5433. Schema still at revision `0002` — M1.3 added no migration. Holds two JIT-provisioned users and the auth audit trail. A separate `dcp_test` database serves the integration suite |
| Redis | **Running and in use** | redis 7.4.1-alpine via Compose on host port 6380. Now carries the session store, the single-use OIDC login state and the rate-limit counters (database `0`; the integration suite uses `1`). Verified by the live login flow |
| Prometheus | **Running, scraped and read by the backend — the first real integration in this table** | prom/prometheus 3.0.1 via Compose (`observability` profile) on host port 9090, loopback only. `infra/prometheus/prometheus.yml` is a real config: 15 s scrape interval, two jobs (`prometheus` self-scrape, `node`), 30-day retention. The backend reads it through `app/metrics/client.py` at `DCP_PROMETHEUS_URL` (default `http://prometheus:9090`). Verified by smoke-test step 18, which asserts the exporter target `up` and executes **all ten templates** **through that client** — five with their values asserted in range, five asserted to be valid PromQL (see [§7](#7-tests-executed-and-their-actual-results) for why the split, and for what each returned). Still: **targets are static** — generating them from inventory is `OPEN-14`/M2.3 — and **no alert rules exist** (M3.3). This is a local stack, not a production Prometheus; nothing has been deployed anywhere |
| Node Exporter | **Running and scraped** | prom/node-exporter 1.8.2 in the `observability` profile, host port 9100, loopback only. **Deliberately no host mounts**, so it reports the *container's* CPU, memory and filesystems rather than the Docker host's — mounting the host root into a container is a posture change a development convenience does not justify. It is a real exporter producing real `node_*` series, which is what the templates are verified against; host-level collection on registered servers is the agent's job (M6.1) |
| systemd (unit state) | **Running and read — against a test fixture, not a production host** | `node_exporter`'s systemd collector (`TS-02`), so there is no separate exporter. Enabled only in the opt-in `fixtures` profile's `systemd-host` container (Debian + systemd + the pinned exporter), where step 18b of the smoke test reads it through Prometheus, the worker and the API. **Not enabled on the ordinary `node-exporter` service** (no system bus), and no production host runs it. The exporter-side unit allowlist is host configuration |
| windows_exporter | **Not configured, and deliberately out of scope** | `OPEN-09` now has an implemented working default: **there are no Windows templates at all**, and a WINDOWS server resolves to the `UNSUPPORTED` data state. `windows_exporter` cannot run in a Linux container, so Windows PromQL would be strings never executed against anything — which looks like coverage and is not. The decision stays open |
| Grafana Alloy | **Declared, never started** | Compose `logs` profile + placeholder `infra/alloy/config.alloy` that collects nothing (M3.1) |
| Grafana Loki | **Declared, never started** | Compose service + minimal `infra/loki/loki-config.yaml`. No ingestion (M3.1). Still a placeholder *inside the same `observability` profile* as the now-real Prometheus, which is why `scripts/smoke-test.sh` starts `prometheus` and `node-exporter` by name rather than the whole profile |
| Alertmanager | **Not configured** | M3.3 |
| Grafana | **Declared, never started** | In the `observability` profile; no dashboards and no provisioned data source. DCP renders its own charts (M2.2), so this is for ad-hoc exploration only |
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

### M2.5 plan — D1–D4 APPROVED 2026-10-17 as written; commits 1–3 of 5 done, commits 4–5 not started

**Commit 1 (pure classifier + `DCP_HEALTH_THRESHOLDS`) — done, committed.** `app/monitoring/health.py` holds `HealthThresholds`, `HealthThresholdTable` (a `default` plus the four `servers.environment` values, `extra="forbid"`, every merged resolution validated at startup) and `classify_health(thresholds, last_seen_at, reference) -> HostHealth(state, data_state)`. Tests: `backend/tests/monitoring/test_health_classifier.py` (40). Real results: unit **817 passed**; integration **365 passed** against real PostgreSQL/Redis; `ruff check`, `ruff format --check`, `mypy app` clean; `generate-api-types.sh` leaves `schema.ts` unchanged. A mutation check (`>` → `>=` on the stale line) fails `test_the_time_matrix[300-warning-ok]`. **Not run at the time:** `check-all.sh` — and when it was run for commit 2 it caught a real mismatch from this commit (`.env.example` said `{}`, Compose defaults to empty; fixed in commit 2).

Assumptions to review, none of them in the PRD:

- **Boundaries are strict (`age > line`).** Exactly on a line is still the better state, matching the existing `stale` flag and `is_stale`, so no host crosses a line it was already inside. `metrics.thresholds.classify` uses `>=` (a value against a drawn line); the two are deliberately different and the module docstring says so.
- **`data_state`:** `healthy`/`warning` → `ok`; `stale`/`unreachable` → `stale` (a number exists and `last_seen_at` dates it, API_CONTRACTS §4 rule 3); never seen or no reference time → `unknown` + `unavailable`.
- **`reference=None` ⇒ `unknown`** (D2's "marker missing"); `last_seen_at` after the reference is clamped to age 0.
- **`DCP_SERVER_STALE_AFTER_SECONDS` is removed**, so a deployment that still sets it now fails startup (the unknown-`DCP_*` guard). The server-detail `stale` flag and `health.stale_after_seconds` now read the server's environment `stale` line; behaviour at the defaults is unchanged. Compose now passes `DCP_HEALTH_THRESHOLDS` through and the unlisted-settings count in its comment drops from eleven to ten.
- **Compose trap:** the default is empty, not `{}` — Compose ends `${VAR:-...}` at the first `}` and would append a stray one to any real value. The setting treats empty as `{}`.
- `HealthState` now lives in `app/monitoring/health.py` (re-imported by the schemas), because `core.config` imports it and the schemas import `core.config`.

**Commit 2 (sweep job + migration 0006 + D2 marker + D1 docs) — done, committed.**

- `app/monitoring/sweep.py`: `sweep(engine, settings, reference)` moves a **scraped** (`discovery.select_targets`) server between `MANAGED`, `STALE` and `UNREACHABLE` only; the SELECT is limited to those three and the UPDATE is `WHERE status = <the one read>`, so a person's change made mid-sweep wins. `warning` stays `MANAGED` (no such status). Each move inserts a `SERVER_STATUS_CHANGED` audit row (`SYSTEM`, `system:health-sweep`, metadata `{from, to, health_state, age_seconds, reference_at}`) in the same transaction.
- **D2:** `agents.probe` writes the Redis marker `dcp:monitoring:prometheus_answered_at` after its DB write, **only when Prometheus answered**. The sweep ages hosts against it. Missing, unreadable, naive or non-Redis ⇒ `outcome: no_reference` and **no move at all, including recovery**. Marker writes are best effort (a failure only makes the sweep more conservative).
- `dcp.monitoring.sweep_status` task + beat `sweep-server-status` every 60 s on `integrations`.
- Migration `0006`: `set_servers_updated_at()` also ignores `status`, **only when old and new are both in the three sweep states**.
- Docs: `OPEN-30` (ARCHITECTURE §19), DATABASE.md §4.2 (two paragraphs), API_CONTRACTS §14.4 and the audit catalogue.

Real results (2026-10-17): `scripts/check-all.sh` **ALL CHECKS PASSED, all 18** — unit **826 passed**, integration **388 passed** against real PostgreSQL 17.2 and real Redis, frontend 211 passed, changed-line coverage backend 160/162 (uncovered: `dashboard/service.py` 320–321, commit 1's per-environment stale line on the dashboard rows — commit 3 rewrites that path). Mutation checks fail the expected tests: dropping the owned-status filter, ignoring a missing reference, and putting `PENDING` into the migration's rule. **Not run:** `scripts/smoke-test.sh` (nothing in the stack was exercised; the beat entry, the Compose passthrough and the marker against a real Prometheus are unverified until commit 5's step 18). The rollback is exercised: `test_migrations` has a downgrade/upgrade round trip and a full downgrade to base, both in the 388, and `0006 → 0005 → 0006` was also run by hand on a scratch database.

Assumptions and consequences to review:

- **Never-seen stays as it is.** A `MANAGED` host with `last_seen_at IS NULL` is not moved: no evidence is not old evidence. It reads `unknown` health (commit 3), not `UNREACHABLE`.
- **A host that stops being a target keeps its last status.** A `STALE` server whose monitoring is switched off stays `STALE` until an administrator sets it. Silence nobody is listening to is not judged either way.
- **An administrator's `STALE`→`MANAGED` does not move `updated_at`** (the trigger cannot tell who wrote the row; DATABASE.md says so). The sweep reverts it if the host is still quiet.
- **Issue 67 is resolved by D2**, not by a longer threshold: a Prometheus outage freezes the marker, so nothing ages. A *Redis* flush also freezes everything until the next probe rewrites the marker (≤ `agent_probe_interval_seconds`).
- The sweep reads `last_seen_at` as written by the probe, which only moves forward; the sweep does not read `agent_status`.

**Commit 3 (read path) — done, committed.**

- `app/monitoring/read.py`: `Reference` (the Redis marker, **usable only while fresh = 3 probe intervals**), `evaluate(server, table, reference)` and `health_state_expr(table, reference_at)`, the SQL `CASE` twin. Gates, in order: `RETIRED`, then `normalise.gate` (unsupported OS / monitoring off / no `metrics_instance` — the function the service views already use), then never seen ⇒ `unknown`; only then the time ladder. **No usable reference (unconfigured, never answered, or older than 3 probe intervals) ⇒ nothing is judged: `state` is `null`**, not `unknown`.
- The twin compares timestamps (`last_seen_at < reference - N s`), not an extracted age, so its boundary is exactly the Python `age > N`.
- `app/monitoring/marker.py` now owns the marker (moved out of `sweep.py` to avoid import cycles); `HealthState` lives in `monitoring.health`.
- Dashboard: `server_health` is a real rollup over **every readable server** (sum = `servers.total`; adds `stale` and `unreachable`; `critical` stays `null`, `OPEN-28`); counts are `null` (never `0`) when the reference is unusable. `GET /dashboard/health` rows carry `health_state` and `stale` from `evaluate`, and `health_status` filters in SQL, so keyset paging works over the filtered set. `recent_health_changes` is the latest 10 `SERVER_STATUS_CHANGED` rows in 30 days, scope-filtered through the audit row's target; `[]` is a true "none recorded". Server detail's `health` and `stale` use the same function. `inventory.service._is_stale`/`_stale_after` and the dashboard `_is_stale` are gone.
- Contracts: API_CONTRACTS §13.1, §13.2, §14.3 rewritten; `frontend/src/api/schema.ts` regenerated.

Real results (2026-10-17): `check-all.sh` — 17 of 18 checks passed in the full run: unit **848 passed**, integration **397 passed** (real PostgreSQL 17.2 and Redis), frontend tsc/eslint/prettier/vitest (211)/build clean, changed-line coverage backend 290/303. The 18th, `contracts: api types`, failed because the regenerated `schema.ts` was not staged; re-run alone after staging it, it passed. Mutation checks each fail the expected test: SQL stale line off by one and a dropped `RETIRED` gate (both caught only by the parity test), a reference that ignores freshness, and an unscoped `recent_health_changes`. **Not run:** the smoke test; the UI in a browser.

Consequences and assumptions to review:

- **The dashboard page (commit 4) has not been updated.** It still renders Healthy / Warning / Critical cards and a banner for Health changes; with real data the rollup shows numbers for the first two, there are no cards yet for `stale` / `unreachable` / `unknown`, and a `recent_health_changes` section that is `ok` has no list rendering. Nothing was viewed in a browser.
- **`stale` on a row/detail changed meaning slightly:** it now follows the *health verdict* (`null` for `unknown`, including a monitoring-disabled server with an old `last_seen_at`; before, it was `age > stale line` for any seen host). At defaults the numbers agree for every monitored host.
- **A stale reference hides health everywhere**, including per-server detail, rather than showing a frozen last-known state with a stale label. Chosen for the simplest rule that obeys §4 rule 2; a "last known" display could be added without a contract change.
- **The rollup's `servers.stale`/`unreachable` and `server_health.stale`/`unreachable` are different numbers** (stored status vs computed health) and the UI must label them so.
- The dashboard cache (15 s) is not keyed on the reference, so a marker change shows up after the entry expires, like any other write.

**Next: commit 4 (frontend).** Health on the dashboard and server detail (cards for the five states labelled apart from the status counts, last-collected times, a distinct never-seen state, the `recent_health_changes` list), tests incl. axe, mutation-check the key rule (`null` is never rendered as `0`). Then commit 5 (smoke step 18 with tight thresholds, contract/AD-14 amendments, PROGRESS).


Read PRD §4.2, [ARCHITECTURE.md §4 point 5](docs/ARCHITECTURE.md#4-metrics-collection-architecture) (AD-14) and [IMPLEMENTATION_PLAN.md M2.5](docs/IMPLEMENTATION_PLAN.md#m25--stale-data-detection) first. Branch `phase-M2.5` was cut from `main` after PR #17; the next session starts there.

**What exists:** `servers.last_seen_at` and `agent_status` (M2.3 probe); one global `server_stale_after_seconds` that only drives a computed `stale` flag; `servers.status` already allows `STALE`/`UNREACHABLE` but **nothing sets them**; the dashboard and server-detail `health` sections are still `not_configured` ("M2.5"). AD-14 defaults: warning 2× the scrape interval (30 s), stale 5 min, unreachable 15 min, per environment.

**Four problems the documents do not answer, each of which the plan resolves:**

1. **A Prometheus outage would look like a fleet outage.** `last_seen_at` only advances on a successful read, so 15 minutes of Prometheus downtime would age every host to `UNREACHABLE`. This is issue 67, which M2.5 was asked to decide.
2. **Who owns `MANAGED`?** [DATABASE.md §servers](docs/DATABASE.md) says the system owns `MANAGED`/`STALE`/`UNREACHABLE`; the M1.5 API lets an administrator set `MANAGED` (`SettableServerStatus`).
3. **A sweep flip must not break an admin's edit.** Migration 0005 deliberately left `status` inside the `updated_at` trigger ("M2.5 owns that decision"); a silent flip to `STALE` would make an open edit form fail with `409`.
4. **Thresholds cannot go in `system_settings`**: that table accepts exactly one key (read-only mode), so they belong in configuration.

**Five commits (the shape M2.4 used):**

1. **Pure classifier + settings.** `classify()` takes thresholds, `last_seen_at` and a reference time → health state + `data_state`. Unit-tested across the full matrix: never-seen, fresh, warning, stale, unreachable, every boundary ±1 s, per-environment values. One validated `DCP_HEALTH_THRESHOLDS` setting (`.env.example` + Compose passthrough — issue 54's two-halves trap). `server_stale_after_seconds` is removed.
2. **Sweep job + migration 0006.** `dcp.monitoring.sweep_status` (beat, 60 s) over `discovery.select_targets` (the one predicate). Moves only `MANAGED` ↔ `STALE` ↔ `UNREACHABLE`; never `PENDING`/`RETIRED`, never demo or unscraped servers. Each transition audited as `SERVER_STATUS_CHANGED` with a named system actor (rule 7; add it to the API_CONTRACTS audit vocabulary). Migration 0006 stops `updated_at` moving when `status` changes **between those three states only**; an admin `PENDING`→`MANAGED` still moves it.
3. **Read path.** Real `health_state` + `data_state` on server detail and the dashboard; the rollup and the `health_status` filter use a SQL `CASE` translation of `classify()` **with a parity test, as `displayed_state` has** (`app/services/listing.py`); "stale servers" counter.
4. **Frontend.** Health on the dashboard and server detail, last-collected times, a distinct never-seen state; tests incl. axe; mutation-check the key rule.
5. **Smoke + docs.** Smoke step 18 gets tight thresholds for the run: marks the server `MANAGED` (admin), stops the exporter, expects `STALE` then `UNREACHABLE`, restarts, expects `MANAGED`. Contract/AD-14 amendments; PROGRESS with real results.

**Decisions awaiting the owner (defaults first):**

- **D1 — status ownership:** administrators own `PENDING`→`MANAGED` and `RETIRED`; the sweep owns the three-way `MANAGED`/`STALE`/`UNREACHABLE`. Record as `OPEN-30` and amend the DATABASE.md sentence.
- **D2 — Prometheus-outage guard:** age is measured against the last time Prometheus *answered* (a Redis marker the probe writes), not the wall clock; during an outage the clock stops and nothing ages. Marker missing ⇒ health reads `unknown`, **never** stale/unreachable. Resolves issue 67.
- **D3 — thresholds:** one `DCP_HEALTH_THRESHOLDS` JSON setting with per-environment overrides.
- **D4 — `recent_health_changes`:** built from the audit rows the sweep writes anyway (no new table). PRD §4.2 requires it and the code text promises it for M2.5, though the plan does not list it.

**Explicitly not M2.5:** utilisation-driven `warning`/`critical` health (`OPEN-28`; `critical` reports as unknown, never `0`); alerts on stale hosts (M3.3). **Also open, separate from M2.5:** step 18b is NOT RUN on the hosted runner (issue 69) — reading the fixture's log from that job would say why.

**After M2.4 (2026-10-17), in this order:**

1. **~~Push and watch CI~~ — done 2026-10-17: all ten jobs green on PR #17**, after two rounds that found seven causes ([§6, issues 81–87](#6-known-issues)). The `compose` job takes about three minutes. **The systemd fixture cannot start on the hosted runner, so step 18b is NOT RUN there and the job ends `PASSED, BUT INCOMPLETE`** (issue 69): the next useful CI work is reading *why* from that job's log, not switching `SMOKE_SYSTEMD` to `require`.
2. **M2.5 — stale-data detection.** It builds on `agent_status` and now on `services.last_observed_at`, and should decide issue 67 (how long a status may be trusted) and issue 73 (a per-host rollup for `UNCOLLECTED`). The cache's three-interval staleness bound (`normalise.CACHE_STALE_AFTER_SECONDS`) is the pattern.
3. **A registration/edit screen for services** (issue 72), unless the plan assigns it elsewhere.
4. **`OPEN-28` before M3.3**, unchanged.
5. **E2E path 3** (issue 60) now has three real things to assert: a registered server becomes `HEALTHY`, a stopped exporter becomes `UNREACHABLE`, and a failed unit is visible in the failed-service view.

**What M2.5 should not re-litigate:** the node-exporter systemd collector (`TS-02`); `null` for the four unsupplied fields (`OPEN-29`); lists from the cache and detail from Prometheus; the host-first order in `normalise.py` and its SQL twin in `listing.py` (change one, the parity test fails until you change the other); and that an absent series is never a zero.

**Traps this milestone paid for:** `re.match` with a `$` pattern accepts a trailing newline (use `fullmatch`); an integration test over a global list sees every earlier test's rows (scope to your own servers, as `own()` does); `isolated_fleet` in `test_monitoring.py` is how to keep a job that reads *every* monitored host from naming hosts the test never scripted; `roles.name` is a CHECK-constrained set, so a test cannot invent a role; editing `scripts/smoke-test.sh` while it is running corrupts the run (bash reads it incrementally); and a smoke run ends in `down -v`, so bring Postgres and Redis back before `check-all.sh`.

**The M2.3 hand-over, kept because the first item is still true:**

**After M2.3 (2026-10-16), in this order:**

1. **Push, open the PR, and watch CI** — this is now overdue by two milestones. The
   `compose` job runs step 18, which has grown (registration, a stop and a start of the
   exporter); its duration on a two-core runner is unmeasured, and the step budgets
   (120 s to `HEALTHY`, 150 s to each transition) were tuned on a fast machine.
2. **M2.4 — systemd service monitoring.** `TS-02` is resolved (2026-10-17):
   `node_exporter`'s systemd collector, so **there is no second target kind** — the
   systemd series arrive on the `node` job M2.3 already generates, and the work is the
   allowlist, the normalisation, and the read path. What to build around:
   - there are two allowlists: the exporter's `--collector.systemd.unit-include` is host
     configuration (DCP cannot and must not set it; cardinality is `5 × units`), and
     DCP's own is applied on read — queries name only registered `services` rows;
   - `UNCOLLECTED` = `node_scrape_collector_success{collector="systemd"}=0` **or** no
     systemd series at all; a unit missing from a *successful* collection is also not
     `INACTIVE` (verified: such a unit emits no series);
   - `node_systemd_unit_start_time_seconds == 0` means **unknown**, not 1970;
   - `sub_state`, `load_state`, enabled state and main PID are `null` (`OPEN-29`), and
     `active/exited` reads as `ACTIVE`;
   - the dev `node-exporter` has no system bus, so the integration fixture must be a
     container running systemd, **not** the existing service;
   - the scratch run behind the decision is not in the repository — M2.4 should turn
     it into a standing test, as `TS-01`'s gap test was.
3. **`OPEN-28` before M3.3**, unchanged.
4. **`agent_status` is now real, so M2.5 can build on it** — and should decide issue 67
   (how long a status may be trusted) and whether `status` joins the 0005 exclusion.
5. **E2E path 3** (issue 60), which now has two real things to assert: a registered
   server becomes `HEALTHY`, and a stopped exporter becomes `UNREACHABLE`.

**What M2.4 should not re-litigate:** `file_sd` with no reload call; the lifecycle API
stays off; the target policy is applied at *both* the write boundary and the file
build; the probe never asserts what it could not observe; and `readings.py` and
`agents.py` are the only places a Prometheus result becomes a state.

**Traps this milestone paid for, for the next smoke-test edit:** `grep -q` at the end of
a pipe under `pipefail` (a hazard fixed pre-emptively); Git Bash rewriting a bare `/path` argument (use `sh -c '…'`);
a `grep -o` with no match silently ending the script under `set -e`; and the fact that
`smoke-test.sh` ends in `down -v`, so Postgres and Redis must be brought back before
`check-all.sh`. **Also:** writing a Dockerfile `RUN` with `\n` in a Python string
produces a literal backslash-n; use the Edit tool and look at the bytes.

**The M2.2 hand-over, kept because it is still true:**

**After M2.2 (2026-10-16), in this order:**

1. **Push, open the PR, and watch CI.** Nothing from M2.2 has been pushed, and the
   M2.1 handover's request below (the `compose` job now validating every profile and
   running step 18 on a two-core runner) is still unanswered — PR #12 was merged and
   this session did not look at how its CI went. M2.2 adds work to step 18.
2. **M2.3 — target discovery and agent health**
   ([`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md#m23--target-discovery-and-agent-health)),
   gated by **`OPEN-14`**. It is also what turns M2.2's blunt `no_data` into something
   explainable: registration-time `metrics_instance` verification and `agent_status`
   are the two things that would say *why* a host has no series.
3. **Decide `OPEN-28` (configurable thresholds) before M3.3.** M2.2 shipped catalogue
   defaults only, and alert rules need the same numbers; a second copy in a rule file
   is the thing to avoid ([API_CONTRACTS.md §30](docs/API_CONTRACTS.md#30-open-decisions)).
4. **Write the §12 path 3 E2E** (issue 60): seed a server with `metrics_instance:
   node-exporter:9100`, wait for the first reading, assert a card and the gap rule in
   a real browser. The manual check in §7 proves the pages work today and protects
   nothing tomorrow.

**What M2.2 deliberately left for a decision, not a diff:** the dashboard `utilisation`
section (issue 61), comparing an environment of more than 20 servers (issue 63), and
whether M2.5's host staleness should replace M2.2's per-reading 60 s bound (issue 62).

**What M2.3 should not re-litigate:** `PrometheusUnavailableError` is still not an
`AppError`; `readings.py` is the only place a result becomes a state; a reading is a
value or a reason, never a zero; and `render_scrape_timestamp` is derived from
`agent_up` so the allowlist still holds. **A trap for the frontend:** the dev container
can serve stale modules after you edit `src/` (issue 53) — restart `frontend`, and note
the same applies to anything you verify in the browser.

**The M2.1 handover, kept because it is still true:**

**Open the pull request and watch CI, before anything else.** M2.1 changed the
`compose` job — it now validates every profile, runs `promtool check config` against
the pinned Prometheus image, and the smoke test it calls starts the `observability`
profile and asserts step 8. None of that has run on GitHub Actions. Two specific
things to expect on a two-core runner: step 18 polls for up to 150 s for the first
usable `rate()` reading and that budget was tuned on a faster machine, and the
`observability` services add an image pull the job did not previously make. M1.7's
`e2e` and `env-example` jobs have now run, so those are no longer unknowns.

**Then M2.2 — host metrics endpoints and charts**
([`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md#m22--host-metrics-endpoints-and-charts)).
**It is blocked on a decision, and the decision should be made first and with a test
in hand:**

- **`TS-01` is RESOLVED (2026-10-16): `d3-shape` + `d3-scale`, used directly with no
  React wrapper.** It was the critical path and it is no longer blocking. Decided the
  way this section asked — by writing the gap test first and running three candidates
  through it, not by reading feature lists. Full record in
  [TECH_STACK.md §2.5](docs/TECH_STACK.md#25-charting-library); measurements in
  [§7](#7-tests-executed-and-their-actual-results). **What M2.2 inherits:**
  `frontend/src/components/chart/linePath.ts` (two functions — `scalesFor` and
  `linePath` — and nothing else) plus `linePath.test.ts`, the gate, kept as a
  standing regression test. **What M2.2 still owes:** everything above the geometry
  — axes, ticks, tooltips, responsive sizing, keyboard focus, and criterion 3's
  data-table fallback for screen readers. Also criterion 1, "a 24 h range without
  jank on a dashboard with several charts", which could not be measured because
  there is no chart on a page yet.
- **`OPEN-14` (target discovery) gates M2.3**, not M2.2. The target list in
  `infra/prometheus/prometheus.yml` is static and says so.
- **`TS-02` (systemd metrics source) gated M2.4 — resolved 2026-10-17.**

**What M2.2 inherits from M2.1 and from the `TS-01` decision, and what it must not
re-litigate:**

- **The gap rule is `linePath`'s, and it is already pinned.** Do not reach for a
  chart library's own null handling, and do not write `?? 0` to satisfy a type
  checker: `line().defined()` inside `linePath` is the whole mechanism, and
  `linePath.test.ts` fails if a rendered path comes back continuous. Removing
  `.defined()` as a mutation fails 6 of its 12 tests, so the gate is known to work
  rather than assumed to. A chart component's job is to put `linePath`'s output in a
  `<path d=…>`, not to re-decide what a hole means.
- **The guards are already written and tested** (`app/metrics/service.py`:
  `check_time_range` and `check_selection`). §15.2 and §15.3 should *call* them, not
  re-derive the bounds in the handler. The point-count arithmetic has an off-by-one
  in it that is already covered — Prometheus returns a point at `start`, so a span
  that divides exactly yields one more point than the division suggests.
- **`PrometheusUnavailableError` is deliberately not an `AppError`.** Do not "fix"
  that. It exists so a dead Prometheus becomes a section-level
  `data_state: "unavailable"` with a reason rather than a 5xx for a page that is
  otherwise fine (PRD §4.4). M2.2's job is to catch it and map `reason` onto
  `unavailable_reason`; the reason strings are a closed `Literal` in
  `app/metrics/client.py`.
- **The rate window is a parameter for a reason.** `render(..., window_seconds=...)`
  defaults to 300 s; a range query whose `step` exceeds the window samples disjoint
  windows and under-reports, so §15.2 should widen it with the step rather than
  taking the default.
- **A WINDOWS server is `unsupported`, not empty** (`OPEN-09`'s recorded default).
  §15.1's response has to carry that state distinctly; `UnsupportedOsFamilyError` is
  what the renderer raises.
- **`servers.metrics_instance` is the join key and nothing verifies it yet.** A
  server whose label does not match any scraped target returns no series, which
  M2.2 must render as `unavailable` rather than as a flat zero line. The
  *registration-time* verification and the `agent_status` indicator that would
  explain *why* are M2.3 — so until then "no data" and "wrong label" look the same
  to a user, and that is a known, recorded limitation rather than an oversight.
- **The dashboard's `utilisation` section is still `not_configured` with nulls.**
  M2.1 deliberately did not flip it. M2.2 is the milestone that gives it a source,
  and it is also the first time any dashboard section can reach `stale`.

**What Phase 2 can build on, and what it should read first:**

- **`scripts/smoke-test.sh` and the E2E suite are now the two places a stack-level
  regression gets caught.** Extend them rather than adding a third mechanism; §11
  step 8 and §12 paths 3–7 are written and waiting.
- **Issue 53 before touching the frontend**: the dev container can serve stale
  modules, so `docker compose restart frontend` after editing `src/`. A green E2E
  run is not evidence until that is ruled out.
- **`useLatestResponse` for any view that re-requests.** Every new filtered or
  polled view needs it; the race it prevents is invisible in jsdom and intermittent
  in a browser.
- **The coverage gate will judge the diff**, 80 % on changed lines. It has not yet
  run against a backend change, so M2.1 will be the first to find out whether the
  Cobertura path matching works on `app/` paths in anger.

**What M1.7 built on (kept for history):**

- `frontend/src/api/schema.ts` and `npm run generate:api` — the E2E suite should use
  the generated types rather than hand-writing fixtures.
- `useCursorPaging` / `Pager`, now used in four places — the fourth is issue 44's
  application picker, which pages its candidates rather than showing the first 50 as
  though they were all of them.
- **Issue 44's write controls**, so the E2E path can cover a write that is not a
  registration: the `/servers/:serverId/edit` form, the `If-Unmodified-Since`
  conflict, the environment-change confirmation, and associate/remove on the detail
  page. Two of those are worth an E2E test specifically because jsdom cannot prove
  them — the `409` needs two real sessions racing, and the environment change needs a
  caller who genuinely lacks Administrator rather than one whose advisory array omits
  it.
- The dashboard as the landing route, which is the natural first step of the E2E
  path "login → dashboard → server list → register server → unauthorised action
  rejected".

**What M1.7 should clear first, in roughly this order:**

- **Issue 51 — the flaky audit-row assertions. Fixed 2026-10-15 (RESOLVED) and in
  `main`, so M1.7 starts from a suite that is green.** Worth reading before writing
  the E2E suite anyway: the fix was *not* the tiebreaker this section originally proposed —
  that would have left the flake live while looking fixed — and the two traps it
  turned up bite any new suite. **Never run two pytest sessions against `dcp_test` at
  once** (the session-scoped `migrated` fixture drops the schema under the other one;
  this produced an 84-failure run during the fix that had nothing to do with any code
  change), and **module subsets are not supported** — `pytest` on
  `test_admin_roles.py` plus `test_auth_flow.py` fails 22 tests on an unmodified tree
  while each passes alone. Verify with `-m integration`.
- **Issues 20 and 49 together** — `scripts/smoke-test.sh` has never run, and `worker`
  and `beat` have reported `unhealthy` since before M1.5. M1.7's own task list
  includes a Compose smoke test asserting the stack is healthy, so it has to resolve
  49 to pass 20 honestly.
- **Issue 21** — images are tag- not digest-pinned (`scripts/pin-digests.sh`).
- **Issue 26** — the secret scanner has never been shown to fail on a planted secret.
- **Issue 40** — the CSRF error code, still a one-word fix in an M1.3 module.

**Decisions M1.7 will inherit:**

1. **`OPEN-27`** — unchanged and still not urgent. M1.6 confirmed in code what
   PROGRESS §9 was corrected to say on 2026-10-14: §13.1 counts servers, failed
   services, alerts and recent activity, and `application_id` is only a filter that
   resolves through `application_servers` to servers, which *are* scope-filtered. The
   stale copy of the old claim in
   [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) was corrected in this
   change.
2. **`TS-01`** (charting library) is still unselected, and M1.6 deliberately selected
   nothing. It gates M2.2, not M1.7.
3. **Issue 42** — the idempotency replay comparison, still the oldest unresolved
   contract divergence.
4. **Issue 50** — the dashboard lags a write by up to the cache TTL. Accepted; worth
   knowing before writing an E2E test that registers a server and asserts the
   dashboard total.

## 10. Scope confirmation

**M2.3 implementation session (2026-10-16).** Scope was milestone M2.3 exactly, plus
what its verification forced (issue 65's decoding fix in the coverage gate, the two
stale copy strings, and a handful of smoke-test fixes recorded in §7).

**Added (backend):** `app/metrics/discovery.py`, `app/metrics/agents.py`,
`app/workers/tasks/monitoring.py`,
`alembic/versions/0005_servers_updated_at_ignores_observations.py`;
`tests/metrics/test_discovery.py`, `tests/integration/test_monitoring.py`.
**Modified (backend):** `app/core/config.py` (four settings, one validator),
`app/metrics/templates.py` (`render_agent_window`), `app/inventory/service.py` and
`router_servers.py` (target policy, regeneration triggers), `app/workers/celery_app.py`
(two beat entries, one import), `app/dashboard/schemas.py` and `app/inventory/service.py`
(two stale strings), `Dockerfile` (a directory the worker's volume inherits ownership of).
**Modified (frontend):** `StatusBadge.tsx` (four agent states), `ServerDetail.tsx` (the
row) and its test.
**Modified (infrastructure):** `infra/compose/docker-compose.yml` (passthrough, a named
volume, the lifecycle flag **removed**), `infra/prometheus/prometheus.yml` (`file_sd`,
static target removed), `.env.example`, `scripts/smoke-test.sh` (step 18),
`scripts/check_coverage_diff.py` (issue 65).
**Modified (docs):** `ARCHITECTURE.md` §4 (point 1 amended, point 3 and `OPEN-14`
resolved), `TECH_STACK.md`, `DATABASE.md`, `SECURITY.md` §19, `API_CONTRACTS.md` §14,
`IMPLEMENTATION_PLAN.md`, `TESTING.md`, `README.md`, `PROGRESS.md`.

**One migration** (0005, a trigger replacement; no column added or dropped, no data
touched; the schema is now at `0005`). **No new dependency.** **Four new settings**, each
with its `.env.example` entry and Compose passthrough, because issue 54's trap applies.

**Deliberately not done:** Windows scrape targets (`OPEN-09`), `DEGRADED` /
`NOT_INSTALLED`, exporter-absence alert rules (M3.3), a multi-host delivery mechanism
(`OPEN-12`), an E2E path (issue 60), and `status` in the trigger exclusion (M2.5).

---

**M2.2 implementation session (2026-10-16).** Scope was milestone M2.2 exactly, plus
the three things its own verification forced (the `bandit` finding, issue 59's
timeouts, and the stale shell copy), each argued where it appears above.

**Added (backend):** `app/metrics/readings.py`, `app/metrics/thresholds.py`;
`tests/metrics/{fake_prometheus,test_readings,test_thresholds}.py`,
`tests/integration/test_metrics_values.py`.
**Modified (backend):** `app/metrics/{router,schemas,templates}.py`,
`app/authz/dependencies.py` (a docstring).
**Added (frontend):** `src/api/metrics.ts`; `src/components/chart/{LineChart.tsx,
LineChart.test.tsx,ticks.ts,format.ts}`; `src/pages/servers/{ServerMetrics.tsx,
ServerMetrics.test.tsx}`; `src/pages/metrics/{Compare.tsx,Compare.test.tsx,
RangePicker.tsx,range.ts,range.test.ts}`.
**Modified (frontend):** `src/App.tsx` (routes, nav, the stale banner copy),
`src/auth/permissions.ts`, `src/pages/servers/{ServerDetail.tsx,ServerDetail.test.tsx}`,
`src/index.css` (eight `--series-*` tokens, light and dark), `src/test/setup.ts` and
`vite.config.ts` (issue 59), `src/api/schema.ts` (regenerated, not hand-edited).
**Modified (other):** `scripts/smoke-test.sh` (step 18), `docs/API_CONTRACTS.md` (§15 and
`OPEN-28`), `docs/IMPLEMENTATION_PLAN.md`, `docs/TECH_STACK.md` §2.5, `README.md`,
`PROGRESS.md`.

**No migration. No new dependency** (`d3-shape` and `d3-scale` were `TS-01`'s).
**No new setting**, deliberately: the stale bound is a module constant for the reason the
breaker thresholds are (issue 54).

**Deliberately not done:** the dashboard `utilisation` flip, fleet aggregation, a
per-server or per-environment threshold, target discovery (`OPEN-14`, M2.3), systemd
(`TS-02`, M2.4), and an E2E path (issue 60).

---

**M2.1 implementation session (2026-10-16).** Scope was milestone M2.1 exactly, plus
two fixes its own verification forced (issues 56 and 57, both argued below).

**Added:** `backend/app/metrics/` (`__init__`, `templates`, `client`, `service`,
`router`, `schemas`), `backend/tests/metrics/` (`__init__`, `test_templates`,
`test_guards`, `test_client`, `test_status`),
`backend/tests/integration/test_metrics_api.py`.

**Rewritten:** `infra/prometheus/prometheus.yml` — a real scrape config where it was
a placeholder. 15 s interval (down from 30 s, so a `rate()` window has two samples
inside half a minute), an explicit `scrape_timeout`, and the `dcp-backend` job
**removed**: the backend exposes no `/metrics` endpoint yet, so that job could only
ever report a permanently failing target, which makes "is every target up?" useless
as an assertion and presents a scrape that does not exist as one that does
(CLAUDE.md §7). It returns with the endpoint.

**Modified:** `backend/app/core/config.py` (two settings, one shared field validator
tightened, one production rule), `backend/app/main.py` (one router mounted),
`backend/app/inventory/schemas.py` (one pattern made public and re-anchored),
`backend/tests/test_config.py`, `infra/compose/docker-compose.yml`, `.env.example`,
`scripts/smoke-test.sh`, `scripts/check_coverage_diff.py`,
`.github/workflows/ci.yml`, `docs/TESTING.md`, `docs/API_CONTRACTS.md`,
`docs/IMPLEMENTATION_PLAN.md`, `frontend/src/api/schema.ts` (regenerated, not
hand-edited).

**No migration.** `metrics:read` and `admin:integrations` were already seeded by
migrations `0002` and `0004`, so the schema stays at `0004` and M2.1 adds no DDL.

**No new dependency.** `httpx` was already runtime (M1.3, the OIDC client) and
`httpx.MockTransport` is part of it, so the circuit-breaker and timeout tests needed
nothing added. TECH_STACK §10 was not consulted because no rung was reached.

Changes touching code outside the milestone's own files, each forced by it rather
than opportunistic:

- **`scripts/check_coverage_diff.py`** — the gate could not see untracked files and
  therefore judged **none** of this milestone's new code while printing a pass
  (issue 56). Fixing it was not optional: the milestone cannot claim a coverage
  result from a gate that was not looking at it, and the alternative — `git add`
  before running, so the files become visible — would have left the defect in place
  for the next person to be misled by. The floor was not touched.
- **`backend/app/inventory/schemas.py`** — `METRICS_INSTANCE_PATTERN` made public,
  because the renderer re-validates the instance label at the point of
  interpolation rather than trusting that §14.2 validated it on the way in; and
  re-anchored from `$` to `\Z`, because `$` in Python also matches before a
  trailing newline and this is now the guard a PromQL string literal depends on.
  **The six sibling patterns in the same module have the same slip and were left
  alone** (issue 57): they are stored rather than interpolated, they belong to
  M1.5's write boundary, and a tightening of stored-value validation deserves its
  own diff and its own tests.
- **`backend/app/core/config.py`'s URL validator** — extended to reject embedded
  credentials, fragments and whitespace, which also tightens `DCP_OIDC_ISSUER` and
  `DCP_OIDC_REDIRECT_URL`. One validator serves all three fields and all three are
  fetched or redirected to; adding the checks only for the new field would have left
  two known-weaker copies of the same rule a few lines away.

**Then, in the same session and at the user's direction, `TS-01`.** Scope was the
*decision* and the test it rests on — not M2.2. **Added:**
`frontend/src/components/chart/linePath.ts` (two functions: `scalesFor`, `linePath`)
and `linePath.test.ts` (the gate, 12 tests). **Modified:**
`frontend/package.json` + `package-lock.json` (four pinned entries),
`docs/TECH_STACK.md` §2.5 and §12, `docs/IMPLEMENTATION_PLAN.md` (three places),
`PROGRESS.md`. **No chart component, no axes, no tooltip, no page change** — those
are M2.2's, and building them here would have been exactly the scope creep CLAUDE.md
§3 forbids. Three candidate libraries and a zero-dependency baseline were installed
with `--no-save` for the measurements and removed again, so nothing a losing
candidate pulled in survives in `package.json` or the lockfile.

**Two new dependencies:** `d3-shape@3.2.0` and `d3-scale@4.0.2`, both pinned exactly,
runtime, plus their `@types` as dev dependencies. §10 rung 3 is the rung that failed,
and only half of it — the line geometry *is* a few lines and was written as a few
lines; tick selection over an arbitrary time domain is not. Recorded in TECH_STACK
§2.5 with the measurements and the three rejections.

**Deliberately not done, and recorded instead:** endpoints §15.1–§15.3 and charts
(M2.2 — though `TS-01`, which gated it, is now resolved); target discovery from inventory
(`OPEN-14`, M2.3); `agent_status` and registration-time `metrics_instance`
verification (M2.3 — not widened into §14.2); systemd and `TS-02` (M2.4). **The
dashboard's `utilisation` section was left `not_configured` with nulls**, because
M2.1 gives it no source and marking it configured would be claiming one.

---

**M1.7 implementation session (2026-10-15).** Scope was milestone M1.7 plus
**issue 49**, which was explicitly in scope because the smoke test cannot assert a
healthy stack while two services lie about their health.

**Added:** `scripts/check_env_example.py`, `scripts/check_coverage_diff.py`,
`infra/compose/postgres-init/01-create-test-database.sql`,
`frontend/playwright.config.ts`, `frontend/e2e/` (`support.ts`, `global-setup.ts`,
`critical-path.spec.ts`, `authorisation.spec.ts`),
`frontend/src/components/useLatestResponse.ts` and its test, and
`frontend/src/pages/applications/Applications.test.tsx`.

**Rewritten:** `scripts/smoke-test.sh` — 19 numbered steps mapped onto
docs/TESTING.md §11, where it previously covered a subset and had never run.

**Modified:** `infra/compose/docker-compose.yml` (the `migrate` service; the worker
and beat healthchecks; three `depends_on`; the postgres init mount; three settings
passed through), `.env.example` (six additions and one corrected value),
`README.md`, `docs/TESTING.md`, `docs/IMPLEMENTATION_PLAN.md`,
`.github/workflows/ci.yml` (two jobs added, two coverage steps, `fetch-depth: 0`),
`scripts/check-all.sh`, `.gitignore`, `frontend/package.json`,
`frontend/tsconfig.json`, `frontend/vite.config.ts`.

Changes touching code outside the milestone's own files, each forced by it rather
than opportunistic:

- **Four frontend pages** (`ServerList`, `ApplicationList`, `ApplicationDetail`,
  `ServerDetail`) moved onto `useLatestResponse`. Not a refactor for tidiness: the
  race it fixes is what made the new E2E suite fail three runs in eight, and
  patching only the one page the test touched would have left four copies of a known
  defect (CLAUDE.md §3 allows noting and moving on; this one blocked the
  milestone's own acceptance criterion).
- **`frontend/vite.config.ts`** — `cobertura` added to the coverage reporters so one
  parser serves both ends of the stack, and `test.include` scoped to `src/` because
  vitest was otherwise collecting the Playwright specs and `test.describe` threw
  inside the vitest runner.
- **`frontend/tsconfig.json`** — `e2e` and `playwright.config.ts` added to
  `include`, so the E2E code is typechecked rather than exempt.

**One new dependency:** `@playwright/test`, pinned exactly, dev-only. TECH_STACK §8.2
already names Playwright as this project's E2E tool, so rung 1 of §10 is satisfied
by the stack document — the same argument M1.6 used for `openapi-typescript`.
**`@axe-core/playwright` was deliberately not added**: `axe-core` is already a
dependency and `page.addScriptTag` plus `axe.run` is the whole of what the wrapper
provides (rung 5).

**Deliberately not touched, as scoped:** issue 21 (digest pinning), issue 26
(proving the secret scanner), issue 40 (the CSRF error code), `TS-01`. Issues 42, 47
and 50 are also still open and untouched.

**No backend file changed, and no migration.** The schema is still at `0004`.

**Issue 44 session (2026-10-15).** Scope was issue 44 and nothing else: the server
edit form and association management UI, taken before M1.7 for the reason in
[§9](#9-next-recommended-task). **Frontend only.**

**Added:** `frontend/src/pages/servers/ServerEditForm.test.tsx` and
`frontend/src/pages/servers/ServerDetail.test.tsx`.

**Modified:**

- **`frontend/src/pages/servers/ServerForm.tsx`** — extended rather than duplicated,
  as the task required. The registration form's inline `field`/`choice` closures
  became two module-level components, `TextField` and `ChoiceField`, and the new
  `ServerEditForm` uses them. They are two components rather than one with a mode
  flag: edit offers a different field set, loads before it renders, and carries the
  environment warning and the `409` that registration has no equivalent of.
- **`frontend/src/pages/servers/ServerDetail.tsx`** — an `Edit` link, a `Remove`
  button per association, and an `AssociateApplication` control (search box, paged
  candidate `<select>`, optional `role_on_server`).
- **`frontend/src/api/inventory.ts`** — `updateServer` gained the
  `If-Unmodified-Since` precondition, converting the row's `updated_at` to an
  HTTP-date **inside the client** so the page never handles date formats, and sending
  no header at all rather than a malformed one. `associateApplication` gained the
  optional `role_on_server`. Two generated-type aliases added, `ServerUpdate` and
  `AssociateApplication`.
- **`frontend/src/App.tsx`** — the `/servers/:serverId/edit` route.

**Not touched:** every backend file. §14.4, §14.6 and §14.7 were already implemented,
audited and tested, and `npm run generate:api` regenerating `schema.ts` with no diff
is the evidence that none of them moved. No migration; the schema is still at `0004`.
No new dependency — `axe-core`, `Pager`/`useCursorPaging`, `StatusBadge`,
`EmptyState` and `ErrorState` were all already here, which is what M1.6 left them
for.

**M1.6 implementation session (2026-10-15).** Scope was milestone M1.6. Added the
dashboard module (`app/dashboard/router.py`, `service.py`, `schemas.py`),
`app/openapi.py`, `scripts/generate-api-types.sh`, the `contracts` CI job, the
frontend components `StatusBadge`, `DataStateBanner`, `EmptyState`, `ErrorState`,
`Pager` and `useCursorPaging`, `src/api/dashboard.ts`, the generated
`src/api/schema.ts`, `src/pages/dashboard/Dashboard.tsx`, and four test modules
(`tests/dashboard/test_summary.py`, `tests/integration/test_dashboard_api.py`,
`Dashboard.test.tsx`, `StatusBadge.test.tsx`, `Pager.test.tsx`).

**Deleted:** `frontend/src/pages/inventory/states.tsx`, as its own docstring
instructed and as [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) M1.6
requires — replaced, not left beside the new components.

Changes touching code outside the dashboard module, each forced by this milestone
rather than opportunistic:

- **`app/inventory/queries.py`** — the six shared server filters were extracted into
  `server_filter_conditions`, which `servers_query` now calls. Without it §13.1,
  §13.2 and §14.1 would hold three copies of "how does `provider` resolve through
  `cloud_accounts`". Behaviour unchanged; the existing inventory suites pass
  untouched.
- **`frontend/src/api/inventory.ts`** — hand-written response interfaces deleted and
  replaced by aliases onto the generated schema (issue 43, agreed before the
  milestone). `call` and `queryString` were exported so the dashboard client reuses
  the one status-code ladder instead of copying it.
- **The five existing frontend pages** — rewired from `states.tsx` to the shared
  components. Mechanical, and required by the deletion above.
- **`frontend/src/pages/servers/ServerDetail.tsx`** — two `=== null` comparisons
  became `== null`. The generated types are correct that a Pydantic field with a
  default is *optional*, so those fields can be `undefined`; the hand-written types
  had claimed otherwise and `tsc` caught it.
- **`frontend/tsconfig.json`** — `"vite/client"` added to `types`, so
  `import.meta.env` typechecks for the configurable refresh interval.
- **`backend/app/main.py`, `app/core/config.py`** — router mounted, one setting added.
- **`backend/tests/integration/test_auth_flow.py`** — `_clear_namespace` now also
  clears `dcp:dashboard:*`. Without it a 15-second cache entry outlives the test that
  wrote it and the next test reads another caller's totals, which would surface as a
  flake rather than as the failure the cache-key tests are designed to show.
- **`frontend/src/auth/SessionProvider.test.tsx`, `ServerList.test.tsx`** — updated
  for the new landing route and for `StatusBadge` rendering a human label. Not
  weakened: the `ServerList` assertion is now stronger, checking the badge label
  *and* the filter option separately.

**Deliberately not touched:** issue 44 (server edit form and association
management), `TS-01`, anything Prometheus, and the two flaky M1.3/M1.4 test modules
in issue 51 — diagnosed and recorded here, then fixed on 2026-10-15 (see the row), deliberately not during M1.6 because they belong to those
milestones (CLAUDE.md §3).

**No migration.** The schema is still at `0004`; M1.6 only reads existing tables.

**M1.5 implementation session (2026-10-14).** Scope was milestone M1.5. Added
migration `0004`, the inventory module (`app/inventory/router_servers.py`,
`router_applications.py`, `service.py`, `queries.py`, `schemas.py`, `seed.py`), the
shared `app/core/pagination.py`, `filters.py` and `ssrf.py`, the SQL scope
translation `app/authz/queries.py`, five frontend pages plus
`src/api/inventory.ts` and `src/pages/inventory/states.tsx`, and seven test modules.

Changes touching code outside M1.5, each forced by this milestone rather than
opportunistic:

- **`app/authz/matcher.py`** — the `A-09` amendment. The milestone could not be
  delivered without it; see [§3](#3-in-progress-milestone), where it is flagged
  rather than buried, and `docs/SECURITY.md` §4, amended in the same change. Four
  matcher cases pin the new behaviour. `_normalise` was also renamed
  `normalise_scope` so the SQL translation could reuse it instead of importing a
  private name — the one-matcher rule is better served by sharing the function than
  by copying it.
- **`app/auth/dependencies.py`** — `require_read_budget`, the read-class rate limit
  M1.4 explicitly deferred to the first domain reads (docs/SECURITY.md §13). It
  lives there because it is keyed on identity and `app/core` does not import from
  the layers above it.
- **`app/core/errors.py`** — `FieldValidationError`, so the three places that raise
  a §8 field list share one class.
- **`app/core/config.py`** — four settings: the read-class budget, the SSRF host
  allowlist, and the stale threshold.
- **`app/main.py`** — mounts the two new routers.
- **`frontend/src/api/session.ts`** — `request` exported, nothing else changed, so
  the inventory client reuses the one CSRF-aware fetch wrapper instead of growing a
  second.
- **`frontend/src/App.tsx`** — the routing shell. The identity panel was initially
  lost in the rewrite and restored: removing information the operator had was
  collateral, not an improvement, and the M1.3 session tests caught it.
- **`tests/test_db_units.py` and `tests/integration/test_migrations.py`** — issues 48
  and the index-predicate split, both described in [§6](#6-known-issues).

Deliberately **not** changed, though noticed: the CSRF error code (issue 40) is still
an M1.3 module; the log permission names (issue 39) still belong to M3.2.

One dependency added: `react-router-dom` 7.18.4, by prior agreement, with the §10
rung analysis in [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §2.6. A **second** was
considered and refused: `openapi-typescript`, which [`docs/TECH_STACK.md`](docs/TECH_STACK.md)
§11 and CLAUDE.md §5 call for. Adding it was not in the agreed scope and would have
been a unilateral second dependency, so the API types are hand-written and the gap is
recorded as issue 43 rather than quietly filled or quietly ignored.

Existing data was preserved. Migration `0004` was applied to the existing `dcp`
database; its three users, one role grant and one `ON_PREM` cloud account are intact,
and the migration only adds nullable columns and renames one permission row. No
database was created, no volume reset. The integration suite continued to use
`dcp_test`, whose schema it drops and rebuilds per session as it has since M1.2. Six
synthetic servers and three synthetic applications were seeded into `dcp` by
`app.inventory.seed`, all visibly marked; two servers created by hand during
verification were retired through the API rather than deleted, so the audit trail is
intact. No cloud resource was provisioned, no production system contacted, and no
real credential created or stored.

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
| Provider capabilities not assumed identical | Yes — capability matrix in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §7.2, per-account capabilities endpoint, per-OS-family metric templates, and a test that pins the differences. **Annotated after M2.1 (2026-10-16):** the per-OS-family structure is implemented and the test exists, but there is currently only *one* family with queries — what the test pins is that every template covers `LINUX` and that WINDOWS and OTHER raise `UnsupportedOsFamilyError`, which is the honest form of "not assumed identical" while `windows_exporter` is out of scope (`OPEN-09`). The differences between two populated families remain unpinned because there is no second populated family |
| Implementation plan has no forward dependencies | Yes — each milestone's dependencies point only backwards; the gating table lists which open decision blocks which milestone |
| Duplication avoided | Yes — each topic has one owner (security rules in SECURITY, schema in DATABASE, endpoints in API_CONTRACTS, decisions in ARCHITECTURE §18–19), referenced by relative link elsewhere |
| Relative links | All inter-document links verified as existing files with matching heading anchors |

Re-run this review whenever a contract document changes.

### M1.6 code-vs-documentation review (2026-10-15)

| Claim | Verified how |
|---|---|
| Endpoints 13.1 and 13.2 exist and require `dashboard:read` | `GET /openapi.json` on the running stack lists both paths; the route-coverage gate (`tests/authz/test_route_coverage.py`) passes, so neither escapes a permission dependency |
| Counts respect scope | `test_counts_are_scope_filtered` and `test_the_health_list_is_scope_filtered`; the predicate is `app/authz/queries.readable_servers`, the same one §14.1 uses, so `tests/integration/test_scope_filtering.py` still guards the matcher/SQL agreement |
| The cache key includes scope | Four unit cases plus one end-to-end test, **all confirmed to fail** against a deliberately scope-blind fingerprint before being relied on ([§7](#7-tests-executed-and-their-actual-results)) |
| No section substitutes `0` for unknown | `test_every_section_without_a_source_is_null_and_says_why` over the wire; `renders an unavailable metric as unavailable, never as 0` in the browser suite, confirmed to fail against a `?? 0` regression; and read off the rendered page by hand |
| `health_state` is never defaulted to healthy | `test_the_health_list_never_defaults_a_server_to_healthy`, `StatusBadge` unit test, and observed as `? Unknown` on all six seeded servers in a real browser |
| Status is never conveyed by colour alone (PRD §8) | `StatusBadge` renders glyph + word + colour; two unit tests pin it; observed on the page as `✕ Unreachable`, `◐ Stale`, `◌ Pending`, `● Managed` |
| `collected_at` displayed for every data section | `CollectedAt` on the summary and the status table; `displays collected_at for the data sections`; observed on the page |
| Summary cards link to the relevant detail view | `links summary cards to the relevant detail view`; observed as `/servers`, `/servers?status=MANAGED`, `…=STALE`, `…=UNREACHABLE` |
| Selecting a filter updates the server list (PRD §4.2) | `an environment filter updates the health list` over HTTP, `sends the chosen filter to the backend rather than filtering locally` in the browser suite, and done by hand in a real browser (6 → 3 servers) |
| `axe` reports no violations | `has no axe violations`, wcag2a + wcag2aa. **`color-contrast` is explicitly disabled** and says why: jsdom has no layout, so that rule returns "incomplete" rather than a verdict. Contrast is unverified and belongs to M1.7's E2E suite |
| TypeScript types are generated, committed, and gated on drift | `scripts/generate-api-types.sh` is idempotent (verified by hash across two runs); the `contracts` check regenerates and diffs in both CI and `check-all.sh`; `src/api/inventory.ts` holds no hand-written response shape |
| The documented divergences are documented | Both are written into [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §13 in this change, not only here |
| §13 example payload matches what the code returns | Replaced in this change with the real shape, including `server_health`, `items: null` and `cache_ttl_seconds` |

**Corrected while passing through:** the `OPEN-27` row in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)'s gating table still said
"M1.6 — the dashboard counts applications, so it meets the question immediately".
That claim was corrected in PROGRESS §9 on 2026-10-14 but the plan's copy was missed.
M1.6 is now built and the correction is confirmed by the code: §13.1 counts servers,
failed services, alerts and recent activity, and nothing counts applications.

### M1.5 code-vs-documentation review (2026-10-14)

| Claim | Verified how |
|---|---|
| Every §14 and §18 endpoint shipped carries the permission the contract names | Read endpoint by endpoint against [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §14 and §18, and enforced over HTTP: four non-administrator roles get `403` on every write and `200` on every read, parametrised per role |
| The permission rename actually happened | Asserted against the **live catalogue**, not the migration source (`test_the_permission_was_renamed_by_migration_0004`): `admin:integrations` present, `integration:manage` absent, ADMINISTRATOR's grant followed the permission id. Confirmed again in `/auth/session` on the running stack |
| The SQL scope filter and the matcher agree | `test_the_sql_filter_selects_exactly_what_the_matcher_allows` — 14 scopes × 2 effects against 5 real rows, comparing the set SQL returns with the set `permits()` allows. This is what keeps docs/SECURITY.md §4 rule 7 true across two evaluation strategies |
| The `A-09` amendment is written down everywhere it is relied on | `app/authz/matcher.py` docstring, [`docs/SECURITY.md`](docs/SECURITY.md) §4, [§3](#3-in-progress-milestone) — and four matcher cases, including the one that proves production is still not implicit under a wildcard |
| The new columns are documented, not just built | [`docs/DATABASE.md`](docs/DATABASE.md) §4.2, §4.3 and §14, including **why** the idempotency index is not partial on `deleted_at` and why the predicate is needed at all |
| Honest data states are real, not claimed | Read in a browser: "Never seen", "Unknown — never seen", "Not configured — … (M2/M3/M4)". No zero, no dash. `test_an_administrator_registers_a_server_and_it_is_pending` asserts the same four nulls on the API |
| Divergences are recorded, not hidden | Issues 42–48 and `OPEN-27`, all in writing. The two that most invite quiet resolution — the hand-written API types (43) and the missing edit UI (44) — are stated as gaps against the documents that require them |
| What is **not** implemented is said so | §14.8, §18.6 and §18.7 are marked in [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) and in the router docstrings, with the milestone each waits on. §31's "no endpoint exists yet" line is replaced with an accurate implementation-status note |

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
