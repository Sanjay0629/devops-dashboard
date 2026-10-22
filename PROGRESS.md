# PROGRESS — DevOps Control Plane

**Purpose:** preserve development state between AI coding sessions. Read this first; update it last.
**Last updated:** 2026-10-23
**Updated by:** the **M4.6 implementation session (audit API and operations history)** — read [§9](#9-next-recommended-task) first. The shortest version: a person can now *read* what was done. `GET /operations` and `GET /servers/{id}/operations` list the operation history (the result with its read-back evidence, the provider's execution id, the approval and its decider), scoped on the **stored** environment; `GET /audit-events` and `/audit-events/{id}` read the trail, scoped on the event's environment, redacted a second time on the way out, and **every read writes an `AUDIT_QUERIED` row that is committed before the page is returned**; a bounded export (31 days, 100 000 events, 64 MiB; refused rather than truncated) is built by a worker that re-authorises the requester and downloaded exactly once. The dashboard's `recent_operations` is real and issues 143 and 164 are closed. **Proven against the test database and stubbed providers; the new pages were not looked at in a real browser, and the Compose smoke test and Playwright were not run locally. Merged to `main` as PR #33 (`061a94b`, 2026-10-23); the owner reports hosted CI green on it (not read from the development machine — `gh` is not installed there).** The things most worth knowing: **the agreed rule for events with no environment would have hidden every login and role grant from the bootstrap Administrator** (its grant names all four environments, because production must be named) — found by reading the matcher before writing the predicate, and changed with the owner's choice; **the database does not refuse a partition `DETACH`**, so the immutability claim was reduced to what is true and a strict `xfail` records the rest for M6.2; **two audited fields carry free text by design** (the incident switch's reason, an inventory edit's before and after), so the secret sweep claims masking for them and absence for the rest; **the first full run after the frontend commit failed five tests that passed alone** — a default window ending exactly at "now" compared the application's clock with the database's, now given five minutes of slack (an inference from the failure pattern, not a reproduced skew); and **two `check-all` failures were my own tests leaving rows other suites count**. Fixed in the plan's §8 – §13 list.

Before it, the **M4.3 implementation session (AWS SSM adapter)** — read [§9](#9-next-recommended-task) first. The shortest version: DCP now contains a real AWS Systems Manager adapter behind the M4.1 interface and the M4.2 state machine, and with it §14.8, §26.2 – §26.6, resume from a persisted handle and the operation-failure alert. **All of it is built and proven against a stubbed AWS layer (botocore's own `Stubber`, plus a real local HTTP server for the retry rule) and nothing else: it has never been run against AWS.** This session had no AWS account, no credential, and the `aws-mcp` server failed to connect, so the milestone's acceptance criterion — *a restart on a real non-production instance succeeds and is verified* — is **not met by the build**; it needs the owner's manual run ([`infra/aws/README.md`](infra/aws/README.md), issue 146). Fourteen decisions (`OPEN-78` – `OPEN-91`) were closed before code at the owner's instruction to take the recommended option. The things most worth knowing: **the adapter interface needed a third change found only while building** (`get_operation_status(ref, handle)`: a `CommandId` alone cannot be polled); **there is no way to read a unit's state from SSM, so every read is a Run Command** and the registered document is the only read/write boundary (IAM cannot tell `status` from `restart`); **the document is the one place a shell line exists**, so it takes its two parameters as environment data, and the tests *run the script under `sh`* with hostile values and a stand-in `systemctl`; **the write client has botocore's retries switched off, proven against a real HTTP server that counts requests** rather than by reading a config key; **a runtime "proof" that the role denies `AWS-RunShellScript` would have meant sending one**, so that check is offline only; and the new alert producer left alert rows behind for tests that count alerts until the test fixture was made to remove them. Nothing is pushed; hosted CI has not run; the Compose smoke test was **not run** (it needs `down -v`, issue 144).

Before it, the **M4.2 implementation session (operation lifecycle and the state machine)** — read [§9](#9-next-recommended-task) first. The shortest version: a person can now request a start, stop or restart, it becomes a durable `operations` row guarded by a state machine and three storage guarantees (one operation per idempotency key, one in flight per service, and **no `SUCCEEDED` without the state that was read back**), a worker re-authorises and re-validates before it invokes a provider exactly once, and a reaper times out what a dead worker left. **All of it is proven against a test fake and nothing else: in the shipped application no operation can be created**, because no adapter is registered and the demonstration adapter refuses writes, so `POST …/operations` answers `503 not_configured` and creates no row. Eleven decisions were closed before code at the owner's instruction to take the recommended option. The things most worth knowing: **the plan asked for endpoints 22.1 – 22.4 that read a table which does not exist yet** (they moved to M4.5); **an operation that needs approval is refused, not queued**, until M4.5, because a row nobody can decide would hold the target's one slot; **the hardest bug was found by the test, not by reading** (two requests with one key could be told "operation in progress" instead of being replayed); and two documents disagreed about a crash (resume or time out) and the reaper won. Nothing is pushed; hosted CI has not run; the Compose smoke test was **not run** (it needs `down -v`).

Before it, the **M4.1 implementation session (provider adapter interface and the demo adapter)** — read [§9](#9-next-recommended-task) first. The shortest version: the seam between DCP and a cloud now exists and is tested, and **nothing is behind it**. A synchronous `ProviderAdapter` with a typed signature (no `params`, so a command has no parameter to travel in — asserted by signature inspection, and the assertion is itself shown to fail on bad signatures), a `DemoAdapter` that never asserts a fact about a host, an empty registry that fails closed, and `GET /cloud-accounts/{id}/capabilities`. Nine decisions were closed before code at the owner's instruction to take the recommended option; the one most worth knowing is that **`ARCHITECTURE.md` §7.2 and API_CONTRACTS §14.8 disagreed about the demo adapter** (CLAUDE.md §7 broke the tie). One test in the alerts suite fails intermittently on unmodified `main` (issue 128). Nothing is pushed; hosted CI has not run; the Compose smoke test **passed** from empty volumes, including the new step 17b (issue 129, resolved).

Before it, the **M3.3 implementation session (alert rules and lifecycle)** — read
[§9](#9-next-recommended-task) first. The shortest version: a stopped host now becomes a **real alert row** — a generated Prometheus rule, through a real Prometheus and a real Alertmanager, into a webhook that treats the payload as untrusted text — and a person can acknowledge and assign it, and **acknowledging never resolves it** (a schema CHECK says so, not just the code). Nine decisions were closed before code at the owner's instruction to take the recommended option. What is most worth knowing: **a documented constraint was impossible** (the "unique open row" index on a partitioned table cannot enforce it, so an arbiter table does); **the real Alertmanager was measured before any design relied on it** (a refused delivery is re-sent every `group_interval`; `max_alerts` silently drops the excess); the dashboard's `0` now says when the alerting pipeline is *unconfirmed*, so it cannot read as all-clear; and one of my own tests poisoned the whole suite by letting the Celery singleton configure itself against the placeholder Redis. Nothing is pushed; hosted CI has not run.

Before it, the **M3.2 implementation session (log search API and UI)** — read
[§9](#9-next-recommended-task) first. The shortest version: a registered server's logs can now be
searched through `POST /logs/query` and the `/logs` page, scoped per server **and per source type**
in SQL, redacted a second time on the way out, paged without repeating or skipping a line even
inside a group of lines sharing one nanosecond, and **never answered as an empty page when the log
store cannot answer or a shipper has gone quiet**. Seven decisions were closed before code. What
is most worth knowing: **a stand-in Loki passed everything while the real one rejected every
continuation page** (the M3.1 read ceiling was 1 000 and the API reads up to 5 000) — found only by
smoke step 18f; **the order of streams in a Loki response is not something to rely on** (a total
order and a re-read of the boundary tie group replaced the plan's simpler cursor); and the
`application_id` label is a **UUID** where DATABASE.md said slug (`OPEN-33`). Nothing is pushed;
hosted CI has not run.

Before it, the **M3.1 implementation session (Alloy and Loki ingestion)** — read
[§9](#9-next-recommended-task) first. The shortest version: a real Alloy now ships a managed
server's logs (Nginx, PHP-FPM, syslog, a JSON application log, Docker, and a real systemd
journal) into a real Loki with **exactly seven labels, closed values, redaction before
storage, and a line with a bad identity dropped rather than stored** — all asserted by the
smoke test (steps 18c–18e), not only observed. Eleven decisions were closed before code at
the owner's instruction to take the recommended option. The things most worth knowing:
**the first fail-closed check was vacuous** (a path-rewriting accident left the container
dead, and "nothing stored" passed) and was caught only because the drop *counter* was
missing; **a multi-line PEM key is not redacted and a test now pins that**; **Docker's log
files are `root:root` `0640`, so a non-root shipper reads nothing and says nothing** (AD-22,
issue 95); and an old M2.4 integration test with no headroom against the 120 s limit failed
`check-all` three runs in five (issue 91). Nothing is pushed; hosted CI has not run.

Before it, the **M2.3 implementation session (target discovery and agent
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

**Update 2026-10-23 — M4.6 (audit API and operations history) is done on `phase-M4.6`, seven commits cut from `main` at `e56ed1b` (M4.5 merged and green on hosted CI), **merged to `main` as PR #33 (`061a94b`) on 2026-10-23; hosted CI green per the owner**. Phase 4 is complete apart from the deferred Azure adapter (M4.4, `OPEN-92`). The plan is [`docs/M4.6_PLAN.md`](docs/M4.6_PLAN.md); its §8 – §13 list what differed from it, twenty-seven items found while building. Everything here is proven against the test PostgreSQL and Redis and stubbed providers; the pages have not been seen in a browser.**

**Update 2026-10-20 (later still) — M4.5 (the approval workflow) is done locally on `phase-M4.5`, six commits cut from `main` at `dbe61a7` (the M4.3 merge); not pushed, not on hosted CI. A request that needs an approval is now *queued for a person* instead of refused: it waits `AWAITING_APPROVAL` with an `approvals` row and a pending-approval alert, another person in scope and in the policy's role approves or rejects it in one guarded transaction, an unused window expires to `CANCELLED` and frees the target, and the worker no longer trusts the flag on the row — it re-checks an `approvals` row and the approver's standing. An Administrator can now edit approval policies, including turning self-approval on. Like everything in Phase 4 it has been exercised only against stubbed providers: no AWS call has ever been made, the owner's manual AWS check (issue 146) has not been done, and none of this is evidence that a real server was restarted. M4.4 (Azure) is deferred and nothing here depends on it.**

**Update 2026-10-20 — M4.3 (AWS SSM adapter) is done locally on `phase-M4.3`, seven commits cut from `main` at `1d6481b`; not pushed, not on hosted CI. For the first time a real provider adapter exists and an operation can, in principle, be requested in a deployed application (an AWS account, a role ARN, an SSM document version and no static key are all required). It has been exercised only against a stubbed AWS layer; no AWS call has ever been made.**

**Update 2026-10-19 (later) — M4.2 (operation lifecycle and the state machine) is done locally on `phase-M4.2`, six commits cut from `main` at `75c6417`; not pushed, not on hosted CI. There is still no real provider and no operation can run in the shipped application. What exists is the machinery the first real adapter (M4.3) plugs into: migration `0010` (`operations`), the transition table, a guarded-update builder, endpoints 17.1 – 17.3, a worker with a reaper and a healer, and the UI (actions, a confirmation dialog, an operation page).**

**Update 2026-10-19 — Phase 4 has started: M4.1 (provider adapter interface and the demo adapter) is done locally on `phase-M4.1`; not pushed, not on hosted CI. There is still no real provider: no AWS, no Azure, no credential, no outbound call. What exists is the interface, the error taxonomy, three-valued capability sets, a demonstration adapter and §26.7.**

**Stage: Phase 3 — Logs and alerts, complete locally. M3.1 (Alloy and Loki ingestion), M3.2 (log search API and UI), M3.3 (alert rules and lifecycle; merged as PR #21) and M3.4 (notifications) are done; M3.4 is on branch `phase-M3.4`, not pushed and not on hosted CI. **As of M3.4 an alert is told to someone:** a new alert row writes its delivery rows in its own transaction, a worker sends them (HTTPS or SMTP) with bounded retry, and an administrator manages channels and an operator retries a failed delivery — verified against a **local mock sink**, not against Slack, Teams or a mail provider. **As of M3.3 an alert is real:** a generated rule file, a real Alertmanager and a webhook put five conditions into `alert_events`, a person can acknowledge and assign them, and the dashboard's `active_alerts`, per-row counts and the server page's `open_alerts` read them. §19.1, §19.2 and §16.7 answer from a real Loki; §19.3 (deployment logs) waits for M5. Phase 2 (M2.1 – M2.5) is complete (M2.5 locally; see issue 90). As of M2.4 a registered unit on a scraped host shows its real systemd state, host-aware: a dead host is `UNKNOWN`, never `INACTIVE`. The dashboard's `utilisation` section is still
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
| **M2.5** — stale-data detection (AD-14, `OPEN-30`): per-environment `DCP_HEALTH_THRESHOLDS`, the status sweep, the Prometheus-answered reference, real health on the dashboard and server detail | **Complete locally, not yet pushed or run on hosted CI; the pages were not viewed in a real browser** | 2026-10-17 | 853 unit + 397 integration (real PostgreSQL 17.2 and Redis) + 227 frontend tests pass; `scripts/check-all.sh` passes all 18 checks; **`scripts/smoke-test.sh` passes from empty volumes** (step 18a drives a real silent host to `STALE` then `UNREACHABLE`, asserts two audited moves, an unchanged `updated_at`, and that a 90 s Prometheus outage ages nothing and recovers; 18b also ran). Details: [§9](#9-next-recommended-task), issues 88–90. |
| **M3.1** — Alloy and Loki ingestion: Loki limits and 14 d retention, sample log producers, Alloy pipeline (closed label set, fail-closed identity, RE2 redaction), journald source, smoke steps 18c–18e | **Complete locally, not pushed or on hosted CI** | 2026-10-18 | `scripts/smoke-test.sh` passes from empty volumes with 18b and 18e run against the real systemd fixture; `check-all.sh` passes all 18 (unit 886, integration 397, frontend 227). Real Alloy 1.5.1 into real Loki 3.3.2: seven labels exactly, 8 canaries absent with the marker present in all 5 file sources, four bad-identity cases dropped and counted, a real journal read. Mutation-checked three ways. **Caveats:** multi-line PEM keys leak (issue 92); the Docker source was run against a real daemon once, by hand (issue 95); no log endpoint exists yet ([§9](#9-next-recommended-task)) |
| **M2.4** — systemd service monitoring (`TS-02`, `OPEN-29`): state normalisation, endpoints §16.1–§16.7, refresh job, honest failed-services count, services pages | **Complete, with the caveats in [§6](#6-known-issues) issues 69–80** | 2026-10-17 | 777 unit + 365 integration + 211 frontend tests pass against **real PostgreSQL 17.2 and real Redis**; `scripts/check-all.sh` passes all 18 checks with nothing skipped (changed-line coverage 585/638 backend and 69/73 frontend, both above the 80% floor and understated for the backend — issue 80); `scripts/smoke-test.sh` passes (20 steps, from empty volumes) with the new **step 18b driving real transitions on a real systemd** and asserting what the portal says: a killed unit is `FAILED`, a cleanly stopped one `INACTIVE`, a unit the host does not have `UNCOLLECTED`, a failing system bus `UNCOLLECTED` with the host still reachable, and **a stopped host `UNKNOWN` for every unit — none `INACTIVE`** ([§7](#7-tests-executed-and-their-actual-results)). **Not observed on a hosted CI runner, and the UI was not viewed in a real browser.** |
| **M3.4** — notifications (`OPEN-48` – `OPEN-57`): secret resolver (`env://`), the request-time half of SECURITY.md §19, `notification_channels` / `notification_deliveries` as a transactional outbox (migration 0009), delivery worker with lease, bounded retry and abandonment, HTTPS and SMTP senders, §26.8 – §26.10 and §20.6, per-channel §26.1 rows, channels page, deliveries and Retry on the alert page, smoke 18h | **Complete locally, not pushed or on hosted CI; verified against a local mock sink only** | 2026-10-19 | `scripts/check-all.sh` **ALL CHECKS PASSED**, nothing skipped, on the final code: unit 1522, integration 665, frontend 367; **`scripts/smoke-test.sh` PASSED from empty volumes, no "incomplete" marker**, with step 18h ([§7](#7-tests-executed-and-their-actual-results)) |
| **M4.1** — provider adapter interface (`OPEN-58` – `OPEN-66`, `OPEN-05` closed): `app/providers/` (types, nine-class error taxonomy with API mapping and read/write retry table, three-valued capabilities, the ABC with a final gated `execute_operation`), `DemoAdapter`, an empty fail-closed registry, §26.7, the provider-branching gate, smoke step 17b | **Complete locally, not pushed or on hosted CI; no real provider exists** | 2026-10-19 | `scripts/check-all.sh` on the final code: unit **1677** passed, frontend **367** passed, mypy / ruff / bandit / pip-audit / npm audit / generated types / `.env.example` / promtool / docs links / the new gate all passed, backend changed-line coverage **358/367 (97.5 %)**; integration **677 of 678** — the one failure is issue 128, reproduced on unmodified `main`; **`scripts/smoke-test.sh` from empty volumes: SMOKE TEST PASSED**, no "incomplete" marker, step 17b included. See [§7](#7-tests-executed-and-their-actual-results) |
| **M4.2** — operation lifecycle (`OPEN-67` – `OPEN-77`): `app/operations/` (the 13-pair transition table with `VERIFYING` never stored, the approval evaluator, closed failure codes, the guarded-`UPDATE` builder), migration `0010`, endpoints 17.1 – 17.3, the in-flight guard on deregister / retire, `execute.run` + `reap`, tasks `dcp.operations.execute` / `dcp.operations.reap`, the Actions panel, confirmation dialog and `/operations/:id` | **Complete locally against a test fake, not pushed or on hosted CI; no real provider exists, so no operation can be created in the shipped app** | 2026-10-19 | `scripts/check-all.sh` on the final code: unit **1861** passed, integration **866** passed, frontend **404** passed, mypy / ruff / bandit / pip-audit / npm audit / generated types / `.env.example` / promtool / docs links / the provider-branching gate all passed, backend changed-line coverage **693/811 (85.5 %)**, frontend **141/150 (94.0 %)**. **`scripts/smoke-test.sh` not run.** See [§7](#7-tests-executed-and-their-actual-results) |
| **M4.3** — AWS SSM adapter (`OPEN-78` – `OPEN-91`): `app/providers/aws/` (credentials via `AssumeRole` of a role ARN with static keys refused, a read client with adaptive retries and a write client that cannot retry, the botocore error mapping, the adapter), `infra/aws/` (the SSM document, the execution-role and trust policies, a runbook, `scripts/verify_aws_role.py`), three interface amendments, resume from the persisted handle (migration `0011`), the region boundary, §14.8, §26.2 – §26.6, per-account §26.1 rows, the operation-failure alert, a *Check connectivity* button | **Complete locally against a STUBBED AWS layer only; never run against AWS; not pushed or on hosted CI** | 2026-10-20 | `scripts/check-all.sh` on the final code: ALL CHECKS PASSED, nothing skipped — unit **2054 passed (+2 skipped by design)**, integration **955 passed**, frontend **422 passed**; backend changed-line coverage **940/1033 (91.0 %)**, frontend **48/53 (90.6 %)**. **`scripts/smoke-test.sh` not run.** **No AWS API was called by any test or check.** See [§7](#7-tests-executed-and-their-actual-results) |
| **M4.5** — approval workflow (`OPEN-93` – `OPEN-105`): migration `0012` (`approvals`, `EXPIRED`, `admin:policies`), the pure `resolve_policy` / `may_decide` / `approval_valid_for_execution`, §17.1 queueing `AWAITING_APPROVAL`, endpoints 22.1 – 22.4, the cancel interplay, the expiry sweep inside the reaper, the pending-approval alert, **the worker's re-check from evidence** with a bypass suite, the Administrator editor for approval policies (25.11 – 25.14), the Approvals and Approval policies pages | **Complete locally, not pushed or on hosted CI; proven against stubbed providers only — never against AWS** | 2026-10-20 | `scripts/check-all.sh` on the final code of the branch: all checks passed with nothing skipped (unit 2217 passed, integration 1069 passed, vitest 484 passed). Six mutation checks, each seen failing named tests. See [§7](#7-tests-executed-and-their-actual-results) and [`docs/M4.5_PLAN.md`](docs/M4.5_PLAN.md) |
| **M4.6** — audit API and operations history (`OPEN-106` – `OPEN-117`): migration `0013` (`audit_exports`), §23.1 / §23.2, §24.1 / §24.2 with `AUDIT_QUERIED`, §24.3 – §24.5 (the bounded, single-retrieval export and its worker and sweep), the stored-environment and event-environment scope predicates (one translation of the matcher, cross-checked), the issue-164 fix, the dashboard's `recent_operations`, the audit registry and completeness test, the Operations, Audit log and export pages | **Complete, merged to `main` (PR #33); hosted CI green per the owner; the pages not seen in a browser; smoke test and Playwright not run locally** | 2026-10-23 | `scripts/check-all.sh` on the code before the last two commits: unit **2313** passed, vitest **537** (38 files), changed-line coverage backend **713/797 (89.5 %)** and frontend **264/292 (90.4 %)**, every static check green; its integration step reported two failures, both my own tests' leftover rows (fixed). Integration on the final code: **1389 passed, 1 xfailed** (the strict `xfail`), 2317 deselected, in 609.65 s. See [§7](#7-tests-executed-and-their-actual-results) and [`docs/M4.6_PLAN.md`](docs/M4.6_PLAN.md) |

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

**None.** M4.5 is finished locally on `phase-M4.5` (six commits, nothing pushed); M4.3 and M4.2 are merged. Next: push, open the PR and watch CI; then **the owner's manual run against a non-production AWS account** (the only thing that can show the adapter works — issue 146); then M4.6 (audit API and operations history) — see [§9](#9-next-recommended-task). What M4.5 built, decided and found is in [§7](#7-tests-executed-and-their-actual-results), [§6](#6-known-issues) (issues 160 – 169; issue 135 is resolved) and [`docs/M4.5_PLAN.md`](docs/M4.5_PLAN.md). **The development database (`dcp`) was not migrated** by this work: it is at `0011` and needs `alembic upgrade head` before the new tables exist there.

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
| 2 — Monitoring | ~~M2.1 Prometheus client~~ **done** · ~~M2.2 host metrics + charts~~ **done** · ~~M2.3 target discovery~~ **done** · ~~M2.4 systemd monitoring~~ **done** · ~~M2.5 stale detection~~ **done** | **Complete locally.** M2.1 – M2.3 complete 2026-10-16, M2.4 and M2.5 2026-10-17; M2.5 not yet on hosted CI ([§9](#9-next-recommended-task)) |
| 3 — Logs and alerts | ~~M3.1 Alloy + Loki~~ **done** · ~~M3.2 log search~~ **done** · ~~M3.3 alert rules + lifecycle~~ **done** · ~~M3.4 notifications~~ **done** | **Phase 3 complete locally.** M3.1 – M3.3 2026-10-18 (M3.3 merged as PR #21), M3.4 2026-10-19 (`phase-M3.4`, not pushed) |
| 4 — Controlled operations | ~~M4.1 adapter interface~~ **done** · ~~M4.2 operation lifecycle~~ **done** · ~~M4.3 AWS SSM~~ **done, stubbed only** · ~~M4.4 Azure~~ **DEFERRED** (no Azure estate, `OPEN-92`) · ~~M4.5 approvals~~ **done, stubbed only** · M4.6 audit API | **In progress.** M4.1 and M4.2 merged; M4.3 complete locally 2026-10-20 (`phase-M4.3`, not pushed) and **never run against AWS**; M4.5 complete locally 2026-10-20 (`phase-M4.5`, not pushed; [`docs/M4.5_PLAN.md`](docs/M4.5_PLAN.md)) |
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
| 39 | **RESOLVED 2026-10-18 (M3.2 commit 1, migration 0007, `OPEN-32`) for the log half; the integration half was resolved in M1.5.** Original entry: **PARTLY DECIDED 2026-10-14** — the integration half is settled: `integration:manage` is renamed to `admin:integrations` in M1.5's migration (see [§3](#3-in-progress-milestone)). The log half stays open until M3.2. **The seeded permission catalogue does not use the names the contracts do, and M1.4 only reconciled the two it needed.** [`docs/SECURITY.md`](docs/SECURITY.md) §4 and [`docs/API_CONTRACTS.md`](docs/API_CONTRACTS.md) §18/§24/§26 name `admin:integrations`, `logs:read:application` and `logs:read:system`; the M1.2 seed created `integration:manage`, `logs:read` and `logs:query`. Migration `0003` added the contract-named `admin:users` and `admin:roles` (needed by §25) and narrowed `admin:manage` to system settings, but left the integration and log permissions alone — renaming permissions nothing enforces yet would be churn, and the log split carries a real security distinction that the milestone enforcing it should design. | Naming drift; no behavioural effect today, because only ADMINISTRATOR holds any of them | Reconcile `integration:manage` → `admin:integrations` in M1.5 (the first endpoint to require it, §18.2) and `logs:read`/`logs:query` → `logs:read:application`/`logs:read:system` in M3.2. [`docs/SECURITY.md`](docs/SECURITY.md) §4 now names both forms so the divergence is visible rather than latent. |
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
| 88 | **The Prometheus circuit breaker never recovered after an outage in a Celery worker (fixed 2026-10-17).** Half-open admits one probe; the agent probe and the service reader sent three queries with `asyncio.gather` under `asyncio.run`, so one query held the probe, two were refused `circuit_open`, `gather` re-raised, and `asyncio.run` cancelled the holder before it could record a verdict. The breaker stayed half-open with the probe taken, refusing every later cycle until the process restarted. Found only by M2.5's smoke test (a Prometheus restart left health "not evaluated" for 150 s); every scripted test used a handler that never yields, which hides it. **Fix:** `gather_all` waits for all queries and prefers a real failure over `circuit_open`; a cancelled call releases the probe. Tests: cancel releases the probe; concurrent queries close the breaker rather than wedge it; the agent probe recovers. Affects M2.1 and M2.4 call sites too. | High (was) | **Fixed**; a regression test fails if either call site goes back to plain `gather` |
| 89 | **Two things the sweep deliberately does not do.** A `MANAGED` host that was **never seen** stays `MANAGED` (no evidence is not old evidence), and a `STALE`/`UNREACHABLE` host that **stops being a target** (monitoring switched off, instance cleared) keeps its last status until an administrator changes it. Both read honestly as health `unknown`, but the *status* column can outlive its evidence. | Low | By design (D1); revisit if operators are surprised |
| 90 | ~~**The M2.5 pages have not been seen in a browser.**~~ **Viewed 2026-10-17** in the built-in browser against the full Compose stack (Prometheus, mock IdP login as the `admin` dev account, seeded demo inventory, a live scraped exporter): dashboard at desktop and 375 px width, the health filter, server detail for an unreachable and a never-seen host, and the dashboard during a real Prometheus outage. Found and fixed three things (below). **Still open:** the hosted CI run of step 18a. | Low | Browser half closed |
| 91 | **`test_the_sql_state_expression_agrees_with_view_cached_on_every_combination` (M2.4) has no headroom against the suite's 120 s hang detector.** It registers ~860 services over HTTP and takes **77–84 s alone** on this machine today (89 s with coverage); inside the full suite under `--cov` it crossed 120 s and `check-all` failed in `backend: integration` three times out of five runs (found 2026-10-17 while doing M3.1; two earlier runs passed at 237 s and 321 s total). The trace showed only a psycopg wait, which is why it first looked like a lock; it was not — no session held one. **Fixed** with a per-test `@pytest.mark.timeout(400)` and a comment, rather than loosening the suite-wide limit, whose job is to catch hangs. The full integration suite now takes **~500 s** here (it was ~100 s in M2.x), so the machine is slower than when that test was written; if it keeps growing, make the test cheaper (batch the registrations) rather than raising the number again. | Low | **Resolved 2026-10-17** (M3.1) |

**Verified on Linux, from a clean checkout, with the workflow's environment and real service containers:** 777 unit and 365 integration tests pass, migrations apply up/down/up on PostgreSQL 17.2, `pip-audit` is clean, the TypeScript API types regenerate **byte-identical** to the committed file, and `scripts/smoke-test.sh` passes (all 20 steps, including the systemd fixture in step 18b) — **also when `docker compose` is the hosted runner's own 2.38.2**. **The E2E suite passes 11/11 against a stack built the way the `e2e` job builds it** (clean checkout, the workflow's variables blanked, the bumped base images; Playwright from the Windows host). **Still not observed on a hosted runner:** the `e2e` job itself, the `compose` job's runtime, and whether the systemd fixture can start there (issue 69).

### Implementation issues (found during M4.6, 2026-10-22/23)

| # | Issue | Severity | Status |
|---|---|---|---|
| 178 | **An audit event with no environment is visible only to a grant that covers every environment** (`OPEN-109`, amended during the build at the owner's choice). Logins, role grants, user changes and system events have no environment, so a production-only Approver does not see them and a server- or account-scoped grant sees no audit row at all. Strict on purpose; loosening it is one predicate (`matcher.permits_environmentless`) and its cross-check. The first wording, "a grant that constrains nothing", would have hidden these events from the bootstrap Administrator, whose grant names all four environments | Medium | Stated; decided |
| 179 | **The database does not refuse detaching or dropping an audit partition.** Retention is designed as export, verify, `DROP PARTITION`, and the application role owns the table. `UPDATE`, `DELETE` and `TRUNCATE` are refused by triggers through both application engines, and a source gate shows nothing but an `INSERT` is issued against the trail; a strict `xfail` (`test_detaching_an_audit_partition_is_refused`) turns red the day a least-privilege role makes the refusal true. Until M6.2, "immutable" means "refused by triggers", not "unreachable by a superuser" | Medium | Open — M6.2 |
| 180 | **The dashboard cache key still ignores `alert:read` grants.** It digested `server:read` only; M4.6 added `operation:read` because `recent_operations` needs it. The alerts section is scoped by `alert:read`, so two callers who read the same servers but different alerts share an entry for the TTL (15 s). Observed, not changed (CLAUDE.md §3) | Low | Open |
| 181 | **Exports are stored in PostgreSQL (`bytea`) and the caps are design budgets, not measurements.** 100 000 events and 64 MiB, built in the worker's memory; a 24-hour expiry. An object store is `OPEN-15`, undecided; `audit_exports` would keep its metadata and lose `content`. Never run at those sizes | Low | Open |
| 182 | **Probing for audit event ids leaves no trace.** §24.2 for an event that is unknown or out of the caller's scope is `404` and writes nothing (nothing was read), as the contract says. A caller who holds `audit:read` can therefore test ids without an `AUDIT_QUERIED` row; the read-rate budget (600/min) is the only limit | Low | Stated |
| 183 | **`AUDIT_QUERIED` is one row per page fetched** (`OPEN-110`), so paging a long result writes a row per page. The audit page never refreshes on its own and shows a detail from the list it holds, to keep this proportionate | Low | Stated |
| 184 | **"`operation:read` without `server:read`" is specified but untestable end to end.** §23.1 and §23.2 ask different questions (the second needs `server:read`, else `404`); every seeded role holding one holds the other and custom roles cannot be created through the API, so the combination is covered in code (`readable_operations` against `permits("server", "read")`), not over HTTP | Low | Stated |
| 185 | **The new pages have not been looked at in a real browser, and no Playwright spec or smoke step covers them.** They are vitest-tested (axe on the open detail panel and the open export dialog) and compile into the production bundle | Medium | Open |
| 186 | **`requested_by` and the audit ids are UUID-only filters.** There is no user directory an operator can read (`OPEN-44`), so the UI offers "only my requests" and takes other ids from the address bar or from "everything with this correlation id" | Low | Stated |
| 187 | **Eight state-changing routes are not driven by the completeness test.** The four cloud-account routes and the connectivity check need a stubbed AWS layer; alert acknowledge, assign and the delivery retry need a seeded alert. The registry names, for each, an existing test that asserts the action, and a static check confirms the test exists and mentions it. The webhook is audited by its own code (`ALERT_INGESTED`) | Low | Stated |
| 188 | **A default window ending exactly at "now" compared two clocks.** Found by a full run, not by reading: five `test_audit_api` tests that passed alone failed once, consistent with a database clock a few tens of milliseconds ahead of the test process. An omitted `to` now reaches five minutes past now. The skew measured afterwards was 0.1 s the other way, so the cause is an inference from the failure pattern | Low | Fixed; cause inferred |
| 189 | **An audit read of a very large window is bounded by the 90-day ceiling and the partition pruning, not by a measured budget.** One `EXPLAIN` on a seeded multi-month table shows a week reads at most two monthly partitions and ninety days at most four; nothing was timed at production volume | Low | Open |

**Documents changed in M4.6:** API_CONTRACTS §13.1 (`recent_operations`), §17.2 (visibility), §23, §24 (24.4, 24.5, scope, `AUDIT_QUERIED`, the export), §28; DATABASE §8, §8.1 (`audit_exports`), §11, §20; SECURITY §18; TESTING §7; ARCHITECTURE §19 (`OPEN-106` – `OPEN-117`); IMPLEMENTATION_PLAN M4.6; `docs/M4.6_PLAN.md`.

### Implementation issues (found during M4.2, 2026-10-19)

| # | Issue | Severity | Status |
|---|---|---|---|
| 134 | **No operation can be created in the shipped application, and the lifecycle is proven only against a test fake.** The registry's factory map is empty and the demonstration adapter refuses writes, so §17.1 answers `503 not_configured` (`demonstration_mode` or `no_adapter_for_provider`) with no row. Everything in §7 for M4.2 ran against `tests/providers/scripted.py` and the scriptable `tests/operations/fakes.py`; none of it is evidence that any cloud works. The first real adapter (M4.3) is where "a restart on a real instance succeeded and was verified" can first be true | High (scope) | **Partly addressed in M4.3:** a real adapter now exists, so an operation can be requested when AWS is configured; it is proven against a stub only — see 146 |
| 135 | **An operation that needs approval is refused, not queued, until M4.5** (`OPEN-68`). That is a `stop` on production, a service whose policy links the seeded production approval policy, or a policy that cannot be read. `AWAITING_APPROVAL`, `APPROVED` and `REJECTED` are in the schema and the machine but no code enters them. So a production restart under the seeded `restart-only` policy cannot be requested at all. The worker's re-check is built and tested by inserting such a row directly | Medium | **Resolved in M4.5** (`OPEN-96`): a request that needs approval is queued `AWAITING_APPROVAL`; the 503 and its code are gone |
| 136 | **No resume from the persisted handle** (`OPEN-70`). A worker killed mid-operation leaves a `RUNNING` row; the reaper ends it `TIMED_OUT` / `DEADLINE_EXCEEDED` with `reconciliation_required` and the handle kept, and nothing re-invokes. A person must look the execution up at the provider; there is no screen or endpoint for that beyond the flag on §17.2. TESTING §7's "restart resumes from the handle" moved to M4.3 | Medium | **Resolved in M4.3 for a row with a handle** (`OPEN-84`, migration `0011`); a row with no handle is still timed out and flagged — see 149 |
| 137 | **The operation-failure alert condition is still not produced** (`OPEN-40`, `OPEN-77`). A `FAILED` or `TIMED_OUT` operation raises no alert and so notifies nobody; `operation_timed_out_or_failed` stays "not produced yet". `alert_events.source` already has `DCP_OPERATION` | Medium | **Resolved in M4.3** (`OPEN-89`, `app/operations/alerting.py`): a failure alerts, a success on the target resolves it |
| 138 | **A worker process is occupied for the whole operation** (up to the policy deadline, at most 3 600 s), polling. With `prefetch_multiplier = 1` the number of operations in flight is the number of `operations` workers; the rest wait in the queue and spend their deadline there. A working default, not a measurement | Medium | Open — measure in M6.3 |
| 139 | **`service_action_policies.max_concurrent_operations` above 1 is not honoured**: `uq_operations_single_inflight` is per `(server, service)` and SECURITY §13 says one in flight. The column stays; nothing reads it | Low | Open |
| 140 | **`parent_operation_id` is never set** (`OPEN-77`). §17.1's body is closed and a `retry_of` field would be a contract change, so a retry is a fresh request with a new key and is not linked to the one it follows | Low | Open — needs a contract decision |
| 141 | **Verification reads from the same provider that acted** (`OPEN-72`). Prometheus was rejected as the verifier (scrape and lookback make the outcome depend on timing) and stays the live display beside it. An independent read is an M4.3 option | Low | **Accepted in M4.3** (`OPEN-85`): not built; the verifier is still the provider that acted |
| 142 | **Worker audit rows are attributed to the requester** (`actor_id = requested_by`, `actor_label = system:operations-worker`, `on_behalf_of` in metadata; `OPEN-76`), so a search for a person's actions includes the steps their request caused. Chosen for rule 7 (traceable to the initiating identity); a `SYSTEM` actor with only `on_behalf_of` is the alternative | Info | Decided |
| 143 | **The dashboard's `recent_operations` section is still `not_configured`.** Its reason text said "not implemented yet (M4.2)", which stopped being true; it now says the history is M4.6. The section was not flipped: the history API and its scoping are M4.6 | Low | **RESOLVED in M4.6**: `recent_operations` is a real section (the ten newest operations `operation:read` reaches over the stored environment; `[]` is true and distinct from `null`). It is scope-only, not "or your own", because the dashboard cache is keyed by scope |
| 144 | **Smoke step 17c was not added and `scripts/smoke-test.sh` was not run.** The script needs `docker compose down -v`, which deletes the developer database, and it was not run without the owner's agreement; an unrun script step is the failure mode this project has hit before (M2.3 took seven smoke runs). What a step would assert: `POST …/operations` on a registered demo service answers `503` `demonstration_mode`, creates no row, and writes a `SERVICE_OPERATION_DENIED` audit row | Medium | Open |
| 145 | **A race the test found, fixed before commit** (commit 3). Two concurrent requests with one `Idempotency-Key` could reach the one-in-flight guard after the other had committed and answer `409 operation_in_progress` instead of replaying. Both the pre-check and the `uq_operations_single_inflight` violation now look the key up again before refusing. The concurrent-duplicate test failed one run in four before the fix and 6 of 6 passed after. Also corrected: the `OPEN-74` text said an in-scope reader without `operation:read` gets `403`; the route's own dependency does that (no `operation:read` anywhere), and a reader outside the target's scope gets `404` | Info | Resolved |
| 146 | **[Criterion met once, 2026-10-22, against one sandbox account — see "Executed — manual AWS check".] M4.3 had never run against AWS.** No account, no role, no credential, no instance existed, and the `aws-mcp` server failed to connect. Every test uses botocore's `Stubber` (real clients and real parameter validation, queued answers) or a local HTTP server. That proves what DCP does with an answer; it does **not** prove that AWS accepts the document, that `interpolationType: ENV_VAR` behaves as documented on the target's SSM Agent, that `GetCommandInvocation` returns the fields the parser reads, or that the role's policy allows what the adapter calls. The acceptance criterion "a restart on a non-production instance succeeds and is verified" is therefore **unmet** until the owner runs [`infra/aws/README.md`](infra/aws/README.md)'s checklist and records a dated manual check (`pytest -m real_integration --integration-profile=…`, `scripts/verify_aws_role.py`) | **High (scope)** | **Met once, 2026-10-22, in one sandbox account** (`OPEN-23` closed: the owner owns the sandbox); what that did not cover is issues 170 – 173 |
| 147 | **The SSM document is unverified in AWS** and depends on a recent SSM Agent. `interpolationType: ENV_VAR` (AWS documents it for agent 3.3.2746.0 or later; the number is from a search result, to be confirmed against the current AWS page) makes the parameters arrive as `$SSM_Action` / `$SSM_UnitName`. On an older agent they are absent; the script treats absent as invalid and exits 64, so it fails closed, but it would then fail *every* operation. The script's own validation is tested by running it under `sh` with hostile values and a stand-in `systemctl`; that is not a run on an instance | Medium | Open — the owner's check |
| 148 | **Every read is a Run Command.** SSM has no API for a unit's state, so the pre-flight identity check, the pre-flight state read and up to three verification reads are each a `SendCommand` plus polling (seconds each), against an account-level throttle. IAM cannot tell a `status` invocation from a `restart` (no parameter condition key), so the **document** is the only read/write boundary. Latencies and the read bound (30 s) are working defaults, not measurements | Low | Stated |
| 149 | **A `RUNNING` row with no recorded handle is still not recoverable.** Resume needs the handle; a worker killed between `SendCommand` and persisting it leaves a row the reaper times out as `DEADLINE_EXCEEDED` with `reconciliation_required`. Finding the command by `ListCommands` for the instance and matching its `Comment` (`dcp-op-<uuid>`, which `ListCommands` cannot filter on) is a read-only recovery tool that was **not built** (`OPEN-84`) | Medium | Open — a person reconciles |
| 150 | **The resume threshold is a working default (120 s).** A provider read can itself take close to a minute (botocore retries a read up to five times), so a shorter threshold would take over a live worker. If it does, two pollers read the same handle; only one `_finish` wins the guarded transition and neither can submit, so the cost is a duplicate read, never a duplicate action. The original worker is not told it was superseded | Low | Stated |
| 151 | **No cloud-accounts admin page and no sync button.** §26.2 – §26.6 are complete on the backend and in the generated types; an administrator uses the API directly until a page is asked for. §26.2 lists at most 200 accounts with no cursor, and does not return `last_rotated_at` (no column records it) | Medium | Open — M4.4 or later |
| 152 | **§26.5 cannot probe `ssm:SendCommand`.** The only runtime probe would send a command. It is reported in `not_probed`. The execution role's explicit deny of `AWS-RunShellScript` / `AWS-RunPowerShellScript` is checked offline: tests parse `infra/aws/iam-execution-policy.json` (each rule shown to fail on a weakened copy) and `scripts/verify_aws_role.py` asks IAM's simulator — an operator-run script whose result is a manual check, not an automated pass | Medium | Open — the owner's check |
| 153 | **The shared secret store question (`OPEN-06`) is narrowed, not closed.** AWS needs no stored secret (a role ARN is an identifier and DCP's own identity is ambient), so no Secrets Manager backend was built. Azure (M4.4) and Jenkins (M5.1) may need one. A static key in the process environment makes the AWS adapter refuse to run (`static_credentials_present`) | Info | Open — M4.4 / M5.1 |
| 154 | **Inventory sync reconciles; it does not discover** (`OPEN-88`). It fills `os_version` / `private_ip` only where empty, keeps what SSM reported in `provider_metadata.ssm`, counts unregistered managed instances (capped at 500; the audit row says if it was cut) and never registers a server or touches `environment`. There is no route to poll a sync task; the outcome is the account's `last_sync_at` / `last_sync_error` and the `CLOUD_ACCOUNT_SYNCED` audit row | Info | Stated |
| 155 | **`inventory/schemas.py` still validates AWS resource ids as `i-…` only** (issues 127 and 130 stand): an SSM hybrid-activation host (`mi-…`) cannot be registered, though the adapter's own id check accepts it | Low | Open — M6.1 |
| 156 | **`boto3` is a new runtime dependency** (with `botocore`, `jmespath`, `s3transfer`). Already the approved SDK; rungs 1 – 3 of TECH_STACK §10 fail (SigV4 and STS refresh are not a few lines); `moto`, `aioboto3` and hand-written signing were rejected. It ships no type information, so mypy treats it through a narrow override on `app.providers.aws.*` and the adapter's own surface stays typed. `pip-audit` reported no known vulnerabilities | Info | Stated |
| 157 | **The credential cache is per process.** Assumed sessions (900 s, refreshed two minutes early) are cached per (role, region, session name) in each API and worker process; an operation's own `dcp-<id>` session is reused for that operation's retries and pruned on expiry. A forced re-assume happens once on an authentication failure, never more | Info | Stated |
| 158 | **The operation-failure alert is one `WARNING` rule** whatever the environment, and a flapping target is one row counted. It fires for a `TARGET_VALIDATION_FAILED` (stale inventory) and an `EXPIRED_BEFORE_START` (a stuck queue) as well as a provider failure, and not for a denial. A failure to write the alert never undoes the operation's end (a savepoint); it is logged | Info | Stated |
| 159 | **M4.4 (Azure) is deferred, not built** (`OPEN-92`, the owner's decision: no Azure estate). PRD §4.11's Azure requirements and acceptance criteria are therefore **unmet by this release**; `AZURE` accounts are refused at registration; TESTING §6's "providers are asserted to differ" stays unwritten (issue 131) because there is no second real adapter; the `OPEN-06` secret-store question loses its other consumer (issue 153). Non-AWS servers can be monitored (metrics, logs, alerts need no adapter) but not controlled, unless they are enrolled with AWS SSM hybrid activations (`mi-…` ids, issue 155) | Medium (scope) | Deferred — reopened by a requirement |
| 160 | **M4.5 is proven against stubbed providers only.** The workflow — request, queue, decision, expiry, the worker's evidence check — is exercised through the real application, PostgreSQL and Redis, but the provider is the test-only `ScriptedAdapter` and no AWS call has ever been made (issue 146 stands). "A production restart was requested, approved by a second person and carried out and verified on a real instance" is **not** shown. The mock IdP is likewise a development aid: the two people in the tests are two logins of a fake provider | **High (scope)** | Open — the owner's manual AWS check |
| 161 | **The role and the scope are checked separately** (`OPEN-98`): `required_role` is a direct query of the decider's unexpired role names, `operation:approve` over the target is the matcher, and they are not tied to the *same* grant. A role held only in one scope plus the permission held only in another would pass both. The seeded roles cannot produce that (`APPROVER` carries `operation:approve`; no other role does), and a custom `required_role` that nobody holds in the right scope fails closed. It becomes real only if a second role is ever given `operation:approve`, or `Grant` gains a role name | Low | Stated |
| 162 | **The policy editor can weaken a gate, and the audit row is its only guard** (`OPEN-105`). An Administrator can set `allow_self_approval`, move a policy to another environment or widen the window; each is audited `APPROVAL_POLICY_CHANGED` with before and after, none requires a stated reason, and nothing stops an Administrator who also holds `APPROVER` from then approving their own request. Separation of duties is policy, not mechanism, for a team this size | Medium | Stated; a required reason on a weakening edit is the first extension |
| 163 | **A request waits holding the target's one slot** (up to the window, an hour by default), and a **required** approval whose approvers are nobody — no `APPROVER` role granted, no channel configured — simply expires. The notification is an alert through the existing channels, so with no channel registered (none is, in any real deployment: issue 114) the only way an approver learns of it is to open the queue or the alert list. The alert is also subject to the generic alert expiry (three repeat intervals), so a window longer than that can see the alert `EXPIRED` while the request still waits; the queue, not the alert, is the authority | Medium | Stated |
| 164 | **M4.2's visibility check reads the server's *current* label, not the stored environment.** §17.2 says "`operation:read` over the stored environment"; `visible_operation` (renamed from `_visible` in M4.5, otherwise unchanged) asks the matcher about `server_target(server)`. After a server is re-labelled, an approver scoped to the *new* label can see an operation made under the old one — but cannot decide it (the decision uses the stored environment, tested) and the queue omits it. Found by a test; not fixed, because it is M4.2 code and the safe direction (deny) holds | Low | **RESOLVED in M4.6** (`OPEN-107`): `operation_target(operation, server)` is the stored environment; `visible_operation`, the cancel path and the history SQL all use it; a re-labelled server no longer moves an operation in or out of scope, tested on both sides. One existing test changed: a staging-scoped approver now gets `404`, not `403`, for an old production operation, because they cannot see it |
| 165 | **The deployment half of the approvals table is unused.** `operation_type = DEPLOYMENT` is in the CHECK and refused (`422`) by §22.1; `required_approvals` above 1 is refused at request time and cannot be saved by the editor; there is no per-approver notification, delegation, out-of-office or bulk decision (`OPEN-105`). M5.4 reuses the table and these rules | Info | Deferred to M5.4 |
| 166 | **The service dialog's hint can be wrong in one direction.** `approval_required` and `approval_window_seconds` on §16 are read from the service's linked policy; a production `stop` is gated *by default* with or without a policy row, so the dialog may not warn about a request the backend will still queue. The response to §17.1 and the operation page are what is true | Low | Stated |
| 167 | **No M4.5-specific Playwright spec or smoke step.** (The existing Playwright suite and smoke script ran green on hosted CI on 2026-10-22; neither was extended for approvals. A real browser was used once, on 2026-10-22, in the manual AWS check: the operation page's waiting, approved, succeeded and cancelled states and the Approvals queue were seen against a running backend. The Approval policies page was not exercised by that check: the policy window was edited through the API.) The pages are otherwise vitest-only (`fetch` stubbed); the Playwright suite was not extended and its two spec headers still say approvals are absent; `scripts/smoke-test.sh` has not been run locally for M4.5 (it needs `docker compose down -v`, issue 144) and no approval step was added to it. The development database was not migrated by M4.5; it was, to `0012`, during the 2026-10-22 manual check | Medium | Open |
| 168 | **The queue is newest-first only.** A long queue pages by `created_at`; there is no "expiring soonest" order, and a `PENDING` row whose window has lapsed leaves the list before the sweep (≤ 30 s) has ended it, so for that moment it is in no list. The 30 s reaper cadence is a working default, not a measurement | Low | Stated |
| 169 | **Approver-facing text is in words the tests pin; the closed codes behind it are not versioned.** `details.reason` values (`self_approval_not_permitted`, `approver_role_required`, `approval_expired`, `not_awaiting_approval`, `approval_policy_unsupported`) and the worker's `reason_code`s are closed in code and documented in API_CONTRACTS §17 / §22; a client that sees an unknown one falls back to the backend's message | Info | Stated |
| 170 | **`aws login` sessions need `awscrt`, which the backend does not depend on.** With boto3 1.43.115 the default credential chain raises `MissingDependencyException` ("pip install botocore[crt]") on a profile that has `login_session`. The 2026-10-22 check ran the API, worker, beat and tests under `uv run --with awscrt`; nothing in `pyproject.toml` or `uv.lock` changed. A deployment on an instance or task role does not need it. Adding it would go through [`docs/TECH_STACK.md`](docs/TECH_STACK.md) §10 | Low | Open — only if DCP is to run from a laptop sign-in again |
| 171 | **The ambient-role path was not exercised.** The identity under test was a laptop `aws login` session assumed into `dcp-runtime-role`, not an EC2 instance profile or an ECS task role, which is what `infra/aws/README.md` step 4 recommends. The adapter takes the same boto3 default-chain path either way, but credential refresh over a long run and the one-hour cap on a chained role session were not observed | Medium | Open |
| 172 | **The refusal for a `PENDING` server is opaque, and registration was API-only.** A newly registered server is `PENDING` and only an administrator can make it `MANAGED` (`PATCH /servers/{id}`, `status`). Until then `POST …/operations` answers the generic `404`, and the UI shows "This service does not exist, or is outside your access scope" with no hint, to the one person entitled to be told. The deliberate non-disclosure for other callers is right; for an administrator it cost the owner one failed click in the check. No UI page for registering a cloud account was found (the path appears only in the generated types), and the check registered the account, server and service through the API, which also needs the CSRF token and an `Idempotency-Key` header on every write | Medium (usability) | Open |
| 173 | **The 2026-10-22 check's resources were removed from the sandbox account the same day**: the EC2 instance and its security group, three IAM roles, the SSM document and the administrator IAM user (each read back as gone except the user, which the owner confirms in the console). Re-running the check means creating all of it again from `infra/aws/README.md` | Info | Closed pending the owner's console confirmation |
| 174 | **`ADMINISTRATOR` cannot approve.** The role does not hold `operation:approve` (the approver role does), so the first administrator cannot decide a request until someone grants `APPROVER`; the refusal reads `no_matching_grant`, not `self_approval_not_permitted`. Seen in the check, consistent with the policy's `required_role: APPROVER`; stated so nobody takes the first refusal for the self-approval rule | Info | Stated |
| 175 | **CI runner image drift.** `ubuntu-latest` moved to Ubuntu 26.04, which Playwright 1.56.1 does not support (its table lists 20.04, 22.04, 24.04), so `npx playwright install --with-deps chromium` failed the `e2e` job after the M4.5 merge. `e2e` is now pinned to `ubuntu-24.04`; the other nine jobs still say `ubuntu-latest` and were not reported failing, so they were left alone. Not verified on a hosted runner by the assistant | Low | Confirmed — `e2e` green on `ubuntu-24.04` on 2026-10-22; unpin when Playwright is upgraded |
| 176 | **A new advisory turned two CI jobs red with no code change.** `certifi 2026.7.22` (transitive, via `httpx` and `botocore`) was flagged on 2026-10-22 (GHSA-6q48-6c7w-9gw2 / PYSEC-2026-4320, compromised emSign roots; fixed in `2026.10.19`), failing `pip-audit` ("Backend") and Trivy ("Build and scan images, backend"). Fixed by `uv lock --upgrade-package certifi` (only that entry moved). `pip-audit` was re-run locally and is clean; the image scan was **not** reproduced locally (Docker was not running) and is expected to pass because the Dockerfile installs with `uv sync --frozen`. Both gates did their job; expect this to recur with other packages | Info | Confirmed — the audit and the image scan were green on 2026-10-22 |
| 177 | **Smoke step 12 raced beat's start-up and also had the `grep -q` / `pipefail` trap.** `scripts/smoke-test.sh` read `docker compose logs beat` once, about 25 s after `up --wait`, and piped it into `grep -q 'beat: Starting'`. Beat's healthcheck only proves PID 1 is `celery beat`, so the container is healthy before the scheduler logs; measured on a cold Docker Desktop, the banner appeared 29 s and `beat: Starting...` 41 s after the container started (restarts 0, no crash), so the hosted run (2026-10-22) failed with "beat did not log that the scheduler started". Step 12 now polls up to 90 s on a captured copy of the log and prints the beat log tail if it still fails. Verified locally by recreating beat and running the edited block at once (waited 41 s, passed); the full `smoke-test.sh` was **not** run (it needs `down -v`, issue 144) | Low | Confirmed — the full smoke script passed on hosted CI on 2026-10-22 |

**Documents changed in M4.2:** ARCHITECTURE §6.2 (two reaper-only edges; `VERIFYING` never stored), the §6.2 allowlist note, AD-09, §19 (`OPEN-67` – `OPEN-77`); API_CONTRACTS §17 (validation order, the `503` and `422` reasons, `failure_reason` codes, `verified_at` derived), §22 (moved to M4.5); SECURITY §16 (replay before the rate limit); TESTING §7; DATABASE §6; IMPLEMENTATION_PLAN M4.2; `docs/M4.2_PLAN.md`.

### Implementation issues (found during M4.1, 2026-10-19)

| # | Issue | Severity | Status |
|---|---|---|---|
| 127 | **Provider-specific logic still exists above the adapter layer, on purpose.** `inventory/schemas.py` holds a resource-id pattern per provider and a provider → management-method table; `inventory/models.py`, `seed.py`, `router_servers.py` and `dashboard/router.py` hold the closed enums. `scripts/check_provider_branching.py` allowlists exactly those five files (a test pins the set; a stale entry fails too), and **nothing inside them is checked by the script** — the review is. My reading is that this is registration-time validation of closed data and not behavioural branching (no `if provider == …` picks a code path that touches a provider), so M4.1's acceptance criterion is met in that sense and **not** in the literal "no provider names" sense (`OPEN-65`, approved by the owner). Revisit in M4.3, when an adapter exists that could own `ServerRef` validation | Medium | Open, accepted |
| 128 | **A pre-existing intermittent failure: `tests/integration/test_alerts_api.py::test_the_source_still_resolves_an_acknowledged_alert_and_history_is_kept`.** The history it asserts (`first_seen`, `acknowledged`, `resolved`) comes back with `acknowledged` first. It failed 2 of 4 isolated runs on `phase-M4.1` and 2 of 6 on an unmodified `main` worktree, and failed once in the final `check-all`; it passed in the first `check-all`. `alerts/read.py::_history` sorts by timestamp, mixing `first_seen_at` (written by the code under test) with the audit row's `occurred_at` (a transaction timestamp, issue 51), so a skew of milliseconds reorders them. **Hypothesis, not verified:** a clock difference between the Windows host and the Docker PostgreSQL. Not caused by M4.1 and not fixed here | Medium | Open |
| 129 | **Smoke step 17b was written, and the smoke test was held back until the owner agreed to its `down -v`** (it uses the default Compose project, so it deletes the volumes holding the developer database and `dcp_test`). Run on 2026-10-19 with that agreement: it passed, step 17b included. Cost, for the record: the developer database (its users and audit rows) is gone; Postgres and Redis were started again and the Postgres init script recreated `dcp_test` (checked, and the 13 new integration tests pass against it). The step's assertions are string matches on the JSON, so they would not notice a *new* field that mentions a credential-shaped word under another spelling | Low | Resolved |
| 130 | **`ON_PREM` / `OTHER` accounts have no adapter, and a hybrid-activation host cannot be registered.** `OPEN-05` is closed as "adopt, do not build", but SSM hybrid activations produce `mi-…` ids under an AWS account and `inventory/schemas.py` accepts only `i-…` for that provider. An M4.3 / M6.1 question | Low | Open |
| 131 | **"Providers are asserted to differ" is not tested, and cannot be with one adapter.** M4.1 pins the demonstration column of the §7.2 matrix against the document (a change to either fails) and built the parametrised conformance harness; the disagreement assertions arrive with the second real adapter (M4.4). The harness is shown to reject eight adapters written to break one rule each, and accepts the demo adapter and a test-only `ScriptedAdapter` | Low | Deferred (`OPEN-61`) |
| 132 | **Documents changed in M4.1:** ARCHITECTURE §7 (typed signature, the Demo column, a §7.2 note), §10.2 (read and write retry columns), §19 (`OPEN-58` – `OPEN-66`; `OPEN-05` closed); API_CONTRACTS §3 (`422 unsupported_operation`, class → status), §14.8 (moved to M4.3), §26.1 note, §26.7 shape; TESTING §6; IMPLEMENTATION_PLAN M4.1 | Info | Done |
| 133 | **`check-all.sh`'s backend coverage line listed `providers/service.py` lines 45–51 and 62–63 as uncovered** although the integration suite exercises them: the changed-line gate seems to read the unit run's `coverage.xml`, so route code covered only by integration tests is understated (the same cause as issue 80). The gate still passed (97.5 %) | Low | Open |

### Implementation issues (found during M3.4, 2026-10-19)

| # | Issue | Severity | Status |
|---|---|---|---|
| 113 | **The `env://` secret backend is not a production secret manager.** `DCPSECRET_NAME` is read from the process environment (a development / small-deployment backend, `OPEN-48`); AWS Secrets Manager and Azure Key Vault are not built (they arrive with M4.3 / M4.4) and **`OPEN-06` stays open**. `DCP_SMTP_PASSWORD` is an ordinary `SecretStr` setting for the same reason | Medium | Stated; before a real rollout |
| 114 | **Slack and Teams payloads are written from those products' public documentation and were never sent to a real workspace or tenant**; neither was a real mail provider. The only receiver ever exercised is the local `notification-sink` (smoke 18h), which proves what DCP sends, to where and how it retries — not that a provider accepts it | Medium | **Not verified**; a dated manual check against a test destination is the only honest claim and could not be done here |
| 115 | **Delivery is at-least-once.** A worker that dies after sending leaves a row that becomes due again (a duplicate message is possible, a lost one is not), and the claim and the send are two tasks, so a broker redelivery of `deliver(id)` while the first run is still in flight can send twice | Low | By design (`OPEN-53`); documented |
| 116 | **A channel hears that a problem began, not that it ended.** No notice for a repeat, an acknowledgement, a resolve or an expiry (`OPEN-51`); adding resolved notices is one migration (a `kind` in the uniqueness). There is also no send-test / verify route (`OPEN-57`), so a mistake appears as a `FAILED` delivery, not before | Medium | Product decision deferred |
| 117 | **No per-channel rate limit or digest.** A fleet outage sends one message per alert; Slack's `429` is handled by backoff (`Retry-After` can lengthen a wait, capped at 1 h) | Low | Deferred (`OPEN-57`) |
| 118 | **`.env.example` ships empty SSRF allowlists on purpose**, so a fresh clone can register no webhook channel until the deployment names its hosts. The smoke test exports `notification-sink`, the Docker bridge ranges and the SMTP settings itself; `DCPSECRET_NOTIFY_SINK` defaults to the mock sink in Compose, which only exists in the `mocks` profile | Low | By design |
| 119 | **The SSRF lists and every notification setting now reach the containers** (issue 54's list of settings that did not shrinks by the two SSRF lists), but the other OIDC tunables and read-rate-limit values still do not | Low | Stated |
| 120 | **A pre-existing test-ordering trap, found while running a subset.** `pytest tests/authz tests/alerts tests/metrics tests/integration/test_alerts_api.py` hangs on the first server registration (120 s timeout): the Celery singleton is built under the unit-test placeholder Redis when no earlier test has configured it. The same subset hangs at the commit-3 baseline, so M3.4 did not cause it; the full suite's order avoids it | Low | Known; the task tests set the broker first, other orders can still trip it |
| 121 | **A destination can pass the write-time check and be refused at send time** (the write-time check resolves the secret and validates the URL against the allowlist, but does not resolve DNS; the address check is at connect time). The first symptom is a `FAILED` delivery with `destination_refused`, not a `422` | Low | By design (SECURITY.md §19: the address actually connected to is the one checked) |
| 122 | **STARTTLS name verification was measured against a real handshake in a throwaway script, not in the suite.** The unit test asserts that DCP connects to the checked address with the host name set for TLS and a verifying context; it does not run TLS | Low | Spike result recorded in `OPEN-49` / `OPEN-54`; no automated TLS test |
| 123 | **`GET /integrations/status` now needs a database session** (one row per channel); if the database cannot answer, the notification row says `database_unreadable`. The channel list is capped at 200 rows and is not paged | Low | Stated |
| 124 | **No 180-day drop for `alert_events` and no 400-day drop for `audit_events`** (unchanged, issue 107). `notification_deliveries` rows older than 90 days that are not `PENDING` *are* deleted by a daily task | Medium | Open (107) |
| 126 | **The frontend production image's Alpine packages age between nginx releases.** `nginx:1.30.6-alpine` is rebuilt only when nginx is, so CI's Trivy gate (HIGH/CRITICAL with a fix) failed on `libexpat` and `libx11` on 2026-10-19, unrelated to any M3.4 code. Fixed by `RUN apk upgrade --no-cache` in the production stage (verified locally: 0 vulnerabilities). A base image pinned by digest (AD-16, still open) would make this a deliberate bump rather than a surprise; until then the gate will fail whenever Alpine ships a fix the base lacks | Medium | **Fixed**: merged as PR #23; the hosted pipeline was then reported green by the repository owner (2026-10-19) |
| 125 | **Three defects found by running things, each fixed before commit 6:** `$` in the reference and mailbox patterns accepted a trailing newline (found by the first unit test of each); **`smtplib` raises a 4xx recipient refusal as `SMTPRecipientsRefused`, which was classified as permanent** (so an email delivery went `FAILED` instead of retrying — found by the second smoke run, because the harness only said "False"; the harness now prints the deliveries on a timeout); and smoke 18g's `recovered` mode selected the first resolved alert, which is an older one from step 18a (a latent harness bug exposed by 18h) | — | Fixed |

### Implementation issues (found during M3.3, 2026-10-18)

| # | Issue | Severity | Status |
|---|---|---|---|
| 102 | **The webhook accepts only the rules DCP generates.** An `alertname` outside `app/alerts/catalogue.py` is `rejected: unknown_rule`, so a rule added by hand to `infra/prometheus/rules/` produces **no row** until the catalogue knows it (a code change). Deliberate — severity, discriminators and meaning must come from DCP, never from the payload (`OPEN-41`, SECURITY §21) — but it is a closed set an operator can trip over | Medium | By design; the rejection is counted in the audit row and the response. A configured rule that never appears is the symptom |
| 103 | **Every `for:` duration is a working default, not a measurement** (CPU 10 min, memory 5 min, disk 5 min, failed unit 1 min; "stopped reporting" follows each environment's `stale_after_seconds`). The alert thresholds are `DCP_UTILISATION_THRESHOLDS`; nobody has tuned any of it against a real fleet | Medium | Constants in `app/alerts/catalogue.py`; revisit with Phase 6 measurements (`OPEN-46`) |
| 104 | **`INFO` is a valid severity and nothing produces it.** The dashboard counts it; the server page's `open_alerts` carries only `critical` and `warning` (its contract shape), so an open `INFO` alert would not appear in that count | Low | Nothing raises `INFO` today; extend the shape when something does |
| 105 | **The development defaults are a placeholder secret and the whole private address space.** Compose supplies `DCP_ALERT_WEBHOOK_SECRET` (a documented local-development value, as `DCP_OIDC_CLIENT_SECRET` has) and `DCP_ALERT_WEBHOOK_ALLOWED_CIDRS=10/8,172.16/12,192.168/16`. Both must be set explicitly anywhere that matters; with the secret unset the endpoint is `503`, and with the CIDRs empty it refuses everyone. Rotating the secret means restarting both ends — **no two-live-secrets rotation** | High for a real rollout | Documented in `.env.example` and SECURITY §21; Phase 6 (alongside `OPEN-15`, `OPEN-31`) |
| 106 | **Smoke 18g posts the *repeat* itself and cannot test the address allowlist.** Alertmanager re-notifies a firing alert only every `repeat_interval` (1 h), so the repeat is posted to the real endpoint with the real secret and the stored labels (it proves deduplication, not Alertmanager's scheduling); and every peer in Compose is a private address, so a refusal by source address is covered only by `tests/alerts/test_webhook_unit.py` and `tests/integration/test_alert_webhook.py` | Low | Labelled in the harness and in TESTING §11 |
| 107 | **No retention drop is built for `audit_events` (400 d) or `alert_events` (180 d).** Only the runway is maintained (and, since M3.3, alerted on). Old partitions stay until a job drops them; DATABASE.md §9.1 and §11 describe the intent | Medium | Not M3.3; needs an export-then-drop job for audit (OPEN-08's durations are decided) |
| 108 | **There is no assignee picker.** The UI offers "assign to me" and "unassign"; the API accepts any active user who holds `alert:acknowledge` and whose scope reaches the alert. A picker needs a user directory an operator may not read (`admin:users` is Administrator-only) — a product decision (`OPEN-44`) | Low | Deferred with its reason |
| 109 | **A stopped Alertmanager reads as `alertmanager_timeout`, not `alertmanager_unreachable`.** On the Docker network a stopped container's name stops resolving or the connect hangs to the 3 s probe deadline; a refused connection gives `unreachable`. Both are the outage and both are closed reason codes; the smoke harness accepts either (found by the first real run) | Low | Fine as it is; stated so a reader does not expect one code |
| 110 | **The failed-unit rule fires for any failed `*.service` the exporter reports on a scraped host**, not only registered services. A registered unit links the alert to the service; an unregistered one makes an alert about the *server* with the unit name in `details`. A noisy host with many failing unregistered units raises many alerts | Low | Registration is the lever; no per-host suppression exists |
| 111 | **Dashboard alert counts can be up to `DCP_DASHBOARD_CACHE_TTL_SECONDS` (15 s) behind the alert list**, by the M1.6 design (a write is not reflected until the entry expires); `pipeline` is read at the same moment | Low | By design; `0` disables the cache |
| 112 | **`check-all.sh`'s two `promtool` steps need Docker** and are listed as skipped, not passed, where it is missing; the new CI `alert-rules` job runs them on every push but has not yet run on a hosted runner | Low | Stated; CI not yet observed |

### Implementation issues (found during M3.1, 2026-10-17/18)

| # | Issue | Severity | Status |
|---|---|---|---|
| 92 | **A multi-line PEM private key is not redacted — by Alloy or by DCP's own pass.** Both layers match one log entry at a time, and a key written across lines arrives as several entries. A one-line (or JSON-escaped) key *is* redacted. Pinned: the canary corpus carries a `known_gap` multi-line key, and both `test_canary_corpus.py` and smoke 18d assert that it leaks, so the limit cannot be forgotten or quietly "fixed" on paper. The control is not emitting multi-line secrets. A `stage.multiline` join was considered and rejected as too fragile to trust. | Medium | Accepted, documented in SECURITY.md §17 |
| 93 | **Nothing can tell a quiet host from a blind shipper.** A shipper that matches no files, or cannot read them, logs no error and sends nothing (issue 95 is the proven case), and a host with no log lines looks identical. Needs a per-server "last log received" signal — a freshness state in the style of AD-14 — before an empty log search can be called trustworthy. M3.2 must not render "no logs" for a server with no recent ingestion as though it were information. | High for M3.2 | Open; M3.2's plan |
| 94 | **No per-server Alloy configuration exists.** Identity is five `ALLOY_*` environment variables; the sample stack uses a fixed synthetic server id that is **not a registered server**, so M3.2's scope-filtered UI will not show these lines until a server with that id is registered (or the id is made to match one). Rendering config from inventory — and getting it onto a host — is unbuilt and has no milestone yet; the agent work (`OPEN-05`, M6.1) is the likely home. | Medium | Open |
| 95 | **The Docker source is proven against a real daemon only by one manual run, and it needs uid 0.** Docker keeps `/var/lib/docker/containers` as `root:root` `0710` with `0640` files. Alloy as uid 473 — even with gid 0, which can enter the directory but not list it — matches **no files and logs no error**. As uid 0 with `--cap-drop ALL`, `no-new-privileges` and a read-only mount it works: it took `service` from the container's `service` label, redacted a canary and kept only allowed labels (2026-10-18, a `docker run` on Docker Desktop's VM). That is an exception to SECURITY.md §23's non-root rule, now written there. Not in the smoke test: the Compose service reads the producer's files as uid 473, so it cannot prove this path. An ACL granting a non-root user list/read is the alternative and is untested. | Medium | Open; documented |
| 97 | **The sample log host is registered by a flag, not the real fleet.** `seed --sample-log-host` gives the *sample* shipper a registered server (fixed ids) so the UI and smoke test show logs. A real managed server still needs its Alloy configured with its own `server_id` by hand; nothing renders that config from inventory (issue 94 stands, and is now the first thing a real rollout hits). | Medium | Open |
| 98 | **The ingestion probe's cost is unmeasured.** Each first page runs two `count_over_time` queries over every selected server (15 minutes and 24 hours, up to 200 servers). Fine against the sample stack; at fleet scale the 24-hour count reads chunks. On failure the state is `unknown` (never `silent`), so it degrades honestly, but a slow probe still delays the first page. Cheaper shape if it matters: a short-lived Redis cache of the per-server counts. | Medium | Open, unmeasured |
| 99 | **Tie order within one stream is trusted, and verified only by the last line's hash.** Cross-stream tie order no longer matters (total order plus a full re-read of the boundary timestamp), but lines sharing a nanosecond *within one stream* must come back in the same relative order on the next page. Loki returned them stably in every real run (smoke 18f, sizes 1–9, both directions), and a mismatch is `422 invalid_cursor`, never a repeat. A group of 5 000+ lines in one nanosecond is `503`. | Low | Accepted, documented |
| 100 | **No Playwright path for logs.** The page is covered by component tests and the API by the integration suite and smoke 18f; nothing drives the page in a browser against a real backend (E2E path 3 / issue 60 is the same gap). | Low | Open |
| 101 | **A unit shipped as `other` cannot be selected by name.** Instance units (`getty@tty1.service`) and names over 63 characters collapse to `service=other` at the shipper (AD-21), so §16.7 answers `422 unselectable` for them rather than an empty page. Correct, and a visible limit of the closed label set. | Low | Accepted |
| 96 | **Removing `stage.label_keep` fails the smoke ingestion assertion by timeout, not by a label message**, because Loki's seven-label limit then rejects every push and nothing arrives to inspect. Two layers hold, but a future failure of that shape reads as "timed out waiting for all five file sources". | Low | Noted |

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

### Executed — M4.6 (2026-10-22/23)

- **`scripts/check-all.sh`** was run on the branch after commit 6 (`b0eee65`), with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set to the Compose PostgreSQL (port 5433, `dcp_test`) and Redis (port 6380). **Everything passed except the integration step**: ruff lint and format, mypy, bandit, unit **2313 passed** (2 skipped, 1390 deselected), pip-audit, eslint, prettier, tsc, vitest **537 passed** (38 files), build, npm audit, generated API types unchanged, `.env.example`, the alert rule file, `promtool check rules` and `test rules`, backend changed-line coverage **713/797 (89.5 %, floor 80 %)** and frontend **264/292 (90.4 %)**, the provider-branching gate, doc links. Its integration step: **1387 passed, 2 failed, 1 xfailed (the strict `xfail`)**. Both failures were my own tests leaving committed rows that other suites count (`application_servers` associations and notification channels from the completeness test); the fixture now removes them.
- **The integration suite was then re-run on the fixed code and failed five `test_audit_api` tests** that pass alone (48 passed) and that had passed in the two runs before. The cause is inferred, not reproduced (issue 188): a default window ending exactly at "now" compared the application's clock with the database's. Fixed with `CLOCK_SLACK`. **Integration on the final code: **1389 passed, 1 xfailed** (the strict `xfail`), 2317 deselected, in 609.65 s.**
- **Re-run on the final code:** ruff lint and format, mypy, bandit, unit (**2315 passed**, 2 skipped), the integration suite above, and the doc-link check (563 links, 0 problems). **Not re-run:** the rest of `check-all.sh` — pip-audit, the frontend steps, coverage and promtool — because the final commit changed `app/core/window.py`, one test file, `tests/test_window.py` and documents only; the frontend was untouched since its green run.
- **Mutation checks, each seen failing a named test, files restored byte-identical:** the scope predicate removed from the audit read (four named tests); redaction on read removed; `AUDIT_QUERIED` removed from the list read (four); the cover-all rule forced to false in the SQL, and the stored environment swapped for the server's label (the SQL-versus-matcher cross-checks failed for each); an `audit.record` removed from `DELETE …/services/{name}` (the completeness test for that route); the `status = 'READY'` guard removed from the download statement (three, including the blocked-second-session test); the requester's re-authorisation removed from the export build (demoted and disabled requesters); an operation's reason added to its audit metadata (the secret sweep).
- **Found by running it, not by reading:** the agreed rule for events with no environment would have hidden every login from the bootstrap Administrator (read from `matcher._in_scope` and the bootstrap CLI); the build's page size of 500 exceeded the read query's own ceiling of 200 (the export ended `internal_error`); the first secret sweep failed because an inventory edit audits its before and after by design (the claim was split in two); a partition `DETACH` is not refused by the database (the claim was reduced); a script that edited source with Python's default Windows encoding mangled two files (restored from git and redone); and the five-test clock flake above.
- **New tests.** *Unit:* `test_window.py`, `test_history_query.py`, `test_audit_query.py`, `authz/test_environmentless.py`, `authz/test_audit_registry.py`, additions to the route-coverage and dashboard files. *Integration:* `test_audit_exports_schema.py` (every CHECK and index), `test_history_and_audit_scope.py` (SQL against matcher for operations and audit events over seventeen scopes and both effects, re-labelled servers, paging across ties and NULLs for every sort and direction), `test_history_api.py` (37), `test_audit_api.py` (48), `test_audit_export_api.py` (40), `test_audit_completeness.py` (29: 28 routes driven), `test_audit_evidence.py` (13 + the `xfail`), `test_audit_query_plan.py`, the migration tests for `0013`, and four dashboard tests. *Frontend:* `history.test.ts`, `OperationHistory.test.tsx`, `AuditLog.test.tsx`, `AuditExport.test.tsx`, three dashboard tests.
- **Not executed, and therefore not claimed:** the Compose smoke test (`scripts/smoke-test.sh`, which needs `down -v`, issue 144) — no smoke step was added; the Playwright suite; the new pages in a real browser (issue 185); hosted CI; a Celery worker consuming `dcp.audit.export` from a real broker (the task body is called directly, and the task's name, queue, signature and schedule are asserted); an export at its caps; anything against real AWS.

### Executed — manual AWS check (2026-10-22), issue 146

**A manual check, not an automated pass.** Run step by step by the owner (console) and the assistant (read-only AWS CLI checks, the application, the database), against a throwaway **sandbox** AWS account (12-digit id ends `2076`), region `ap-south-1`, with the working tree of `phase-M4.5` unchanged (no repository file was edited to run it). Never production.

- **Created in the sandbox (console, by the owner):** IAM role `dcp-test-instance-role` (managed `AmazonSSMManagedInstanceCore`); one EC2 `t3.micro`, Amazon Linux 2023, tag `DcpManaged=true`, no key pair, no inbound rule, IMDSv2 required, SSM Agent `3.3.5390.0` (the documented floor is 3.3.2746.0), Fleet Manager `Online`; IAM user `dcp-admin` (`AdministratorAccess`, console access, **no MFA**; the root user cannot assume roles); `dcp-runtime-role` (trusts only `dcp-admin`; one inline policy, `sts:AssumeRole` on the execution role); `dcp-execution-role` (trusts only `dcp-runtime-role`; inline policy = `infra/aws/iam-execution-policy.json` with the placeholders filled, **read back from AWS and compared**); SSM document `DCP-ServiceControl` **version 1**, fetched from AWS, parsed and **identical** to `infra/aws/ssm/DCP-ServiceControl.json`.
- **Identity, no keys:** profile `dcp-runtime` in `~/.aws/config` (`role_arn` = the runtime role, `source_profile` = the `aws login` session of `dcp-admin`). No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` in any process environment. An unrelated static-key profile found in `~/.aws/credentials` was removed locally at the owner's request.
- **Results, each seen and not assumed:**
  1. **Role chain.** `dcp-runtime` assumed `dcp-execution-role`; it was refused `iam:ListRoles` and refused assuming any other role (`dcp-test-instance-role` tried).
  2. **IAM policy simulator**, as the execution role, `ssm:SendCommand`: allowed on the document and on the tagged instance; implicitly denied on the instance without the tag and on another document; **explicitly denied** on `AWS-RunShellScript` and `AWS-RunPowerShellScript`.
  3. `scripts/verify_aws_role.py` printed **`OK`**.
  4. `pytest -m real_integration --integration-profile=nonprod-sandbox` (unit `chronyd.service`): read-only run 1 passed, 1 skipped; with `REAL_AWS_ALLOW_RESTART=yes` **2 passed** in 22.4 s.
  5. **The whole application against it** (`DCP_DEMO_MODE=false`; PostgreSQL migrated `none` → `0012`, Redis, mock IdP, API, Celery worker and beat, Vite, all but the first two native on Windows): account registered with `credential_reference` = the execution role's ARN and verified (`reachable`, `permissions_ok`, `not_probed: ["ssm:SendCommand"]`; capabilities `adapter_state: configured`, `adapter_id: aws_ssm`, `demo_mode: false`); server registered, connectivity check `reachable`, `provider_status: online`; `chronyd.service` bound to the seeded `restart-only` policy.
  6. **Restart through the UI in a real browser, `development`:** `SUCCEEDED`, `approval_status NOT_REQUIRED`, state before and after `ACTIVE`, steps `claimed → validated → submitted → completed → verified`; audit `REQUESTED` (the administrator), `STARTED` and `COMPLETED` (`system:operations-worker`). AWS's own history shows exactly `status → restart → status`, and the restart's command id equals the operation's `external_execution_id`.
  7. **Production approval.** Request → `AWAITING_APPROVAL`, with **zero AWS commands while it waited** (count unchanged). The requester's approve was refused `403` twice for two different reasons: first `no_matching_grant` (`ADMINISTRATOR` does not hold `operation:approve`), then, with `APPROVER` granted for the test and revoked at once, `403 self_approval_not_permitted`, audited as `SERVICE_OPERATION_DENIED`. A second person (`operator`, `APPROVER` scoped to `production`) approved in the Approvals page → `APPROVED`, `SUCCEEDED`, verified; AWS received `status → restart → status` starting about 4 s after the decision; the audit row `SERVICE_OPERATION_APPROVED` names the operator.
  8. **Expiry.** With the seeded production policy's window temporarily 60 s (restored to 3600 s), an unanswered request ended `approval_status EXPIRED`, `execution_status CANCELLED`, with no `started_at` and no `external_execution_id`, cancelled by `system:approval-sweeper` 15 s after its deadline; AWS's command count was unchanged.
- **Not covered, and therefore not claimed:** production AWS; a second account or region; DCP running under an **ambient instance or task role** (a laptop `aws login` session assumed into the runtime role was used, issue 171); `start` and `stop`; any failure path against real AWS (a refused or failed command, a timeout, a restart whose verification fails); two approvers; Prometheus, Loki and every other integration (not configured); the Playwright suite, `scripts/smoke-test.sh` and the hosted CI run; Azure; the worker under load.
- **Removed again the same day, at the owner's request** (issue 173): the EC2 instance (`terminated`) and its volume and `launch-wizard-2` security group, the SSM document `DCP-ServiceControl`, and the roles `dcp-execution-role`, `dcp-runtime-role` and `dcp-test-instance-role` (with its instance profile) were each deleted and then read back from AWS as not found. The IAM user `dcp-admin` was deleted last (`delete-user` returned success); it could not be re-read with its own session, so the owner confirms it in the IAM console. The local `dcp-runtime` and `[default]` login entries in `~/.aws/config` were removed. Nothing from this check remains in the account except what the owner confirms in the console.
- **Left on the development machine:** the development PostgreSQL now at `0012` and holding the mock `admin` (`ADMINISTRATOR`) and `operator` (`APPROVER`, production) users, one AWS account, one server (`development`, `MANAGED`) with one service, and three operations (one `SUCCEEDED` in development, one `SUCCEEDED` after approval, one `EXPIRED`). The account and server rows now point at AWS resources that were deleted afterwards, so a restart or verify from that database would fail until they are re-registered.

### Executed — M4.5 (2026-10-20)

- **`scripts/check-all.sh`, run on the final code of the branch (commits 1 – 5, before the evidence commit), with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set to the Compose PostgreSQL (port 5433, `dcp_test`) and Redis (port 6380): ALL CHECKS PASSED, nothing skipped (a first run found two test-order failures, fixed in the last commit; this is the second, full run).** ruff lint and format, mypy (179 source files), bandit, unit **2217 passed** (2 skipped: the two `real_integration` tests; 1069 integration tests deselected), integration **1069 passed** (661 s), pip-audit (no known vulnerabilities), frontend eslint / prettier / tsc / vitest **484 passed** (34 files) / build / npm audit, generated API types unchanged, `.env.example` complete, the generated alert rule file current, `promtool check rules` and `test rules`, the provider-branching gate, doc links 548 / 0. Backend changed-line coverage **585/724 (80.8 %, floor 80 %)**, frontend **239/261 (91.6 %)**. The first full run had failed two integration tests: the seeded-policy test saw `test-ops-all` committed by a suite that now sorts ahead of it, and the migration round-trip test tripped 0012's downgrade guard on `EXPIRED` operations the approvals tests had left behind — both test-order interactions, fixed in the tests (the guard is the intended behaviour).
- **New tests.** *Unit:* `tests/approvals/test_rules.py` (160: `resolve_policy` over environment × action × seven policy states, `may_decide` over requester/decider × policy flag × four grant shapes × three role sets, `approval_valid_for_execution` for every refusal, the last-instant boundary and the closed refusal set), the guarded-statement builder (`tests/operations/test_store_statement.py`, 3 added). *Integration:* `test_approval_schema.py` (19: every CHECK and index, the policy FK, `EXPIRED`, the permission held by `ADMINISTRATOR` only) and a migration test that `0012`'s downgrade **refuses** while an operation is `EXPIRED`; `test_approvals_api.py` (34: the request path, the queue's fields and SQL scoping, filtering and paging, approve and reject, the decision body accepting nothing but a reason, self-approval refused and audited, a policy that allows it, role, scope, a re-labelled server, cancel by either party, a late decision `409 approval_expired` with the sweep's effect applied, the sweep, the reaper running it, `deadline_at` never timing out a waiting request, **decide-versus-cancel racing six times and, deterministically, a second session blocking on the first's uncommitted decision and then losing**, the window and the sweep excluding each other exactly, and three end-to-end cases through the worker including **a direct execution attempt on a waiting request doing nothing**); `test_approval_bypass.py` (27: each case builds the row in SQL and each is refused with `APPROVAL_REQUIRED`, a closed `reason_code`, **zero provider calls and no pre-flight read**, and an audited denial; plus a positive control, the resumer refusing and following, and a policy that now needs two approvals); `test_approval_policies_api.py` (33: audited create / edit / delete with before and after, a no-op writing nothing, twelve inputs the workflow would not honour refused and not saved, the referenced-policy and in-use deletions, non-administrators `403`, read-only `423`). *Route gate:* the approval and editor routes declare the permissions the contract names. *Frontend:* `ApprovalQueue.test.tsx`, `ApprovalPolicies.test.tsx`, `api/approvals.test.ts`, and additions to `OperationDetail.test.tsx` and `OperationActions.test.tsx` (the four facts, reject needing a reason, the own-request control disabled and the backend refusal still shown, every refusal in words, one request per double click, the time left, expired / awaiting / approved / rejected / withdrawn states, axe on the open dialogs).
- **Mutation checks, each seen failing named tests, files restored byte-identical and diffed after each:** the worker's evidence check reduced to the flag (19 failures, the first being `test_a_row_set_to_approved_in_sql_with_no_approvals_row_is_refused`); the self-approval comparison removed from the worker's check (3: `test_a_row_approved_by_its_own_requester_is_refused_under_a_no_self_approval_policy` and two unit tests) and from `may_decide` (4: the unit matrix and `test_a_requester_cannot_approve_their_own_request`); the scope check removed from the worker's check (4: `test_an_approver_whose_scope_no_longer_reaches_production_is_refused` and three unit cases) and from `may_decide` (39, including at API level `test_an_approver_who_can_read_but_not_approve_here_is_refused_and_it_is_audited` and `test_a_stored_environment_not_the_servers_current_label_decides_scope`); the guarded `WHERE decision = 'PENDING'` removed from the decision statement (2: `test_a_second_decision_statement_finds_nothing_to_decide` and `test_the_guard_decides_a_race_at_the_sql_level_not_by_timing`). The driver is `scratchpad/mutate.py`, not committed.
- **Found by running it, not by reading:** the first bypass case that inserted a `PENDING` approvals row with a decision time was refused by `ck_approvals_decided_consistent` — the schema, not the test helper, caught it; the plan's "approve twice ⇒ 409" is answered by an application pre-check before the guarded write is reached, so only the SQL-level tests can show the guard itself matters (the first mutation that removed it passed the API tests and failed the store-level ones, which is why those exist); `approvals.created_at` had to be set to the operation's own `now` for the window arithmetic to be exact; SQLAlchemy 2.1's `Select` takes one type argument per column, so the queue's seven-column query needed `Select[*tuple[Any, ...]]`; and M4.2's visibility check reads the server's current label (issue 164).
- **Not executed, and therefore not claimed:** **anything against a real AWS account** (issue 146; issue 160); `scripts/smoke-test.sh` (issue 144; no smoke step added); the Playwright E2E suite; the new pages in a browser against a running backend; the hosted CI run; a Celery worker consuming `dcp.operations.execute` from a real broker for an approved row (the task body and `execute.run` are called directly, the singleton trap); the sweep under load; the development database (`dcp`) migrated to `0012`.

### Executed — M4.3 (2026-10-20)

- **`scripts/check-all.sh`, run on the final code of the branch (before the evidence commit), with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set to the Compose PostgreSQL (port 5433, `dcp_test`) and Redis (port 6380): ALL CHECKS PASSED, nothing skipped.** ruff lint and format, mypy (169 source files), bandit, unit **2054 passed** (2 skipped: the two `real_integration` tests, which skip without `--integration-profile` by design; 955 integration tests deselected), integration **955 passed** (1141 s), pip-audit (no known vulnerabilities; `boto3` included), frontend eslint / prettier / tsc / vitest **422 passed** (31 files) / build / npm audit, generated API types unchanged, `.env.example` complete, the generated alert rule file current, `promtool check rules` and `test rules`, the provider-branching gate, doc links 534 / 0. Backend changed-line coverage **940/1033 (91.0 %)**, frontend **48/53 (90.6 %)**, both against the 80 % floor. (The earlier commits were each checked with ruff, mypy, bandit, the unit suite and the integration suites they touched; two full integration runs on intermediate code — 943 passed, then a targeted 208 — are in the transcript, not claimed here beyond the final run.)
- **New tests.** *Unit:* `tests/providers/aws_artifacts/` (48: the SSM document and the IAM policies parsed, each rule shown failing on a weakened copy, and **the document's script executed under `sh` with a stand-in `systemctl`** against 13 hostile unit names, 6 hostile actions and absent parameters — nothing is called before the input is refused); `tests/providers/aws/` (145: the adapter over a `botocore` `Stubber` — the capability column pinned, the read path with polling and every output token, the credential cache and the single forced re-assume, the write path, throttling, every non-throttle write failure sent exactly once, status mapping, error mapping for every code, account verification, instance facts and paging, the registration rules, the static-key refusal, the shared conformance harness — and **a real local HTTP server that returns `500` and counts requests: the write client sends once, the read client retries**); two `real_integration` tests (skipped here). *Integration (real PostgreSQL and Redis):* `test_operations_aws.py` (6: a restart through the real adapter to `SUCCEEDED` with `observed_state_after`, a mismatch, throttling beyond the budget, a denied role, an instance SSM no longer knows, a static key); `test_operations_resume.py` (21: poll from the handle without a second send, no resume of a live, handle-less, overdue or non-`RUNNING` row, **one winner of two resumers**, a revoked permission or disabled account ⇒ `RESUME_REFUSED` flagged, the healer, the region boundary, the task shape); `test_operations_alerts.py` (12); `test_cloud_accounts_admin.py` (50: §26.2 – §26.6, §26.1 and §14.8 end to end — secret-shaped values refused and not echoed, every non-admin refused, verify's cache, sync's lock, reconciliation never creating or re-environmenting a server, connectivity changing nothing about the server, the rate limit).
- **Mutation checks, each seen failing named tests, files restored and diffed after each:** the document script's unit validation removed (14 tests); the write client's retries raised from 0 (the HTTP-server test); the throttle cap removed; the resume claim's compare-and-set removed (the two-resumer test); the resumer made to call `execute_operation` (the "never sends" tests); the alert producer's denial exemption removed (the denial test); the success-resolves-alert step removed (the resolve test).
- **Found by running it, not by reading:** botocore's `Stubber` validates a response's shape, which exposed three test-data mistakes of mine (key and token minimum lengths, a `CommandId` minimum length); `Config(retries={"max_attempts": …})` counts *retries*, so the write client uses `0`, and only the HTTP-server test could show that the number does what the name suggests; a throttled write retry was assuming the role again on every attempt until the cache covered the operation's own session; and the alert producer left `DCP_OPERATION` rows behind that tests counting alerts would have tripped over.
- **Not executed, and therefore not claimed:** **anything against a real AWS account** (none exists; no credential was used; issue 146); the SSM document on an instance or with a real SSM Agent (issue 147); `scripts/verify_aws_role.py` against a real role; `scripts/smoke-test.sh` (it needs `docker compose down -v`, issue 144, and M4.3 added no smoke step); the Playwright E2E suite; the hosted CI run; the new button in a browser (vitest only); a worker consuming the new `resume` and `sync_account` tasks from a real broker (the task bodies are called directly, the Celery singleton trap).

### Executed — M4.2 (2026-10-19)

- **`scripts/check-all.sh`, run after each of commits 2 – 5, all passing on the final code.** Final (commit 5): ruff lint and format, mypy, bandit, pip-audit, unit **1861 passed** (866 deselected), integration **866 passed** (against the Compose PostgreSQL on 5433 and Redis on 6380), frontend eslint / prettier / tsc / vitest **404 passed (30 files)**, build, npm audit, generated types, `.env.example`, alert rules and `promtool`, backend changed-line coverage **693/811 (85.5 %, floor 80 %)**, frontend **141/150 (94.0 %)**, the provider-branching gate, docs links. Earlier runs: commit 2 passed first time (720 integration); commit 3's first run failed **one** check, `contracts: api types`, because the regenerated `schema.ts` was not yet staged (the M4.1 trap again) — fixed, not a code defect. Issue 128 (the intermittent alerts-history test) did not appear in any of the four full integration runs.
- **New tests:** `tests/operations/` (the transition table: every ordered pair of the ten states checked against the 13 documented legal edges, terminal states never left, every state reachable, `VERIFYING` reported as and stored as `RUNNING`; deadline arithmetic and bounds; the evaluator matrix over environment × action × policy including missing, dangling and unknown; the closed codes against the contract text; the statement builder), `tests/integration/test_operation_schema.py` (each CHECK and index against PostgreSQL, restrict-on-delete, the guarded transition, **12 sessions racing one row — exactly one claim**, and a deterministic version where the second session blocks on the first's uncommitted claim), `tests/integration/test_operations_api.py` (79: the happy path, replay, concurrent duplicates, scope, permission, allowlist, validation, 423, 429, fail-closed rate limiting, every `503` / `422` reason, cancel, visibility, the in-flight guards), `tests/integration/test_operations_worker.py` (67), the route-coverage entry for §17, frontend `OperationActions.test.tsx` and `OperationDetail.test.tsx` (with axe).
- **The worker tests prove, against real PostgreSQL and a scriptable provider:** a restart to `SUCCEEDED` with `observed_state_after`; a provider that says done while the unit is wrong ⇒ `FAILED` / `VERIFICATION_MISMATCH` (four cases) and never `SUCCEEDED`; a verification read that is `stale`, `unavailable` or `not_configured` ⇒ `VERIFICATION_UNAVAILABLE`, flagged; a failed invoke is **never retried** (one attempt, for seven kinds of failure) and is flagged for reconciliation only when ambiguous; the provider's id is committed **before the first poll**; a deadline passing mid-poll ⇒ `TIMED_OUT`; **two workers racing one row ⇒ one claim, one invocation**; a duplicate delivery ⇒ nothing; **permission revoked while queued ⇒ refused with zero invocations** (and a narrowed scope, a role without the action, a disabled requester, a disabled account, a re-labelled server in both directions, a gone or unmanaged target, a moved service, a policy that no longer allows the action, no adapter, demonstration mode, a different adapter, an unsupported action, a host the provider reports as someone else, a unit not on the host); **an approval-required row without an approval ⇒ refused by the worker**; a worker killed mid-operation ⇒ the reaper ends it `TIMED_OUT`, flagged, handle kept, and a late redelivery loses the claim; an unclaimed row past its deadline ⇒ `EXPIRED_BEFORE_START` without a provider; the healer re-enqueues a lost message; a worker that lost the row to the reaper reports `superseded` and does not write over it; the task takes an id and nothing else, refuses a malformed id, and is routed and scheduled as designed.
- **Mutation checks, each seen failing named tests, files restored and diffed after each:** the code-side success guard removed (5 tests); the schema's `verified_success` CHECK removed (1); the guarded `WHERE` removed (6, including both race tests); the per-action permission check in the request path removed (2); the approval refusal removed (2); the replay lookup removed (5); in the worker — verification treated as always matching (the mismatch tests), the permission re-check removed (3), the approval re-check removed (2), the handle persisted after polling instead of before (4).
- **Not executed, and therefore not claimed:** `scripts/smoke-test.sh` (issue 144); the Playwright E2E suite; the hosted CI run; anything against a real AWS or Azure account (none exists); the UI against a running backend in a browser (the vitest tests stub `fetch`); a Celery worker consuming a real queue (the task body is called directly, the wrapper's shape is asserted); the worker under load (issue 138).

### Executed — M4.1 (2026-10-19)

- **`scripts/check-all.sh`, two runs on the branch, with `TEST_DATABASE_URL` and `TEST_REDIS_URL` pointing at the Compose PostgreSQL (port 5433, `dcp_test`) and Redis (port 6380).** *Run 1* (before the last fix): **2 checks failed** — `mypy` (six errors in `app/providers/service.py`, a `**dict` argument) and `contracts: api types` (the regenerated `schema.ts` was not yet staged, so its diff showed); everything else passed (unit 1677, integration 678, frontend 367). Both were fixed. *Run 2* (final code): **1 check failed — `backend: integration`, 677 passed and 1 failed, the failure being issue 128**; the others passed: ruff, ruff format, mypy (145 files), bandit, unit **1677 passed**, pip-audit, eslint, prettier, tsc, vitest **367 passed**, build, npm audit, `contracts: api types` (clean), `.env.example`, alert rule file, promtool check and test, backend changed-line coverage **358/367 (97.5 %, floor 80 %)**, frontend (no coverable change), **`providers: no branching above the adapter layer`** (5 reviewed files), docs links. The script therefore did **not** print ALL CHECKS PASSED on the final code.
- **New tests:** `tests/providers/` (taxonomy and mapping, the read/write retry table, value objects and the envelope, capabilities and the gate, the signature inspection and its own failure cases, the conformance harness against eight violating adapters, the demo adapter, the registry, the §7.2 matrix pin), `tests/test_provider_gate.py`, `tests/integration/test_cloud_accounts_api.py` (13: Administrator-only with the refusal audited, 404 for unknown and soft-deleted, 422 for an unknown parameter, the documented keys only, a stored credential reference absent from the response, no audit row for a read, and the demonstration, configured and not-configured states), and a unit test for the §26.1 row text.
- **Mutation checks, each seen failing a named test:** the capability gate removed from `execute_operation`; the `__init_subclass__` override guard removed; the demo adapter's `demo_mode` flag set false; the demo adapter's connectivity capability changed to `supported`; `demo_mode` dropped in §26.7; the §26.7 permission weakened to `server:read`; a stray `AWS_SSM` string planted in `health/router.py` (the gate named the file and line). Files restored and diffed after each.
- **The conformance harness rejects, each for its intended reason:** a raw `RuntimeError` leaking from a read, a result stamped for another adapter, a wrong `demo_mode`, a non-result return, a free-form `run(command: str)` method, an action declared supported but refused, a raw `KeyError` from status, and a handle stamped for another adapter.
- `scripts/smoke-test.sh` from empty volumes (2026-10-19, on the final code): **SMOKE TEST PASSED**, no "incomplete" marker, exit 0 -- every step through 20, including the new **step 17b**, which in the real stack asked §26.7 about a seeded server's account and got the demonstration adapter, `demo_mode` true, no `supported` capability and no mention of a credential. The hosted runner's smoke job has not been seen (the 18b fixture cannot start there, issue 69).

**Not executed, and therefore not claimed:** the hosted CI run (including its smoke job); anything against a real AWS or Azure account (none exists); §26.7 in a browser (there is no page); the cause of the alerts flake (issue 128).

### Executed — M3.4 (2026-10-19)

- **`scripts/check-all.sh` with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set, on the final code: ALL CHECKS PASSED, nothing skipped** (its first run on the same code failed `bandit` on an `assert` I had used for type narrowing; fixed and the whole script re-run): `ruff`, `ruff format`, `mypy` (135 files), `bandit`, `pip-audit`, `eslint`, `prettier`, `tsc`, the Vite build, `npm audit`, `contracts: api types`, `.env.example`, `alerts: rule file current`, `promtool check rules` (33 rules), `promtool test rules`, doc links 504/0. Unit **1522** (was 1353), integration **665** (real PostgreSQL 17.2 and Redis; 726 s; was 581), frontend **367** (was 325). Changed-line coverage **backend 781/869 (89.9 %)**, **frontend 147/154 (95.5 %)** against the 80 % floor.
- **`scripts/smoke-test.sh`, from empty volumes: SMOKE TEST PASSED, no "incomplete" marker** (4th run; the first three failed for the reasons in issue 125 and are listed there, not hidden). **Step 18h**, on 18g's real alert from a real Prometheus and Alertmanager, to the `notification-sink` mock: `notify_register` (both channels created; the API returned the reference and no URL; a pasted URL was `422` and not echoed; both integration rows `degraded: no_delivery_attempted_yet`, not `ok`); `notify_failures` (a sink answering `503` and SMTP `451`: **four attempts each, then `ABANDONED`**, `http_503` / `smtp_busy`; the sink saw exactly four posts with the fixed `dcp.alert.v1` shape, the registered `Host` and a link; both rows `unavailable` with no address); `notify_retry` (a redirect: **`FAILED` / `redirect_refused` after exactly one attempt**, not followed, not retried over 35 s; then a manual retry of the `FAILED` and of the `ABANDONED` delivery both `SENT`, one HTTP message and one mail with `Subject: [DCP] [CRITICAL] HostNotReporting (staging)`, a retry of a `SENT` delivery is `409`, both rows `ok`); `notify_quiet` (the source resolved the alert and **nothing more was sent for 50 s**). The SSRF lists, Docker bridge ranges, retry delays, SMTP settings and `DCP_PUBLIC_BASE_URL` reached the worker through Compose.
- **Mutation checks, each seen failing** — commit 1: the address check removed, redirects followed, empty routing treated as match-nothing, IPv4-mapped addresses not unwrapped. Commit 2: unreadable rules treated as a catch-all, disabled channels included. Commit 3: the lease removed, `SKIP LOCKED` removed (**the first, load-based test did not fail under that mutation, which is why a deterministic one now exists**), the channel's status not re-checked at send time, `record()` unguarded. Commit 4: retry ignoring the alert's scope, `SENT` made retryable, the write-time destination check removed, the reference written into the audit row.
- **Measured before relying on it (a throwaway script):** a literal-IP URL with the `Host` header and TLS `sni_hostname` verifies the certificate for the **name**; a wrong name and an IP absent from the certificate fail; a `302` is returned, not followed; and `smtplib` with `_host` set verifies the STARTTLS certificate for the name while connecting to another address (right name verifies, wrong name and an IP fail).
- **An adversarial test** shows a crafted alert (mentions, a link, a header-injection attempt in its summary) adds a delivery row for a **registered** channel only, changes no channel, and that the import pin still forbids `app.core.ssrf`, `app.core.secrets`, the senders and `smtplib` in the alert path.

**Not executed, and therefore not claimed:** the hosted CI run; delivery to a **real** Slack workspace, Teams tenant or mail provider (only the local sink was exercised); the channels page and the alert deliveries in a browser (no Playwright path); a webhook secret under real rotation; retention of deliveries against real time (the purge is tested by backdating `created_at`); the 1-hour `repeat_interval` and 3-hour expiry (unchanged from M3.3).

### Executed — M3.3 (2026-10-18)

Per-commit results are in [§9](#9-next-recommended-task); the final ones:

- **`scripts/smoke-test.sh`, from empty volumes (all profiles `down -v` first): SMOKE TEST PASSED, no "incomplete" marker**, so 18b (real systemd) and 18e (real journal) ran too, and **step 18g ran as part of one unbroken run** with all five modes passing (`pipeline`, `down`, `recovered`, `outage`, `restored`). **`scripts/check-all.sh` with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set, on the final code: ALL CHECKS PASSED, nothing skipped** — unit **1353** (was 1227), integration **581** (real PostgreSQL 17.2 and Redis; 529 s; was 493), frontend **325** (was 279; +46), `tsc`, `eslint`, `prettier`, the Vite build, `ruff`, `ruff format`, `mypy` (118 files), `bandit`, `pip-audit`, `npm audit`, `contracts: api types`, `.env.example`, `alerts: rule file current`, `promtool check rules` (33 rules), `promtool test rules`, doc links 496/0; changed-line coverage **backend 1009/1105 (91.3 %)**, **frontend 135/140 (96.4 %)** against the 80 % floor (the uncovered backend lines are defensive branches and Redis-failure paths; `scripts/gen_alert_rules.py` and `scripts/smoke_alerts.py` are outside the report, so the smoke test is their check).
- **Mutation checks, each seen failing:** `unless` replaced by `or` in a generated warning rule; the environment matcher dropped from a rule; `ON CONFLICT DO NOTHING` replaced by check-then-insert (caught only by the deterministic race test, not the load test); severity read from the payload; environment read from the payload; the alert scope removed (6 tests); unlinked alerts shown to everyone; the acknowledge status guard removed; on the frontend, a summary rendered through `dangerouslySetInnerHTML` (3 tests including the source pin) and the expired explanation reworded (1).
- **Found by running it against reality, not by reading:** the retry behaviour of a real Alertmanager for `4xx`, `429`, `503` and `max_alerts`; that the documented unique index cannot enforce one open row; that the Celery singleton configures itself from the first test to import it; that a stopped Alertmanager container reads as a timeout; that my integration tests broke two existing ones by leaving servers and services behind; that `unconstrained` had to know the administrator's full scope *names* all four environments.

**Not executed, and therefore not claimed:** the hosted CI run; the alert pages in a browser; a real notification receiver (M3.4); a webhook caller outside the allowlist in Compose; the 1-hour repeat and 3-hour expiry against real time; rules against a host under real load; the retention drop of either partitioned table (issue 107).

### Executed — M3.2 (2026-10-18)

Per-commit results are in [§9](#9-next-recommended-task); the final ones:

- **`scripts/smoke-test.sh`, from empty volumes (all profiles `down -v` first):** **SMOKE TEST PASSED, no "incomplete" marker** (2026-10-18), so 18b (real systemd) and 18e (real journal) both ran on this machine, and step 18f ran as part of one unbroken run: catalogue and retention agree (14 d); the integration page reports Loki `ok` and, with Loki stopped, `unavailable`; 50 sample lines carry the inventory hostname and the shipper is `receiving`; all five file sources searchable alone; 8 canaries absent from the API's answer with the marker present; **8 canaries pushed raw into Loki: none comes back**; the multi-line block does (pinned); a hostile `contains` matches literally; all 8 presets accepted and each exact; **10 lines with 4+3 sharing a nanosecond across two streams paged at sizes 1-9 both ways: no repeat, no gap**; an unknown server dropped and counted; a never-shipped server `silent`; LogQL-carrying fields and an unbounded query `422`; Loki stopped `503 provider_unavailable`; Loki back `200`; then step 19 (database outage) and step 20 (`down -v` and back to healthy). Three rate-limit waits (about 3 minutes) are part of the run.
- **`scripts/check-all.sh` with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set, on the final code: ALL CHECKS PASSED, nothing skipped** — unit **1227**, integration **493** (real PostgreSQL 17.2 and Redis; 375 s), frontend **279**, backend changed-line coverage **683/729 (93.7 %)**, frontend **170/181 (93.9 %)**. The `contracts: api types` check failed once on the way, correctly, on a docstring-driven schema change, and passed once the regenerated file was committed. **Without the integration variables the script skips that suite and says so; a run that skipped it is not a pass** (the first M3.2 run did exactly that).
- **Mutation checks, each seen failing:** `contains` escaping removed (7 tests); retention check removed (2); per-line scope re-check removed (1); tie-group completion removed (2 paging cases); the XSS pin and the ingestion notices on the frontend (2 and 4).
- **Found by running it against reality, not by reading:** the 1 000-entry Loki ceiling vs the API's up-to-5 000-line internal reads; stream order in a response; pydantic `pattern=` being a search; a session that outlived 18b–18e; the harness tripping the product's own rate limit.

**Not executed, and therefore not claimed:** the hosted CI run; the log page in a browser; the ingestion probe at fleet scale (issue 98); a real (non-sample) registered server's shipper (issue 97); §19.3 and deployment-to-logs (M5); the multi-line PEM gap closed (issue 92 stands).

### Executed — M3.1 (2026-10-17/18)

Per-commit results are in [§9](#9-next-recommended-task); the final ones:

- **`scripts/smoke-test.sh`, from empty volumes: SMOKE TEST PASSED** (2026-10-18), no "incomplete" marker. Step 18c: Loki push/read-back, eighth-label and old-sample rejections, label allowlist, retention 14 d. Step 18d: Alloy non-root / unpublished / unprivileged / no Docker socket; 13 severity mappings; hostile container name → `other`; 8 canaries absent with the marker in all 5 sources; the multi-line PEM pinned as a leak; four bad-identity Alloys, each drop counted, three with nothing stored and one with a positive control. Step 18e: a real systemd journal through `alloy-journal`.
- **`scripts/check-all.sh`: ALL CHECKS PASSED, all 18** (2026-10-18) — backend unit **886**, integration **397** (real PostgreSQL 17.2 and Redis; 541 s), frontend **227**. Backend changed-line coverage reports no coverable line changed; the new Python lives in `infra/` and `scripts/`, outside that gate, so its tests are the only check on it.
- **Mutation checks, each seen failing:** AWS stage removed from `config.alloy` (3 unit tests + smoke 18d naming `aws_access_key_id`); `Bearer` removed from `redact_text`; `stage.label_keep` removed (smoke 18d, by timeout — issue 96).
- **By hand, once:** Alloy against a real Docker daemon's `json-file` logs, as uid 473 (reads nothing), uid 473 + gid 0 (reads nothing), and uid 0 (works) — issue 95.

**Not executed, and therefore not claimed:** the hosted CI run; a registered server's logs (issue 94); any DCP code reading Loki; the Docker source in the smoke test; the ACL alternative to uid 0.

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
| Grafana Alloy | **Configured and verified (M3.1), against synthetic sources** | `infra/alloy/config.alloy` + `journal.alloy`; shipped the sample producer's files and a real systemd journal into a real Loki (smoke 18d, 18e). **Not verified:** a registered *real* server (the sample identity is registered by `seed --sample-log-host`, nothing renders config per host — issues 94, 97); the Docker source against anything but a one-off manual run (issue 95); hosted CI |
| Grafana Loki | **Configured, verified, and queried by DCP (M3.1, M3.2)** | 14 d retention, seven-label backstop, ingestion limits, usage reporting off; read ceiling 5 000 entries (M3.2 — it was 1 000 and broke every continuation page); smoke 18c. **`app/logs/client.py` queries it** (read-only, deadline, one connect retry, circuit breaker) for §19.1, §19.2 and §16.7; smoke 18f runs the API against it for real, including with Loki stopped. Single tenant, so nothing authenticates which shipper claims which `server_id` (`OPEN-31`, accepted for v1). `grafana` in the same profile is still a placeholder, which is why the smoke test starts Loki by name |
| Alertmanager | **Configured, verified end to end, and read by DCP (M3.3)** | prom/alertmanager 0.27.0 in the `observability` profile, **unpublished**, `infra/alertmanager/alertmanager.yml`: one receiver (DCP's webhook, `send_resolved`, `max_alerts: 100`), `group_by [alertname, server_id]`, `repeat_interval` 1 h, **no external receivers and no inhibit rules**; the shared secret is written from `DCP_ALERT_WEBHOOK_SECRET` at container start. Prometheus loads the **generated** `infra/prometheus/rules/dcp-alerts.yml` (33 rules) and sends to it. Smoke 18g drove the real chain: the watchdog, a stopped exporter becoming an alert row, a recovery, an outage. **Not verified:** a real notification receiver (M3.4 added only a local mock sink); the hosted-runner run; a production-grade secret and address allowlist (issue 105) |
| Grafana | **Declared, never started** | In the `observability` profile; no dashboards and no provisioned data source. DCP renders its own charts (M2.2), so this is for ad-hoc exploration only |
| Operation executor | **Built (M4.2), resumable (M4.3); exercised against a test fake and against the AWS adapter over a stubbed AWS layer** | `app/operations/execute.py`, tasks `dcp.operations.execute`, `dcp.operations.resume` (queue `operations`, id only) and `dcp.operations.reap` (queue `maintenance`, every 30 s). Re-authorises, claims, invokes once, persists the handle, heartbeats while it polls, verifies by a read, and reaps; a quiet row with a handle is resumed by exactly one worker and never re-sent. It has never called a real provider and its timings are working defaults. No worker has consumed it from a real queue in a test |
| Audit trail, history and export | **Built (M4.6); exercised against the test database and stubbed providers** | `app/audit/` (read, query, exports), `app/operations/history*.py`, `audit_exports` (migration `0013`), task `dcp.audit.export` and the 60 s sweep `dcp.audit.sweep_exports` on `maintenance`. Reads are scoped in SQL and audited as `AUDIT_QUERIED`; the export is bounded, re-authorised in the worker, and downloadable once. **No independent export destination exists** (`OPEN-15`, M6.2) and the application role still owns `audit_events` (issue 179). The pages have not been seen in a browser (issue 185) |
| Approval workflow | **Built (M4.5); exercised against test providers and a fake IdP** | `app/approvals/` (the pure rules, the guarded statements, the request-side, decision and queue services, the sweep, the pending-approval alert, the policy editor). A gated request waits `AWAITING_APPROVAL`; a different person in scope and in the policy's role decides it in one guarded transaction; an unused window expires to `CANCELLED`; the worker re-checks an `approvals` row and the approver's standing before any provider call. The two people in the tests are two logins of a fake OIDC provider. Nothing about it has been seen against a real provider or a real identity provider |
| Provider adapter layer | **Interface (M4.1, amended three times in M4.3), a demonstration adapter, and the AWS SSM adapter** | `app/providers/`: the ABC, taxonomy, capability sets and registry, whose factory map now holds exactly `AWS`. In demonstration mode every provider is the demonstration adapter. The AWS factory refuses (`NotConfigured`) when a static access key is in the environment or `DCP_AWS_SSM_DOCUMENT_VERSION` is unset. Azure, `ON_PREM` and `OTHER` have no adapter |
| AWS Systems Manager | **Adapter built (M4.3); run against one sandbox AWS account on 2026-10-22 (manual check)** | `app/providers/aws/` + `infra/aws/` (document, execution-role and trust policies, runbook). The automated tests prove DCP's behaviour against a botocore `Stubber` and a local HTTP server only. The manual check ("Executed — manual AWS check") ran `status`, `restart`, an approval-gated production restart and an expiry against real AWS in one sandbox account, from a laptop sign-in assumed into the runtime role. Not covered: production, a second account, an ambient instance or task role (issue 171), `start` / `stop`, any failure path. §26.7 reaches *verified* for that one account on that one day, not in general |
| Azure management APIs | **Not configured, and M4.4 is deferred** (`OPEN-92`) | No subscription, no managed identity, no Run Command script, and none is planned: the owner has no Azure estate |
| Jenkins | **Not configured** | No instance, no token, no registered target. Mock planned M5.1 |
| Secrets manager | **Not configured; a development `env://` backend exists (M3.4); AWS needs none (M4.3)** | `app/core/secrets.py` resolves `env://NAME` from the process variable `DCPSECRET_NAME`. That is **not** a production secret store (`OPEN-06` narrowed, still open; issues 113, 153) |
| Notification channels | **Implemented and verified against a local MOCK sink only (M3.4)** | DB-managed channels (`notification_channels`), delivery on the `notifications` queue with bounded retry, HTTPS and SMTP senders through the checked-address SSRF path. Smoke 18h drove the real alert chain into `infra/compose/notification-sink` (a visibly synthetic receiver in the `mocks` profile): failure, four attempts, abandonment, a permanent redirect failure, manual retry, and nothing sent on resolve. **Not verified against Slack, Teams or a real mail provider** (issue 114); **no channel is registered in any real deployment**; **M4.5's pending-approval alert is delivered through these same channels** (`OPEN-101`), so it has the same limit — with no channel registered an approver learns of a request only by opening the queue or the alert list (issue 163) |
| Restricted agent | **Not designed** | `OPEN-05` closed 2026-10-19: adopt, do not build; no agent adapter in Phase 4. M6.1 may revisit (issue 130) |

**No cloud resource has been provisioned. No production account has been contacted.
No credential has been created.** Every value in `.env.example` is a placeholder,
including `DCP_OIDC_CLIENT_SECRET`, whose value is literally
`placeholder-client-secret` and is shared with the local mock IdP. The only
non-placeholder connection attempt made during M1.1 was a local psycopg probe
against `127.0.0.1:5432` using placeholder credentials, which failed authentication
as expected (see issue 23).

---

## 9. Next recommended task

### M4.6 — COMPLETE 2026-10-23 (seven commits on `phase-M4.6`, cut from `main` at `e56ed1b`); **merged to `main` as PR #33 (`061a94b`), and green on hosted CI per the owner** (the run was not read from the development machine). The smoke test and Playwright were not run locally; the new pages were not seen in a browser.

**Next, in this order:**

1. **Look at the pages in a real browser** against the running stack (issue 185): the Operations list with its filters and paging, the Audit log with a detail panel open, the export dialog through one request, a build and the single download, and the dashboard's operations list. This is the one piece of M4.6 whose evidence is the weakest.
2. **Done 2026-10-23: pushed, merged (PR #33) and reported green on hosted CI by the owner.** The `e2e` job runs Playwright and the Compose smoke test; neither covers the new pages, so a green run says the rest of the product still works, not that these pages do.
3. **Consider one smoke step** (needs the owner's agreement to `down -v`, issue 144): with the mock IdP's two users, an administrator reads the trail and the portal wrote `AUDIT_QUERIED`; an export is requested, built by the real worker and downloaded once, the second download `409`.
4. **M5.1 — deployment targets and the Jenkins client** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m51--deployment-targets-and-jenkins-client)). Phase 5 prerequisites: a Jenkins instance (mock locally) and `OPEN-18` decided. M5.4 reuses the approvals table and rules, and the history page gains a deployments half.
5. **M6.2 owns the rest of audit protection** (issue 179): a least-privilege application role, the independent export destination (`OPEN-15`), and the retention mechanism.

**What M4.6 should not be re-litigated for:** the stored environment decides who sees an operation, in the list and the detail alike (`OPEN-107`); an audit event is scoped by its environment alone and one with none needs a grant covering every environment (`OPEN-109`); every read of the trail is audited, committed before the page is returned (`OPEN-110`); the export lives in `audit_exports`, is refused rather than truncated, re-authorises in the worker and leaves exactly once, audited at the download (`OPEN-112`); "immutable" means refused by triggers for the role the application uses, and says no more (`OPEN-113`); every state-changing route has a declared audit behaviour or the build fails (`OPEN-114`).

**Traps this milestone paid for:** **read the matcher before translating it** — the agreed rule for events with no environment was wrong because the bootstrap Administrator's scope names its environments; **an `UPDATE … RETURNING` returns the new row**, so a single-retrieval download needs a locked sub-select to hand back the old bytes; **a claim in a document can be stronger than the system** — partition detach was listed as refused and is not; **a test that plants a secret must know which fields are meant to store text**; **a window that ends at "now" compares two clocks**; **audit rows are never deleted and the test database is shared**, so every assertion about which events come back needs its own slice of time; **tests that commit rows must remove what other suites count**; and **never edit source with a script that uses the platform's default encoding** on Windows (read and write bytes as UTF-8 with `newline=''`).

**Not M4.6:** audit retention and `DROP PARTITION`, the independent export store and the non-superuser role (M6.2), hash-chained records, any export format but `jsonl`, free-text search of metadata, a total count, deployment history (M5), a smoke step, the Playwright suite.

### M4.5 — COMPLETE 2026-10-20 (six commits on `phase-M4.5`, cut from `main` at `dbe61a7`); **pushed and merged (PRs 28 and 29), and green on hosted CI on 2026-10-22** after three CI-only fixes merged as PR 32 (the `e2e` runner pinned to `ubuntu-24.04`, `certifi` 2026.10.19, smoke step 12 waiting for beat; issues 175 – 177). All eleven jobs passed on the PR run, the Compose smoke test and Playwright among them. **Run once against a sandbox AWS account on 2026-10-22 (manual check, "Executed — manual AWS check"); the automated tests still use stubbed providers only.**

**Next, in this order:**

1. **Done 2026-10-22: the owner's manual check** (issue 146), including M4.5's production approval and expiry. See "Executed — manual AWS check". What remains of it: the ambient-role path (issue 171), the owner's console confirmation that the sandbox account is clean (issue 173), and everything under "Not covered" there.
2. **Done 2026-10-22: the development database was migrated** during that check (no revision applied before, `0012` now).
3. **Done 2026-10-22: pushed, merged and read on CI.** The first hosted runs failed for reasons unrelated to M4.5's code (issues 175 – 177); fixed in PR 32, and the jobs are green.
4. **The smoke script has now run green on a hosted runner; running it locally still needs the owner to agree to its `down -v`** (issue 144). A candidate step to add: with the mock IdP's two users, a gated request is `202 AWAITING_APPROVAL`, an approval by a different user is `200`, and a self-approval is `403`.
5. **M4.6 — audit API and operations history** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m46--audit-api-and-operations-history)): endpoints 23.1 – 23.2 and 24.1 – 24.3. The dashboard's `recent_operations` section stays `not_configured` until then (issue 143). M4.5 gave it `approval_id` on audit rows and `EXPIRED` / `REJECTED` outcomes to show.
6. **Extend the editor if it matters:** a required reason on an edit that weakens a gate (issue 162).

**What M4.5 should not be re-litigated for:** one approval only, and a policy needing more is refused rather than treated as one (`OPEN-95`); a waiting request holds the target's slot and `deadline_at` is its window (`OPEN-96`); the approval row is the arbiter of every race and a decision is one transaction (`OPEN-97`); self-approval is one tested function and the default is no (`OPEN-98`); the worker reads evidence, not a flag (`OPEN-99`); expiry cancels and frees the slot (`OPEN-100`); approvers are told by an alert, not a new channel (`OPEN-101`); the editor is in, and it is a privilege (`OPEN-105`).

**Traps this milestone paid for:** **the API's own pre-check can hide a missing storage guard** — the first mutation that removed `WHERE decision = 'PENDING'` passed every API test, because the endpoint checks the state before it writes, and only statements run against the row directly could show the guard mattered; **`now()` is the transaction's start in PostgreSQL**, so a window computed in Python and compared with a column default is off by the request's own duration — the approval row takes the operation's `created_at`; **a refusal that is a closed code is easy to mislabel** — the contract's `self_approval_not_permitted` and the plan's `self_approval_denied` named one thing; **a test helper must not be more lenient than the schema** — the `PENDING` row with a decision time was refused by the CHECK; **SQLAlchemy 2.1 changed `Select`'s type parameters**; **never edit the source tree while a mutation run is using it** (the run was left alone until it had restored every file).

**Plan as proposed, and what changed in the doing:** [`docs/M4.5_PLAN.md`](docs/M4.5_PLAN.md) holds the plan and its "what differed" list (thirteen items). The most important: the policy editor is in the milestone (1), the self-approval refusal's name (2), the bypass rule is slightly broader than D7 (3), and the pre-existing visibility inconsistency found in M4.2 (12).

**Not M4.5:** deployments' approvals (M5.4); more than one approval; per-approver notification, delegation, out-of-office, approving from the alert page, a bulk decision; the history and audit APIs (M4.6); Azure (deferred); any smoke step; the Playwright suite.

### M4.3 — COMPLETE LOCALLY 2026-10-20 (seven commits on `phase-M4.3`, cut from `main` at `1d6481b`). Not pushed, not on hosted CI; the smoke test was not run; **never run against AWS.**

**Next, in this order:**

1. **The owner's manual check against a non-production AWS account** — the only evidence that M4.3 works, and the milestone's acceptance criterion ("a restart on a non-production test instance succeeds and is verified"). The checklist is [`infra/aws/README.md`](infra/aws/README.md): create the role from the committed policies, create the `DCP-ServiceControl` document, tag one instance `DcpManaged=true`, set `DCP_AWS_SSM_DOCUMENT_VERSION`, register the account (§26.3) and verify it (§26.5), request a restart from the UI, run `scripts/verify_aws_role.py` and `pytest -m real_integration --integration-profile=…`, and **record the date and every result here as a manual check**. Until then M4.3 is "built and stubbed". Expect the first real run to find something (issues 146, 147): the document's syntax, the agent version, and the `GetCommandInvocation` fields are from documentation.
2. **Push `phase-M4.3`, open the PR and read CI.** `gh` is not installed on this machine. Unmeasured on a hosted runner: the whole suite (integration took 19 minutes here) and the smoke job.
3. **Run `scripts/smoke-test.sh` once the owner agrees to its `down -v`** (issue 144). M4.3 added no smoke step; a candidate is "register an AWS account with an unconfigured adapter and see `503 not_configured`".
4. **M4.5 — approvals** ([`docs/M4.5_PLAN.md`](docs/M4.5_PLAN.md), planned 2026-10-20, not started; **M4.4 — Azure is deferred** by the owner, `OPEN-92`, issue 159). M4.5 removes the `approval_workflow_unavailable` refusal (issue 135): until it lands, a production `stop`, or a service whose policy links the seeded production approval policy, cannot be requested even with AWS working.
5. **A cloud-accounts admin page** (issue 151) and a sync button, when someone wants to stop using the API for them.

**What M4.3 should not be re-litigated for:** the adapter takes an account's role ARN and no secret, and refuses to run with a static key (`OPEN-79`); the three interface amendments (`OPEN-80`); the document is the read/write boundary and takes its parameters as environment data (`OPEN-81`); the write client cannot retry and only `Throttled` is retried, three times, inside the invoke (`OPEN-82`); `logs_tail` is `not_configured`, not `unsupported` (`OPEN-83`); resume polls from a persisted handle and never sends (`OPEN-84`); verification stays a provider read (`OPEN-85`); connectivity changes nothing about the server (`OPEN-86`); the shell-document deny is verified offline (`OPEN-87`); sync reconciles and never discovers (`OPEN-88`); a failed operation alerts and a success on the target resolves it (`OPEN-89`); `OPEN-23` is closed with the owner as the sandbox owner (`OPEN-90`).

**Traps this milestone paid for:** **a documented interface was not enough** — the plan assumed a handle could be polled alone, and `GetCommandInvocation` also needs the instance, region and role; **"no retries" is easy to write and hard to be sure of** — botocore's `max_attempts` counts *retries*, and only a real HTTP server that counted requests could show the write client sends once; **a runtime check can be the very thing it guards against** — proving the role denies `AWS-RunShellScript` by sending one would run a shell if the deny were missing; **an environment variable that is empty is not set** — `AWS_ACCESS_KEY_ID=""` must not count as a key, and a test pins it; **the shell tool corrupts text twice over** — a heredoc rewrote `\\n` inside Python strings, and `Path.write_text` without an encoding rewrote a UTF-8 file as CP-1252 and CRLF (caught by ruff, repaired, re-checked as LF); **bandit is still not ruff** (an `assert` for type narrowing, again); **a test double must be shown to be able to fail** — the SSM script tests were mutated before being trusted; **`pytest -q` twice hides the summary line** (again); **each Bash call is a fresh shell**, so the `TEST_*` variables must be exported in the same command (a run that skipped 86 integration tests looked like a pass until the count was read); **a fixture that registers things must remove them** — the failure alert left rows that tests counting alerts would trip on.

**Plan as proposed, and what changed in the doing:** [`docs/M4.3_PLAN.md`](docs/M4.3_PLAN.md) holds the plan and its "what differed" list (twelve items). The most important: the third interface change (1), the 120 s resume threshold (2), sync filling rather than overwriting (3), the new `RESUME_REFUSED` code (5), and the credential cache covering an operation's own session (8).

**Not M4.3:** Azure; the approval workflow and `approvals`; the history and audit APIs (M4.6); a restricted agent and `mi-…` hosts; a cloud-accounts page; `ListCommands` reconciliation of a handle-less row; `ExternalId`; a Secrets Manager backend; `max_concurrent_operations` above 1; a retry trigger; server-level operations; any smoke step.

### M4.2 — COMPLETE LOCALLY 2026-10-19 (six commits on `phase-M4.2`, cut from `main` at `75c6417`). Not pushed, not on hosted CI; the smoke test was not run.

**Next, in this order:**

1. **Push `phase-M4.2`, open the PR and read CI.** `gh` is not installed on this machine, so the hosted result must be read from the PR's check summary. Unmeasured on a hosted runner: the whole suite (the integration phase took about ten minutes here) and the smoke job.
2. **Run `scripts/smoke-test.sh` once the owner agrees to its `down -v`** (issue 144), and consider adding the step that asks §17.1 about a registered demo service and expects `503` `demonstration_mode` with no row.
3. **M4.3 — AWS SSM adapter** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m43--aws-ssm-adapter)). It needs the prerequisites the plan names (a non-production account with a scoped role and a reviewed SSM document), and it is where five open items of this milestone become real: the first operation that can actually run (issue 134), resume from the handle (136), the operation-failure alert (137), an independent verification read (141) and §14.8 / §26.2 – §26.6. Register the factory in `providers/registry.py`; `check_provider_branching.py` already allows it.
4. **M4.5 — approvals** removes the `approval_workflow_unavailable` refusal (issue 135) and builds 22.1 – 22.4 and the `approvals` table.

**What M4.2 should not be re-litigated for:** endpoints 22.x are M4.5's (`OPEN-67`); an operation needing approval is refused, not queued (`OPEN-68`); `VERIFYING` is never stored and the reaper alone writes `PENDING|APPROVED -> TIMED_OUT` (`OPEN-69`); one task, no resume, a reaper and a healer on `maintenance` (`OPEN-70`); no state-changing call is retried and the worker re-checks everything after the claim (`OPEN-71`); verification is a provider read and `SUCCEEDED` is refused by the code and by the schema (`OPEN-72`); the idempotency lookup comes before the rate limit and the in-flight guard, and the database decides races (`OPEN-73`); `sanitized_result` holds closed fields only (`OPEN-74`); permission is checked before configuration is revealed (`OPEN-75`); worker audit rows are attributed to the requester (`OPEN-76`); `parent_operation_id` has no trigger (`OPEN-77`).

**Traps this milestone paid for:** **a task list asked for endpoints that read a table that did not exist** (22.x), found by reading both M4.2 and M4.5 before coding; **two documents disagreed about a crash** (resume versus time out); **the first concurrent-duplicate test failed one run in four** and the failure was a real ordering bug in the request path, not a flaky test; **a rolled-back async session expires every loaded row**, so the actor and the target are snapshotted into plain values before anything that can roll back; **a SQL parameter used twice with different type contexts is rejected by psycopg** (`:s` as a column value and in an `IN` list); **`findByRole('status')` matched the page's loading placeholder** before its content, so the tests wait for the text; **a hook called before an early return broke every existing page test** that rendered the panel outside a session provider; **`-q` twice suppresses the pytest summary line**, so a grep for "passed" found nothing and looked like silence; **the generated `schema.ts` must be staged** before `check-all` compares it; **the shell tool chokes on apostrophes in heredocs**, so long edits went through files written with the Write tool.

**Plan as proposed, and what changed in the doing:** [`docs/M4.2_PLAN.md`](docs/M4.2_PLAN.md) holds the plan and its "what differed" list (ten items). The most important: the in-flight / replay race (item 7), a failed pre-flight read being `TARGET_VALIDATION_FAILED` rather than a provider code (8), and the guarded-update builder landing in commit 2.

**Not M4.2:** a real provider; the approval workflow and `approvals`; resume from the handle; the operation-failure alert producer; a retry trigger; the history and audit APIs (M4.6); `max_concurrent_operations` above 1; server-level operations; smoke step 17c.

### M4.1 — COMPLETE LOCALLY 2026-10-19 (five commits on `phase-M4.1`, cut from `main` at `1571c2e`). Not pushed, not on hosted CI; smoke passed locally.

**Next, in this order:**

1. **Push `phase-M4.1`, open the PR and read CI.** The local smoke test passed (issue 129), so what is unmeasured is the hosted runner. Read the actual check results: `gh` is not installed on this machine.
2. **Look at issue 128** (the intermittent alerts-history test) before it costs someone a CI re-run; the likely fix is making `_history` ordering stable for ties and near-ties, or having the test seed its timestamps from the database clock.
3. **M4.2 — operation lifecycle and the state machine** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m42--operation-lifecycle-and-the-state-machine)). It runs against `tests/providers/scripted.py`, which is a test fake and not an integration; the "full operation lifecycle" the M4.1 task list mentions is its test, not M4.1's (`OPEN-63`). Needs a migration for `operations` and the one-in-flight index.

**What M4.1 should not be re-litigated for:** the signature has no free-form parameter and `execute_operation` is final on the base class (`OPEN-58`); a result keeps its own `demo_mode` and never carries a value for an unknown (`OPEN-59`); `NotFound` is a `502` and `Throttled` a `503` (`OPEN-60`); `unsupported` and `not_configured` are different answers (`OPEN-61`); an unregistered provider fails closed and the factory map ships empty (`OPEN-62`); the demo adapter never asserts a fact about a host (`OPEN-63`); §26.7 is `200` with `capabilities: null` when nothing serves the account (`OPEN-64`); the provider tables in `inventory/schemas.py` stay and are allowlisted (`OPEN-65`, issue 127).

**Traps this milestone paid for:** **two contracts disagreed about the same adapter** (§7.2 "Always ok" against §14.8 `503 not_configured`) and the milestone's own task list asked for a lifecycle test its own acceptance criterion forbids — both found by reading before coding; **the integration environment has demonstration mode on**, so a test of the non-demo path must set `DCP_DEMO_MODE=false` itself; **a harness nothing can fail proves nothing**, so eight violating adapters were written and each seen to fail for its stated reason; **an `__init_subclass__` guard stops a violating subclass from being defined at all**, so a violator written to test it broke the whole test module's import until it was removed; **`check-all` run 1 found two failures that were mine** (a `**dict` into a typed model, and an unstaged generated file) and run 2 found one that was not; **git warns that CRLF will be replaced by LF** on several files, so a checkout that converts them may show whole-file churn.

**Plan as proposed, and what changed in the doing:** [`docs/M4.1_PLAN.md`](docs/M4.1_PLAN.md) holds the plan and its "what differed" list. The allowlist is five files, not seven; `adapter_state` gained `configured`; `ProviderAdapter` gained a `demo` class flag; a `ScriptedAdapter` and a conformance harness were built in `tests/`; smoke step 17b was added.

**Not M4.1:** `AgentAdapter`; §14.8 (M4.3); §26.2 – §26.6 and any account page; per-account §26.1 rows (they arrive with §26.5); any real provider, credential, secret backend, timeout, circuit breaker or retry executor; the capability-disagreement assertions (M4.4); hybrid-activation hosts.

### M3.4 — COMPLETE LOCALLY 2026-10-19 (nine commits on `phase-M3.4`, cut from `main` after PR #21). Not pushed, not on hosted CI.

**Next, in this order:**

1. **Hosted CI for PR #22 (M3.4), read from the PR's check summary on 2026-10-19: 10 checks passed and 1 failed** — `Build and scan images (frontend, production)`, which is Trivy's HIGH/CRITICAL gate, not a test: two fixable HIGH CVEs in the Alpine layer of `nginx:1.30.6-alpine` (`libexpat` 2.8.5-r0 → 2.9.0-r0, `libx11` 1.8.13-r0 → 1.8.14-r0). The same two findings reproduced locally against a locally built image, and `apk upgrade --no-cache` in the production stage cleared them (Trivy: 0 vulnerabilities); that fix was merged as PR #23 (issue 126), **after which the owner reported the hosted CI pipeline green** (reported to me, not read from the run: the individual check names and timings were not seen). The names of the ten passing checks were not all visible, so no claim is made about which; the Compose smoke job's hosted result in particular was **not read by me**, so the only hosted-CI claim here is the owner's report that the pipeline is green on `main` after PR #23. What was unmeasured on a hosted runner: smoke 18h adds a sink, four short retry cycles and two manual retries (about 5 minutes on this machine) to a smoke test that already outgrows the hosted runner's patience. `gh` is not installed on this machine, so the hosted result of PR #21 was never read either.
2. **Phase 4 — M4.1, the provider adapter interface and the demo adapter** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m41--provider-adapter-interface-and-the-demo-adapter)). The AWS / Azure secret backends (`OPEN-06`) arrive with M4.3 / M4.4 behind the `resolve()` M3.4 built.
3. **Before a real rollout:** issues 113 – 125 above (a production secret store, a real-provider check of the Slack, Teams and mail shapes, resolved notices if wanted, the 180-day and 400-day partition drops from issue 107) and the standing 97, 98, 100, 102 – 112.

**What M3.4 should not be re-litigated for:** the destination is a reference and never a value (`OPEN-13`, `OPEN-48`); the request-time half of §19 — resolve once, check every address, connect to the checked one, no redirects (`OPEN-49`); the outbox, not an enqueue, drives a notification, so alert handling stays inert (`OPEN-50`); a new alert row notifies, nothing else does (`OPEN-51`); routing is the stored row's severity, environment and target type, and an unlinked alert reaches only unrestricted channels (`OPEN-52`); `FAILED` is permanent, `ABANDONED` is a spent budget, the claim is a lease and delivery is at-least-once (`OPEN-53`); reason codes are closed, never a URL (`OPEN-53`, `OPEN-55`).

**Traps this milestone paid for:** **a documented constraint can be impossible, again** — a `DCP_SECRET_*` variable would stop the process booting (an unknown `DCP_*` variable is a startup error), found by reading `config.py`; **`$` in a Python regex matches before a trailing newline** (`fullmatch`); **a mutation that does not fail a test is a missing test** — removing `SKIP LOCKED` passed the load-based concurrency test until a deterministic one was written; **`smtplib` reports a 4xx recipient refusal as an exception that looks like a permanent one** — found only because a smoke run timed out, and only *understood* because the harness was changed to say what the rows were; **a harness that selects "the alert" must select the one it made** (step 18a had left an older resolved one); **bandit is not ruff** (`assert` for narrowing passes ruff's `S101` noqa and fails bandit); **the editing tools and the shell tool both choke on apostrophes in heredocs** — long edits went through files written with the Write tool; **an import of a pytest fixture under another name does not register it**; **`UPDATE ... SET x = CASE WHEN :s = ...` needs the same parameter typed twice** in psycopg.

**Plan as proposed, and what changed in the doing:** [`docs/M3.4_PLAN.md`](docs/M3.4_PLAN.md) holds the plan as proposed and its "what differed" list (the `DCPSECRET_` prefix; no "critical first" ordering; the model-registry tests missed in commit 2; smoke 18h riding on 18g's stop; the pre-existing test-ordering trap). Six planned commits became nine: commit 6 (documents and PROGRESS) is the sixth, and three small fixes the smoke and `check-all` runs found (issue 125) were committed on their own.

**Not M3.4:** resolved / acknowledged / expired notices; a send-test route; channel delete; AWS and Azure secret backends; webhook signing and secret rotation; per-channel rate limits or digests; escalation, quiet hours and per-user email; the 180-day and 400-day partition drops; producers for the three alert conditions with no source (M4, M5).

### M3.3 — COMPLETE 2026-10-18 (all six commits on `phase-M3.3`, cut from `main` after PR #20); **merged to `main` as PR #21** (corrected 2026-10-19). The hosted-CI result of that PR was not read from the development machine (`gh` is not installed there), so it is **not claimed here**.

**Next, in this order:**

1. **Push `phase-M3.3`, open the PR and watch CI.** Unmeasured on a hosted runner: smoke 18g adds a second stop/start of the exporter and a stop/start of Alertmanager (about 6 minutes on this machine) on top of a smoke test that already outgrows the hosted runner's patience; the new `alert-rules` job runs `gen_alert_rules.py --check` and `promtool check rules` / `test rules`; the `contracts` job regenerates `schema.ts`.
2. **M3.4 — notifications** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m34--notifications)): `notification_channels` and `notification_deliveries`, delivery on the `notifications` queue, retry. Alertmanager has **no external receivers by design** (`OPEN-47`), so notification is DCP's own and is driven from the alert rows this milestone writes.
3. **Issues to take before a real rollout:** 102 – 112 below (retention drop, the placeholder webhook secret and private-range CIDR defaults, an assignee picker), and the standing 97 (per-server Alloy configuration), 98, 100.

**What M3.4 should not re-litigate:** where rules live and how they are made (`OPEN-03`, `OPEN-46`: generated, committed, current-checked); severity is part of the rule name and one condition is never two open rows (`OPEN-41`); the arbiter table for the open-row invariant (`OPEN-42`); the webhook's trust rules (`OPEN-43`); that **only the source resolves** and an unconfirmed alert is `EXPIRED`, not resolved (`OPEN-44`); scope through the server an alert is about, and unlinked alerts visible only to an unconstrained grant (`OPEN-45`); that a `0` is only good news if the watchdog confirms the pipeline (`OPEN-47`).

**Traps this milestone paid for:** a **stand-in is not evidence** — what a real Alertmanager sends and *does when refused* (a `4xx` or `429` is not retried in one attempt but the group is re-sent every `group_interval` until accepted; `max_alerts` silently drops the excess) was **measured in commit 1 before any design depended on it**, and the first real run of 18g found a reason code the integration suite could not (a stopped container reads as a *timeout*, not a refused connection); **a documented constraint can be impossible** — DATABASE.md §9.1's "unique open row" index on a partitioned table cannot enforce what it says (PostgreSQL needs the partition key in the index), found by reading before building; **the Celery app is a module-level singleton configured by whichever test imports it first** — a test that imported the task module under the unit-test placeholder Redis made every later server registration spend ~65 s retrying its enqueue (a full run timed out; the task tests now set the broker first); **tests that register servers must take them away** — the schema is rebuilt once per session and a log search by environment refuses past 200 servers, a dashboard count of unobserved units counts every service row (`retire_servers`); **audit rows are not ordered by insertion** (`occurred_at` is the transaction's `now()`, issue 51) so a test finds *its* row by content; `check-all.sh` still skips the integration suite when `TEST_DATABASE_URL` is unset and exits 0; each Bash call is a fresh shell (the database variables must be exported in the same command); the editing tools rewrite backslashes — long shell and YAML edits went through files written with the Write tool and patched by line.

**Plan as proposed, and what changed in the doing:**

1. **Measure, then the pure parts — done.** A throwaway real Alertmanager 0.27 and a recording receiver, then `DCP_UTILISATION_THRESHOLDS` (decided in M3.1 and **never built**; the metric readings now use the same per-environment lines as the alert rules, so a card and an alert cannot disagree), `scripts/gen_alert_rules.py` and the committed `infra/prometheus/rules/dcp-alerts.yml` (33 rules: five conditions × four environments, plus the watchdog), `check-all.sh` failing on a stale file and running `promtool check rules` and `test rules`, and `app/alerts/` pure code (catalogue, payload models, lifecycle table, `dedup_key`).
2. **Schema and ingestion — done.** Migration 0008 (`alert_events` partitioned monthly with CHECKs including *`resolved_at` is non-null exactly when `status = RESOLVED`*, and `alert_open_keys`); the webhook; per-alert transactions; refusal counters and watchdog liveness in Redis; the `EXPIRED` task; the partition-runway monitor raising a `DCP_SYSTEM` alert; `db/partitions.py` and `alembic/env.py` generalised to both tables.
3. **Read and lifecycle API — done**, with `active_alerts`, per-row counts, the server page's `open_alerts` and §26.1's Alertmanager row made real. API types regenerated.
4. **Alertmanager in Compose and smoke 18g — done.**
5. **Frontend — done.**
6. **Documents and PROGRESS — done.**

**What differed from the plan, each for a reason found while building:** (1) `OPEN-41` said the failed-unit discriminator is `unit`; it is `name`, the exporter's own label (ARCHITECTURE.md corrected); (2) the plan said the dashboard section "reads real rows" — it also needed a **`pipeline` field**, because a `0` beside an unconfirmed Alertmanager is "cannot tell" and the section has no other way to say so; (3) the per-row `active_alert_count` and the server page's `open_alerts` were stubs the plan did not list and would have contradicted the real section; both are real now, `null` only for "you may not read alerts for this server"; (4) `unconstrained` (who sees an alert about no server) had to treat an `environment` list naming all four environments as unconstrained, because `["*"]` never reaches production (rule 4) so the administrator's full scope *names* it; (5) the smoke harness posts the **repeat** itself (Alertmanager re-notifies only every hour), labelled as such; (6) a source address outside the allowlist cannot be exercised in Compose (every peer is private) and is covered by the unit and integration suites instead; (7) six commits, not five, so the API could be smoke-tested before any UI was built on it; (8) a CI `alert-rules` job was added.

**Real results (2026-10-18):**

- **Commit 1:** `check-all.sh` — backend changed-line coverage 254/254; `promtool check rules` **33 rules**; `promtool test rules` **SUCCESS**. **Mutation checks, each seen failing:** the warning expression's `unless` replaced by `or` (the 95 % CPU case fires a warning), and the environment matcher dropped from a rule (a staging host fires another environment's rule). **Measured, not reasoned:** payload shape and the `Authorization: Bearer` header from `credentials_file`; `max_alerts: 2` against three alerts → two sent and `truncatedAlerts: 1`; `429` and `422` → 9 sends in 45 s at the 5 s `group_interval`, none retried within an attempt; `503` → 29 sends (retried with backoff and re-sent); a `resolved` payload carries `endsAt` as the resolution time.
- **Commit 2 (verified in isolation, with the commit-3 files stashed):** ruff, mypy, unit **1328**, and the alert, migration, partition and server integration suites **104 passed** after one test-data fix of mine. **Mutation checks, each seen failing:** `ON CONFLICT DO NOTHING` replaced by check-then-insert fails `test_a_second_firing_waits_for_the_first_to_commit_and_then_counts_as_a_repeat` — **and the first, load-based concurrency test did *not* fail under that mutation** (the race never happened), which is why the deterministic test exists; severity read from the payload and environment read from the payload each fail two tests.
- **Commit 3:** the first full run on that tree had **three failures, all mine**: bandit (`assert` in `status.py`), and two integration tests that *my* tests had broken by leaving servers and service rows behind (the 200-server cap on an environment log search; a count of unobserved units). `contracts: api types` also fails until `schema.ts` is staged, by design. **Mutation checks, each seen failing:** the alert scope removed (6 tests), unlinked alerts shown to everyone (1), the acknowledge status guard removed (1, the two-people race).
- **Commit 4 — the first real run of smoke 18g:** `pipeline`, `down` and `recovered` **passed against the real chain** (33 generated rules loaded and healthy; the watchdog arrived through Prometheus and Alertmanager, so the Alertmanager row went `ok` and the dashboard said `confirmed`; a missing and a wrong secret were 401 and counted; a stopped exporter became a real `HostNotReporting` row for the right server; a repeat was counted on the same row; acknowledging left it open; the restart resolved it with the history kept). `outage` then **failed**: with the Alertmanager container stopped the probe says `alertmanager_timeout`, not `alertmanager_unreachable`. Both are the outage, named by a closed reason; the harness now accepts either.
- **The final runs, on the final code:** **`scripts/smoke-test.sh`, from empty volumes (all profiles `down -v` first): SMOKE TEST PASSED, no "incomplete" marker**, so 18b (real systemd) and 18e (real journal) ran too, and **step 18g ran as part of one unbroken run** with all five modes passing (`pipeline`, `down`, `recovered`, `outage`, `restored`). **`scripts/check-all.sh` with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set, on the final code: ALL CHECKS PASSED, nothing skipped** — unit **1353** (was 1227), integration **581** (real PostgreSQL 17.2 and Redis; 529 s; was 493), frontend **325** (was 279; +46), `tsc`, `eslint`, `prettier`, the Vite build, `ruff`, `ruff format`, `mypy` (118 files), `bandit`, `pip-audit`, `npm audit`, `contracts: api types`, `.env.example`, `alerts: rule file current`, `promtool check rules` (33 rules), `promtool test rules`, doc links 496/0; changed-line coverage **backend 1009/1105 (91.3 %)**, **frontend 135/140 (96.4 %)** against the 80 % floor (the uncovered backend lines are defensive branches and Redis-failure paths; `scripts/gen_alert_rules.py` and `scripts/smoke_alerts.py` are outside the report, so the smoke test is their check).

**Not executed, and therefore not claimed:** the hosted CI run; the alert pages in a browser (no Playwright path for alerts); Alertmanager against a real notification receiver (none exists until M3.4); a webhook caller outside the allowlist in Compose; the 1-hour `repeat_interval` and the 3-hour expiry against real time (expiry is tested by backdating `last_seen_at`); rule behaviour on a host whose exporter reports real CPU, memory and disk load (the sample exporter measures a container, and the `promtool` tests use seeded series); a real fleet's cardinality.

**Not M3.3:** notification delivery and channel admin (M3.4); producers for a failed application health check, a failed deployment and an operation timeout (M5, M5, M4); in-app rule editing (`OPEN-03`); an assignee picker; alerts on the server and service pages; Alertmanager silences; webhook secret rotation; the 180-day and 400-day partition drops.

### M3.2 — COMPLETE LOCALLY 2026-10-18 (all five commits on `phase-M3.2`, cut from `main` after PR #19). Not pushed, not on hosted CI.

**Next, in this order:**

1. **Push `phase-M3.2`, open the PR and watch CI.** Unmeasured on a hosted runner: smoke 18f adds ~5 minutes (a real stack, three rate-limit waits, a Loki stop and start); the `contracts` job regenerates `schema.ts`.
2. **M3.3 — alert rules and lifecycle** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m33--alert-rules-and-lifecycle)). Its plan must decide how utilisation moves the `warning`/`critical` *health* state (left open by `OPEN-28`).
3. **Issues to take before a real rollout:** 97 (per-server Alloy configuration from inventory), 98 (measure the ingestion probe), 100 (a Playwright path for logs).

**What M3.3 should not re-litigate:** the log label contract and closed values (`AD-21`); server-side LogQL only (`AD-05`); `POST` only for log queries (`OPEN-20`); scope as a filter on servers *and source types*; an unavailable log store is never an empty page; the cursor design (`OPEN-36`).

**Traps this milestone paid for:** a stand-in proves what the code does with an answer, never that the real system accepts the question — run the real one before claiming (the 1 000-vs-5 000 ceiling); Loki returns streams in no promised order, so impose a total order and re-read the boundary tie group; **pydantic `pattern=` searches, it does not match** (use `fullmatch` in a validator); a smoke step 30+ minutes after login needs a fresh session; the product's own 30/min limit applies to the harness too; `check-all.sh` **skips the integration suite when `TEST_DATABASE_URL` is unset and still exits 0** (read the last lines for "1 SKIPPED"); Git Bash heredocs and the Edit tool both rewrite backslash escapes — write them through a script and check the bytes.

**Plan as proposed, and what changed in the doing:**

**Decisions closed before code, at the owner's instruction to take the recommended option** (the record is [ARCHITECTURE.md §19](docs/ARCHITECTURE.md), `OPEN-32` – `OPEN-38`): permission spelling renamed to the contract names by migration 0007; the `application_id` label is the application's **UUID** (DATABASE.md and the model said slug — they were wrong, the pipeline was right); a per-server ingestion state answers issue 93; a closed catalogue of eight pattern presets; signed `(timestamp, line hash)` cursors with at most two merged selectors; `DCP_LOG_RETENTION_DAYS`; navigation prefill in router state, with §19.3 and deployment-to-logs deferred to M5.

**Plan (five commits, the M3.1 shape):**

1. **Measure, then build the pure parts.** A spike against the real Loki first: `end` boundary and tie behaviour for backward paging, and that each preset and a hostile `contains` are accepted and match literally. Then `app/logs/`: query models (`extra="forbid"`, closed enums, bounds), the LogQL builder, the preset catalogue, the guard arithmetic, settings `DCP_LOKI_URL` / `DCP_LOKI_TIMEOUT_SECONDS` / `DCP_LOG_RETENTION_DAYS` (with `.env.example` and Compose passthrough, issue 54), and migration 0007. Unit tests for every filter combination, `contains` escaping and the injection suite.
2. **Client and query service.** `app/logs/client.py` (read-only, 15 s deadline, no redirects, bounded body, one retry on connect failure, the existing `CircuitBreaker` imported from `app/metrics/client.py` rather than copied). Scope resolved in SQL with `readable_servers` into a concrete server-id set (capped at 200, over the cap is a `422`, never a silent truncation); at most two selector groups; merge, cursor, ingestion state, outbound redaction. Endpoints §19.1 and §19.2, and §16.7 answered from the same engine (`journald`, the registered unit). Integration tests against real PostgreSQL and Redis with an in-process Loki double for the outage, timeout, circuit and scope paths.
3. **Smoke step 18f against the real pipeline.** Seed the sample identity as a registered server (issue 94), search through the API, and assert: labels and source shown, **raw canaries pushed straight into Loki never appear in a response and the marker does**, an out-of-scope server is dropped and counted, Loki stopped gives `503 provider_unavailable` and not an empty page, a stopped shipper gives `silent` and not "no logs".
4. **Frontend.** Regenerated API types; a `/logs` page (filters, result list, source and timestamp, "load older"); lines rendered as text nodes only; an unavailable state, and an ingestion-aware empty state; "View logs" from a service, prefilled through router state. Component tests including markup in a log line and the unavailable and silent states.
5. **Docs and PROGRESS** with real results; SECURITY §17 gains the outbound pass; DATABASE.md §4.3 and the `Application` comments corrected; API_CONTRACTS §19 amended for `ingestion` and the permission names.

**Commit 1 (measure, then the pure parts) — done 2026-10-18.** `app/logs/` holds the request model (`schemas.py`, `extra="forbid"`, closed enums), the eight-preset catalogue (`presets.py`), the bound guards (`guards.py`: 7-day range, retention refusal, 200-server cap) and the LogQL builder (`logql.py`, pure, re-validates every field when it renders). Settings `DCP_LOKI_URL`, `DCP_LOKI_TIMEOUT_SECONDS`, `DCP_LOG_RETENTION_DAYS` are in `.env.example` and the Compose passthrough. Migration 0007 renames the log permissions in place; `services/router.py`, `services/service.py` and one test follow.

**What the spike measured against a real Loki 3.3.2 (not reasoned):** `end` is **exclusive** and `start` **inclusive**; lines sharing one nanosecond timestamp come back in a stable order within identical queries (three runs), reversed for `backward`; a `contains` with quotes, backslashes, braces, pipes and a backtick matches **literally once escaped**, and the same string unescaped is a `400 parse error`; all eight presets are accepted and each matches exactly its positive lines and no negative; a 201-id selector works; `sum by (server_id) (count_over_time(...))` returns one sample per server that has lines and **omits servers with none**. Consequence for `OPEN-36`: paging by `end = ts + 1` re-reads the tie group, so the cursor must also carry how many lines at that timestamp were already delivered (and the last line's hash to verify it); the "stable order" is observed, not promised, so a mismatch is `422 invalid_cursor`. Commit 2 builds that.

Real results (2026-10-18): `scripts/check-all.sh` — every suite that ran **passed**: unit **1182** (was 886; +281 in `tests/logs/`, +15 config), frontend **227**, `ruff`, `ruff format`, `mypy app` (96 files), `bandit`, `pip-audit`, `npm audit`, API types unchanged, `.env.example` complete, doc links 485/0, backend changed-line coverage **165/166 (99.4 %)** (the one uncovered line is the old §16.7 stub, which commit 2 replaces). **The script skipped its integration suite** because `TEST_DATABASE_URL` was unset in this shell; I ran it separately against real PostgreSQL 17.2 and Redis: **398 passed** (was 397; the +1 is the 0007 round-trip test) in 356 s. **That run first failed once**: `test_viewer_has_only_read_permissions` asserted a Viewer's actions are exactly `{"read"}`, which the rename made false (`read:application`). The test now asserts the real invariant (every action is `read` or `read:<scope>`). **Mutation checks, each seen failing:** removing the `contains` escaping fails 7 tests; removing the retention check fails 2. **A real defect was caught by a test, not by review:** pydantic's `pattern=` is a *search*, so `service='x"} or {a="b'` passed the request model on the strength of its first character; the model now uses `fullmatch`, and the builder re-checks it anyway.

**Commit 2 (client, query service, endpoints) — done 2026-10-18.** `app/logs/client.py` (read-only, 15 s deadline, one connect retry, redirects refused as errors, its own circuit-breaker instance of the shared class), `app/logs/service.py` (scope in SQL, at most three selector groups, merge, signed cursor, ingestion state, outbound redaction), `app/logs/router.py` (§19.1 `POST /logs/query`, §19.2 `GET /logs/sources`) and §16.7 rewritten onto the same engine. `LogLevel` for §16.7 is now AD-21's. API types regenerated. API_CONTRACTS §19 is amended with everything the build added or changed.

**Three design points the tests forced, none of them in the plan:** (1) a **total order** for the lines Loki returns — time, then the stream's labels, then position in the stream — because the *order of streams in a response* is not promised and the first paging tests failed on it; (2) the **boundary tie group is re-read in full** by one extra bounded query whenever a selector group comes back full, because Loki's `limit` may keep a different subset of equal-timestamp lines than the merge order would; (3) **pydantic's `pattern=` is a search**, found in commit 1 and now also covered at the HTTP layer. The cursor therefore needs no assumption about Loki's tie order except that lines *within one stream* keep their order, and it verifies that with the last line's hash (a mismatch is `422 invalid_cursor`).

Real results (2026-10-18): unit **1220** (was 1182), frontend **227**, `ruff`, `ruff format`, `mypy app` (99 files), `bandit`, `pip-audit`, `npm audit`, `.env.example` and doc links clean, backend changed-line coverage **637/679 (93.8 %)** against the 80 % floor (the uncovered lines are client error branches and defensive guards). Integration against real PostgreSQL 17.2 and Redis: **490 passed and 2 errors** in the full run (386 s); both errors were `redis.exceptions.TimeoutError: Timeout reading from 127.0.0.1:6380` during test setup in `test_services_api.py`, and **both pass when run alone** (2 passed) — load on this machine, not a finding, but recorded because a flake is not a pass. `tests/integration/test_logs_api.py`: **77 passed**, the paging cases run with ties as stored **and** flipped. **Mutation checks, each seen failing:** dropping the per-line scope re-check fails `test_a_source_type_the_grant_does_not_cover_is_dropped_even_if_loki_returned_it`; removing the tie-group completion fails 2 paging cases (`forward-1`, `forward-3`, ties as stored). The `contracts: api types` check fails on an unstaged regeneration by design (it diffs the working tree against the index); it passes once the file is staged.

**The Loki stand-in is not evidence about a real Loki.** It evaluates the rendered LogQL with the semantics the commit 1 spike measured, so a wrong or too-wide selector fails the tests, but that a real Loki accepts these queries, and that paging holds against its real tie behaviour, is commit 3's job (smoke step 18f).

**Commit 3 (the real pipeline, asserted) — done 2026-10-18.** Smoke **step 18f** (`scripts/smoke_logs_api.py`, run inside the producer's container as the SPA would call: session cookie, CSRF token, straight to the backend) drives the log API through the real Alloy into a real Loki. `python -m app.inventory.seed --sample-log-host` registers the sample shipper's identity as a server with **fixed ids** and a different hostname from the one it labels with (issue 94 for the sample stack only; a drift test ties the ids to the Compose defaults). What it asserts is in [TESTING.md §11](docs/TESTING.md); the findings are below.

**What the real Loki found that the stand-in could not (three, all fixed):** (1) **`max_entries_limit_per_query: 1000` in `infra/loki/loki-config.yaml` (M3.1) made every continuation page and every tie-group completion a `400`** — they read `limit + 1 + already-delivered` lines, up to 5 000; the stand-in accepted any limit. The ceiling is now 5 000 with the reason written beside it and a smoke assertion on `/config`. (2) The **session from step 13 had expired** by 18f: 18b–18e together outlast the 30-minute idle timeout, so the first 18f run was a `401` about the harness; the step now logs in again. (3) **The API's own rate limit (30 a minute) throttled the harness**; the client waits out `Retry-After` as a client would (~3 minutes added), which also shows the limiter working. Two harness mistakes of mine, for the record: the CSRF check accepts the request's *own* origin and the first run sent the SPA's, and a case-sensitive `|= "canary"` filter hid the AWS canary (`CANARY`) from my own "did the raw push land" check.

Real results (2026-10-18): the full `scripts/smoke-test.sh` **ran to 18f and passed 1–18e** (build, login, seed, Prometheus, the sweep, real systemd, Loki limits, the Alloy pipeline, the journal); 18f then **failed three times for the reasons above and passed when run on its own against the kept stack**: catalogue and retention agree (14 d); 50 sample lines carry the inventory hostname and the shipper is `receiving`; each of the five file sources is searchable alone; the hostile container is `other`; 8 canaries are absent from the API's answer with the marker present; **8 single-line canaries pushed raw into Loki: none comes back**, the multi-line block does (pinned); a hostile `contains` matches literally; **all 8 presets accepted by a real Loki, each returning exactly its own lines**; **10 lines with 4+3 sharing one nanosecond across two streams, paged at sizes 1–9 in both directions: no repeat, no gap**; an unknown server id is dropped and counted; a registered server that never shipped is `silent`; LogQL-carrying fields and an unbounded query are `422`; **Loki stopped: `503 provider_unavailable`, nothing that reads as empty**; Loki back: `200` with lines. **Done in one piece afterwards:** a complete `smoke-test.sh` run from empty volumes with all of the above fixes in place passed; see [§7](#7-tests-executed-and-their-actual-results).

**Commit 4 (frontend) — done 2026-10-18.** `/logs` (`pages/logs/`): a structured filter form (time range, servers picked from the inventory, environment, application, service, source, severity, a literal `contains`, a preset from §19.2), the result table (time to the millisecond with UTC in the `title`, server linking to its page, source, service, level as a word, line), "Load older lines", and a "Logs" nav link shown from the advisory `logs:read:application`. `api/logs.ts` maps `503`/`504` to its own result, **`unavailable`**, distinct from `unreachable` ("we could not ask the backend"), because the backend did answer and what it said is that the log store could not. The service page links to the search pre-filled through the router's history state, not the URL (`OPEN-38`). `OPEN-33`'s corrections to DATABASE.md, the `Application` model comment and the §18.4 schema docstring are in this commit because the docstring is in the OpenAPI schema and `schema.ts` moved with it.

**What the tests pin (52 new):** the request is structured filters and no `query`/`logql`/`selector`/`regex`/`labels`; empty fields are omitted; a requested target is required; a **markup-bearing line creates no element** and the source has no `dangerouslySetInnerHTML` (comments stripped first, because the file explains the rule in words and tripped its own pin); **503/504/not_configured are alerts that say "this is not an absence of logs" and render no table and no "no lines match"**; a failed search clears the table; an empty result from a `silent`, `quiet` or `unknown` server **refuses to say "no errors"** and names the server; lines from one server do not hide that another is silent; excluded servers are announced; "load older" appends, continues the query that produced the rows and not the edited form, keeps rows when it fails, and **discards a slow answer a newer search overtook**; a pre-fill searches once and puts nothing in the URL. **Mutation checks, each seen failing:** rendering the line through `dangerouslySetInnerHTML` fails the markup test and the source pin; disabling the ingestion notices fails 4 tests. One test I wrote first was wrong about the page (the Search button is disabled while a search is in flight, so a double click cannot race itself); the race that can happen is "load older" against a new search, and the test now covers that one.

Real results (2026-10-18): `scripts/check-all.sh` with the integration suite enabled — unit **1222** passed, integration **493 passed, 0 errors** (381 s; the two Redis timeouts of the previous run did not recur), frontend **279** (was 227; +52), `tsc`, `eslint`, `prettier`, the Vite build, `ruff`, `mypy`, `bandit`, `pip-audit`, `npm audit`, `.env.example`, doc links all clean; changed-line coverage **backend 660/706 (93.5 %)**, **frontend 170/181 (93.9 %)** against the 80 % floor. **The one failing check was `contracts: api types`**, correctly: the docstring correction above changed the OpenAPI description, `schema.ts` was regenerated, and the check demands the regenerated file be committed. **Not run:** a browser (no Playwright path for logs, issue 100); the page has been exercised only by component tests and by the API calls the smoke test makes.

**Noted, not changed:** M2.4's §16.7 `level` parameter takes the journald spelling (`notice`, `warning`) while Loki's `level` label is AD-21's (`warn`, no `notice`). Commit 2 must map or replace it when §16.7 is answered; nothing is decided here.

**Commit 5 (documents, one stale row, final verification) — done 2026-10-18.** DOCUMENTS: SECURITY §17 gains DCP's outbound pass as built; TESTING §11 describes 18f; IMPLEMENTATION_PLAN marks M3.2; API_CONTRACTS §19 was amended in commit 2. **One stale row found by reading rather than by a test: §26.1 still reported `Loki: Log ingestion is not implemented yet (M3.1)` after DCP began querying it** — a status page telling an operator the opposite of the truth (CLAUDE.md §7). It is now a live probe (`app/logs/status.py`, `vector(1)`, a reason code and never a URL, the breaker state), unit-tested for not-configured, ok, unavailable and refused, and asserted in smoke 18f both while Loki is up (`ok`, circuit closed) and while it is stopped (`unavailable`).

**What differed from the plan, each for a reason found while building:** (1) selector groups are at most **three**, not two (application-only, system-only, both); (2) the cursor carries *how many lines at the boundary timestamp were delivered* and the merge order is total, and the **boundary tie group is re-read in full** whenever a group comes back full — the plan's `(timestamp, hash)` cursor would have repeated or skipped lines; (3) `GET /logs/sources` is a static catalogue and the ingestion state is on §19.1's first page only (the plan said both); (4) Loki's read ceiling was raised to 5 000; (5) the sample log host is registered through a seed flag, with fixed ids, rather than through the API.

**Not M3.2:** §19.3 and deployment-to-logs (M5), alert rules (M3.3), per-server Alloy config (issue 94 stays open for the real fleet; only the sample identity is registered), a closure for the multi-line PEM gap (issue 92), E2E path 3 (issue 60).

### M3.1 — COMPLETE LOCALLY 2026-10-18 (all five commits on `phase-M3.1`, cut from `main` after PR #18). Not pushed, not on hosted CI.

**Next, in this order:**

1. **Push `phase-M3.1`, open the PR and watch CI.** Unmeasured on a hosted runner: the smoke test is longer (18c–18e add a second systemd-fixture start, four throwaway Alloys and two image pulls), and 18e is NOT RUN wherever 18b is (issue 69). The `compose` job also validates the `logs` and `fixtures` profiles.
2. **M3.2 — log search API and UI** ([IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md#m32--log-search-api-and-ui)). Read **issue 93 first**: an empty result for a server with no recent ingestion must not read as "no errors". Also inherited: `POST` only (`OPEN-20`, closed), `logs:query` is the seeded spelling of `logs:read:system` and §16.7 is routed and answers `503 not_configured` until the Loki client exists; the label contract and closed enums are in [ARCHITECTURE.md §5](docs/ARCHITECTURE.md#5-centralized-logging-architecture); DCP's own outbound redaction pass must be written and tested against `canaries.json` (the same corpus), and will not catch a multi-line key (issue 92); the sample identity is not a registered server (issue 94).
3. **`OPEN-28` is closed** (per-environment `DCP_UTILISATION_THRESHOLDS`, rule file generated from it) but **how utilisation moves the `warning`/`critical` health state is not decided** and belongs in M3.3's plan.
4. Leftovers from earlier hand-overs: a registration/edit screen for services (issue 72); E2E path 3 (issue 60).

**What M3.2 should not re-litigate:** the seven labels and their closed values (`AD-21`); server-side LogQL only; Alloy drops a bad identity rather than storing it; Loki stays single-tenant for v1 (`OPEN-31`); the canary corpus is the contract between the two redaction layers.

**Traps this milestone paid for:** Git Bash rewrites any `/path` argument to a Windows path — use `MSYS_NO_PATHCONV=1` for `docker run`/`compose run` arguments, and a rewrite can make a container exit at once so that "nothing stored" passes vacuously (**assert a counter, never only an absence**); the Edit tool converts a typed `\r\n` into real CR/LF characters (write escapes through a script and check the bytes); `sleep` followed by a command is blocked in this environment; an Alloy `loki.process` receives labels but not the extracted map, so normalisation lives in one shared stage; a missing journal path makes `/-/ready` fail, so the journal source is its own file.

**Decisions closed before M3.1, at the owner's instruction to take the recommended option** (all recorded in the documents, which are the record): `OPEN-03` file-managed alert rules; `OPEN-08` Loki 14 d / Prometheus 30 d / alerts 180 d / audit 400 d, no disk quota in v1; `OPEN-13` notification channels in DB tables; `OPEN-16` log access is scoped permission only, no extra approval; `OPEN-20` `POST` only for log queries; `OPEN-28` per-environment `DCP_UTILISATION_THRESHOLDS` with the M3.3 rule file *generated* from it (one copy of the numbers; how utilisation maps to `warning`/`critical` *health* is left to M3.3's plan). **New, raised by this plan:** `AD-21` (closed label set and closed label values, enforced at Alloy; bad identity labels drop the line), `AD-22` (Docker logs from `json-file` files, never the Docker socket), `OPEN-31` (nothing authenticates which shipper claims which `server_id`/`environment` — accepted for v1, closed by a per-environment-tenant gateway in Phase 6). **Deliberately not closed:** `OPEN-01`, `05`, `06`, `07`, `10`, `11`, `12`, `15`, `17`–`19`, `21`–`27`, `29`, `TS-03`, `TS-04` — later phases, and several need facts only the organisation has (IdP, RPO/RTO, topology, sandbox owner).

**Plan (five commits, the M2.x shape):**

1. **Loki config.** Retention (`336h`, compactor retention on), ingestion-rate / line-size / stream-count limits, `max_label_names_per_series` = 7 and `discover_service_name: []` (Loki 3 otherwise adds an eighth label), non-root. Smoke step 8's Loki half: push a line, read it back, and a push with an eighth label is rejected.
2. **Sample producers** (`logs` profile, reusing the backend image — no new image): Nginx access/error, PHP-FPM, application JSON, syslog and Docker `json-file` lines in a shared volume, each carrying a canary secret and a high-cardinality field.
3. **Alloy pipeline.** File sources for all of the above plus `loki.source.journal`; labels `environment`, `server_id`, `hostname`, `application_id`, `service`, `source_type`, `level`, kept by `stage.label_keep`; `source_type` and `level` collapsed to closed enums (`level` unmapped → `unknown`, never guessed); journald `service` restricted to named `.service` units, transient/instance units → `other`; lines with a missing or malformed `server_id`/`environment` dropped; redaction stages ported from `app/core/redaction.py` to RE2. Admin port bound to loopback and no longer published (SECURITY §7 says Alloy exposes no inbound port; the placeholder did).
4. **Tests.** One canary corpus shared by the backend redaction tests and the Alloy check; a drift test that the Alloy expressions catch every canary; smoke steps that drive the **real pipeline** and read Loki: labels ⊆ allowlist, a high-cardinality field never becomes a label, every canary is absent *and* the `[REDACTED]` marker is present (so absence cannot mean a dead pipeline), bad-identity lines dropped.
5. **Docs + PROGRESS** with real results. SECURITY §17 gets the RE2 and line-based limits.

**Commit 1 (Loki config) — done 2026-10-17.** `infra/loki/loki-config.yaml`: 14 d retention with the compactor's retention switch on, `max_label_names_per_series: 7`, `discover_service_name: []` (so Loki 3 adds no eighth label), `discover_log_levels: false`, 7-day old-sample rejection, ingestion/line/stream limits (design budgets, not measurements), and `analytics.reporting_enabled: false` (Loki phones home by default; SECURITY §7 gives the data tier no outbound route). Compose `loki` gained a healthcheck and a resource limit; it already ran as uid 10001. Smoke **step 18c** (new) pushes through the project network from the backend container and asserts: seven-label push accepted and read back with exactly those labels; an eighth label → `400`; an 8-day-old sample → `400`; every label Loki holds is on the allowlist and `service_name` is absent; `/config` reads back retention 2w, compactor on, reporting off.

Real results (2026-10-17): `scripts/smoke-test.sh` from empty volumes **SMOKE TEST PASSED**, no "incomplete" marker, 18c's output as above. `scripts/check-all.sh` **ALL CHECKS PASSED**: unit 853, integration 397 (real PostgreSQL 17.2 and Redis), frontend 227; coverage gates report no measured file changed (no application code changed). **One earlier `check-all` run failed on a 120 s per-test timeout in the integration suite** (stack dump only, no test named); two reruns passed 397/397, but they took **237 s and 321 s**. Cause not identified; the first failure came straight after a full smoke run tore down and restarted Docker, so load is the suspect, not evidence. Not run: Alloy shipping anything (commit 3).

**Commit 2 (sample log producers) — done 2026-10-17.** `infra/compose/sample-logs/produce.py` (standard library only) writes a managed server's layout — `nginx/{access,error}.log`, `php-fpm/php-fpm.log`, `syslog`, `app/sample-api.log` (JSON lines) and `docker/<id>/<id>-json.log` — every 10 s, with rotation. Each round carries **nine canary secrets in every source** (`canaries.json`: bearer token, `password=`, JSON `api_key`, AWS key id, JWT, connection string, session cookie, a one-line PEM block, and a **multi-line PEM block marked `known_gap`**), request/user/trace ids in the line (high cardinality), severities in every spelling plus an unmapped one and a hostile one (`x<script>…`), and a hostile container service name. Compose `sample-logs` (profile `logs`) reuses the backend development image, runs as uid 10001, is healthy while its heartbeat file is under a minute old. Dockerfile `development` stage pre-creates `/var/log/sample` (the M2.3 targets-volume pattern; not in production). `backend/tests/test_sample_logs.py` (6 tests) asserts every canary reaches every source — **it failed on its first run, because the Nginx access log carried none**, which is exactly the case where "absent from Loki" would have proved nothing. `AD-22` was **corrected**: `tag` is not a `json-file` option; the service name comes from a container label exposed via the driver's `labels` option, which lands in each line's `attrs`.

Real results (2026-10-17): sample-logs started under Compose, healthy, uid 10001, files and canary lines inspected in the container. `scripts/check-all.sh` **ALL CHECKS PASSED**: unit **859**, integration **397** (real PostgreSQL 17.2 and Redis), frontend 227; backend changed-line coverage reports no coverable line changed (the producer lives under `infra/` and is outside the gate's report, so its tests are the only check on it). **Not run:** the smoke test (the producer is not in it until commit 3 gives Alloy something to read); the journald source (no producer — commit 3 decides whether the `systemd-host` fixture's journal can be shared).

**Commit 3 (Alloy pipeline) — done 2026-10-17; verified by hand against a real Alloy 1.5.1 and Loki 3.3.2, with no standing assertion yet (commit 4).** `infra/alloy/config.alloy`: file sources for Nginx access/error, PHP-FPM, syslog, the application JSON log and the Docker `json-file` files; each parses its own format and sets only *raw* labels, then all meet in one `loki.process "common"`, which (1) **drops** a line whose `environment`, `server_id`, `hostname` or `application_id` is missing or malformed, (2) collapses `level` into `debug|info|warn|error|critical|unknown`, (3) collapses `service` into a plain name or `other`, (4) redacts via six RE2 `stage.replace` stages ported from `app/core/redaction.py`, (5) keeps **only** the seven allowed labels. `infra/alloy/journal.alloy` is the journald source, kept separate because a missing journal makes `/-/ready` fail. Both files are `alloy fmt`-clean. Compose `alloy`: runs as uid 473 (the image defaults to root), admin port on loopback and **not published** (the placeholder published it on `0.0.0.0`, against SECURITY §7), `--disable-reporting`, read-only mounts, a positions volume, healthcheck over `/dev/tcp` (the image has no wget/curl).

What I observed (2026-10-17): Loki held **exactly the seven labels** (`filename` and `job`, which Alloy adds, were gone); `source_type` ∈ application/docker/nginx/php_fpm/syslog; levels `critical debug error info unknown warn`; `TRACE`, an HTML-bearing level and an Nginx access line all `unknown`, `err`→`error`, `fatal`→`critical`, php `WARNING`→`warn`, nginx `[crit]`→`critical`; the hostile container name `Weird Name; DROP TABLE` → `service="other"`; **eight of nine canaries absent from Loki in every source and the marker present in every source; the multi-line PEM block leaks, as documented.** 56 redacted JSON application lines all still parsed. **Fail-closed identity:** a second Alloy with `ALLOY_SERVER_ID=not-a-uuid` dropped 2 310 lines (`reason="bad_server_id"`) and stored nothing under its hostname. **Journald:** the `systemd-host` fixture with a volume on `/var/log/journal` wrote a real persistent journal, and Alloy (group 999) read it: `node-exporter.service` kept as the service, non-unit entries `other`, priority mapped to `info`, `password=` redacted, no `application_id`. **That journald run used ad-hoc `docker run` commands, is not in Compose or the smoke test, and is therefore not repeatable yet.**

**Two mistakes caught on the way:** my first bad-identity check was vacuous — Git Bash rewrote `--storage.path=/tmp/alloy` into a Windows path, the container exited at once, and "nothing stored" proved nothing. It was caught because the drop *counter* was absent, not because the store was empty; the rerun (`MSYS_NO_PATHCONV=1`) shows the 2 310. Lesson for commit 4: assert the counter, never only an absence. And the SECURITY.md paragraph I wrote first claimed a test asserts the multi-line leak; that test is commit 4's, and the text was corrected before committing.

Checks run: `alloy fmt` on both files, `docker compose config` with all five profiles, `scripts/check_env_example.py`, the doc-link check. **`check-all.sh` and the smoke test were not re-run for this commit** — no backend, frontend or smoke-script file changed since the last full green run; the smoke test gains its Alloy assertions in commit 4.

**Commit 4 (standing assertions) — done 2026-10-18.** What commit 3 only observed is now asserted. **Smoke step 18d** runs the file pipeline (details in [TESTING.md §11](docs/TESTING.md)): Alloy non-root / no published port / not privileged / no Docker socket; the seven-label allowlist; required labels per stream with `application_id` on the application source only; 13 severity mappings; the hostile container name → `other`; **8 canaries absent from Loki in every source with the marker present in all 5 sources**; the multi-line PEM pinned as a leak; redacted JSON still parses; then **four throwaway Alloys with a bad `server_id`, `environment`, `hostname` and `application_id`, each asserting the drop counter before Loki's silence**, the last with a positive control. **Step 18e** reads a real systemd journal (new `alloy-journal` service and a `fixture-journal` volume under the `fixtures` profile), follows `SMOKE_SYSTEMD`, and is listed NOT RUN where 18b is. `scripts/smoke_logs.py` holds the assertions and runs inside the producer's container. `backend/tests/test_canary_corpus.py` (27 tests) runs one corpus through `redact_text` and through the expressions parsed out of `config.alloy`. The journald path that commit 3 had only by hand is now in Compose and the smoke test.

Real results (2026-10-18): `scripts/smoke-test.sh` from empty volumes **SMOKE TEST PASSED** — no "incomplete" marker, so 18b and 18e both ran against the real systemd fixture on this machine; 18d and 18e lines as above. `scripts/check-all.sh` **ALL CHECKS PASSED, all 18**: unit **886** (was 859), integration **397** (real PostgreSQL 17.2 and Redis; 541 s, see issue 91), frontend **227**. **Mutation checks, each seen failing:** deleting the AWS stage from `config.alloy` fails 3 corpus tests and the smoke ingestion assertion, which names `['aws_access_key_id']`; dropping `Bearer` from the backend fails `test_the_backend_redacts_every_single_line_canary[bearer_token]`; deleting `stage.label_keep` fails the smoke ingestion assertion — **by timeout, not by a label message**, because Loki's own seven-label limit then rejects every push, so nothing arrives to inspect. That is the backstop working (two layers), but it means the failure text for that mutation is "timed out waiting for all five file sources", not "labels outside the allowlist". Two small defects of mine were fixed on the way: the first Alloy healthcheck had real CR/LF characters where `
` was meant (the Edit tool converted the escapes; it worked only through YAML line folding), and `ALL_PROFILES` lacked `logs`, which would have left the new volumes behind after teardown.

**Not run / not claimed:** the hosted CI run (nothing is pushed). The smoke test now takes noticeably longer (two more steps, four extra containers, a second fixture start); its duration on a two-core runner is unmeasured. The `alloy-journal` `group_add: 999` is the `systemd-journal` gid of the fixture's Debian image and would differ on another distribution.

**Not M3.1 (and not done in it):** any backend or frontend code (no Loki client, no endpoints, no migration — §16.7 stays `503 not_configured`), DCP's outbound redaction (M3.2), deployment logs (M5), rendering Alloy config per managed server (parameterised by environment variables here; enrolment tooling is later).

**Risks stated up front:** a multi-line PEM block cannot be matched by a per-line redaction stage (documented gap, tested as a known limit); the Docker bind mount may not work under Docker Desktop — **outcome: the mount works, but the files are `root:root` `0640`, so a non-root shipper reads nothing and says nothing (issue 95)**; ingestion from a *real* journal — **outcome: shared from the `systemd-host` fixture and asserted in smoke step 18e**; images are still not digest-pinned (issue 21).

### M2.5 — COMPLETE LOCALLY 2026-10-17 (all five commits); D1–D4 approved as written. Not pushed, not on hosted CI.

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

- ~~The dashboard page has not been updated.~~ Done in commit 4 (below).
- **`stale` on a row/detail changed meaning slightly:** it now follows the *health verdict* (`null` for `unknown`, including a monitoring-disabled server with an old `last_seen_at`; before, it was `age > stale line` for any seen host). At defaults the numbers agree for every monitored host.
- **A stale reference hides health everywhere**, including per-server detail, rather than showing a frozen last-known state with a stale label. Chosen for the simplest rule that obeys §4 rule 2; a "last known" display could be added without a contract change.
- **The rollup's `servers.stale`/`unreachable` and `server_health.stale`/`unreachable` are different numbers** (stored status vs computed health) and the UI must label them so.
- The dashboard cache (15 s) is not keyed on the reference, so a marker change shows up after the entry expires, like any other write.

**Commit 4 (frontend) — done, committed.**

- `components/HealthCell.tsx`: `HealthBadge` keeps three facts apart that shared one grey "Unknown" — `null` is **"Not evaluated"** (no badge: a badge is a verdict), `unknown` is an evaluated "Unknown" badge, and a real state is a badge; `LastSeen` renders **"Never seen"** (not a time, not "stale") for a null.
- Dashboard: three labelled groups — **Registration status** (`servers.status`, links to the server list), **Health** (five computed counts plus `critical`, dated by when Prometheus last answered) and **Services**. A null in an `ok` section (`critical`) says "Not evaluated — utilisation is not assessed", never `0` and never "Collected". Unusable rollups (unavailable, stale, not configured) show the reason in every card and no digit. New "Last seen" column, a **Health status** filter that goes to the list only, and a "Health changes" list (empty = "none recorded in the last 30 days", null = the section's reason). The reason banner above the table appears only when the rollup has no counts.
- Server detail: health badge with the line it was judged against ("Stale after N s without a scrape"), the reason when unknown or not evaluated, `Stale` as Yes / No / "Not determined — reason" (never "No" for an unknown), `Last seen` via `LastSeen`.
- Tests: frontend **227 passed** (was 211): the never-0 rules for every card in the unusable states, the three-way distinction in the table and on detail, the filter's request, the changes list's three states, and axe on both pages with real health data. Mutation checks (null rendered as `0`; the `ok`-but-null branch removed; `null` rendered as a badge) each fail the expected tests.

Real results (2026-10-17): `scripts/check-all.sh` **ALL CHECKS PASSED, all 18** — backend unit 848, integration 397 (real PostgreSQL 17.2 and Redis), frontend 227, changed-line coverage frontend 23/23, backend 290/303 (the uncovered lines are async-handler under-reporting, issue 80). **Not done: the pages were not viewed in a real browser** — jsdom has no layout or contrast, so spacing, wrapping at six health cards and the palette are unchecked; and the smoke test was not run.

Notes: the health cards are not links (the Health status filter is the drill-through), the registration-status cards still are. The server *list* page still shows only the registration status; it has no health column. Neither was asked for in the plan.

**Commit 5 (smoke + docs) — done, committed. One defect found by it, fixed in it.**

- `scripts/smoke-test.sh` step **18a** (tight lines 30 / 45 / 75 s, exported at the top and passed through Compose): an administrator accepts the registration; **the scheduled sweep is seen running in the worker container's log**; the host is `healthy`; the dashboard rollup is `ok` and sums to the server total; stopping the exporter gives **`STALE` then `UNREACHABLE`**, each agreeing with the server detail's `health.state`; exactly **two audited moves** (`MANAGED>STALE,STALE>UNREACHABLE`); **`updated_at` unchanged**; the move is on the dashboard; restarting gives `MANAGED`/`healthy` and a third move; then **Prometheus is stopped for 90 s** (past the 75 s line): the host stays `MANAGED`, no move is audited, health reads "not evaluated" and dated `(None, 'stale', None)`, and after Prometheus returns it is `healthy` again with no extra move. docs/TESTING.md describes it; AD-14 and ARCHITECTURE §4 point 5 are amended to what was built.
- **The first run failed at the very end, and that was a real defect (issue 88).** After the outage the host never became healthy again: the worker's Prometheus circuit breaker was stuck. Reproduced in a scratch script before changing anything, then fixed with failing tests first.
- **Real results (2026-10-17):** second `scripts/smoke-test.sh` run from empty volumes: **SMOKE TEST PASSED** (no "incomplete" marker; step 18b ran against the real systemd fixture on this machine). Final `scripts/check-all.sh`: **ALL CHECKS PASSED, all 18** — backend unit **853**, integration **397**, frontend **227**; changed-line coverage backend 309/322, frontend 23/23.
- **Not done:** the hosted CI run (nothing is pushed; the 18a lines were tuned on a fast machine — a two-core runner may need looser lines or longer deadlines, and 18a adds roughly six minutes to the `compose` job); the server list page still shows registration status only. **The pages were then viewed in a real browser (below).**

**Browser review (2026-10-17, after commit 5, uncommitted fixes folded into the same final commit).** Stack: full Compose with `observability`, signed in through the project's mock IdP as its `admin` dev account, `app.inventory.seed`, plus one real scraped host (the node exporter) so the probe wrote the reference, and demo servers given hand-set `last_seen_at` ages (database rows, not Prometheus data) so every verdict was on screen at once. Confirmed: six health cards sum to the server total and `Critical` says "Not evaluated"; the table shows each badge, "Never seen", and Health next to the (different) Registration column; the `Health status` filter sends `health_status=` and returns the matching row; the "Health changes" list renders; server detail shows `Unreachable` with "Stale after 300 s without a scrape", and a never-seen host shows `Unknown`, "Never seen" and "Not determined — This server has not been scraped yet."; with Prometheus stopped for 100 s every card reads "Stale — …" and no count is shown. **Three things found and fixed:**

- At 375 px the status table (eight columns) was 579 px wide and made the *page* scroll sideways; it now scrolls inside its own box (`overflow-x-auto`), and the page is exactly the viewport width. The extra "Last seen" column made an existing problem worse.
- The stale-reference reason embedded a raw ISO timestamp with microseconds, repeated on six cards; it no longer does (`collected_at` already dates it).
- The header still said "Milestone M2.4"; it now says M2.5.

Not changed, noted: the stale reason still repeats on all six cards (deliberate since M1.6: each card stands alone); "Stale after 300 s" is shown under an `Unreachable` verdict (the API exposes only the `stale` line, not the `unreachable` one); the server *list* page has no health column; the Compose `frontend` service is built from the image, so a frontend edit is not live until `up -d --build frontend`. The final `check-all.sh` after these fixes: **ALL CHECKS PASSED, all 18** (backend 853 unit / 397 integration, frontend 227, frontend changed-line coverage 25/25).

**Next, in this order:**

1. **Push `phase-M2.5`, open the PR and watch CI.** Now overdue by a milestone's worth of changes: step 18a's timing is unmeasured on a hosted runner (issue 69 still means 18b may be NOT RUN there).
2. **M3.1 — Alloy and Loki ingestion**, the next milestone in [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). `OPEN-28` (utilisation-driven `warning`/`critical`) must be settled before M3.3.
3. The leftovers from earlier hand-overs: a registration/edit screen for services (issue 72) and E2E path 3 (issue 60), which now has real things to assert.


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
