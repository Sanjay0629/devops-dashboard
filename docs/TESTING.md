# Testing and Verification — DevOps Control Plane

**Status:** Draft for review. **No test exists and no command in this document has been run.** Every command is marked **[to implement]**.
**Source of truth:** [`../PRD.md`](../PRD.md) §12. Related: [SECURITY.md](SECURITY.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [API_CONTRACTS.md](API_CONTRACTS.md) · [DATABASE.md](DATABASE.md)

---

## 1. Strategy and principles

This application can stop a production service and start a production deployment. Testing therefore targets a specific set of lies the system must never tell:

1. "This server is healthy" when it has simply stopped reporting.
2. "This service is inactive" when its host is unreachable.
3. "This operation succeeded" when it was only *submitted*.
4. "This deployment succeeded" when its health checks failed.
5. "No logs found" when the log store is down.
6. "0%" when the value is unknown.
7. "You are not authorised" to someone who is, or worse, the reverse.

Each is a dedicated, named test, not an emergent property of coverage.

**Principles**

- **Mocks for automated tests, explicitly configured environments for real integrations.** The ordinary local suite requires **no cloud credentials, no real IdP, no real Jenkins and no production access** (PRD §12 intent, and a hard rule here).
- **Real PostgreSQL, always.** Never SQLite. Partial unique indexes, `CHECK` constraints, partitioning, grants and `ON CONFLICT` behaviour are the things being tested; SQLite would pass tests that PostgreSQL would fail, which is worse than no test.
- **Test behaviour at the boundary**, not implementation internals. The permission matcher, state machines and normalisation tables are the exception — they get exhaustive table-driven unit tests because their edge cases are security-relevant.
- **A test that cannot fail is not a test.** Every security control has a test that is proven to fail when the control is removed (fault injection or a deliberately broken fixture).
- **Evidence over assertion.** A result is reported only after the command ran and the output was read ([CLAUDE.md](../CLAUDE.md)).

---

## 2. Test levels and environments

| Level | Scope | Dependencies | Speed | Where |
|---|---|---|---|---|
| Unit (backend) | One function/class | None; everything stubbed | ms | CI on every push |
| Unit/component (frontend) | One component/hook | MSW-mocked API | ms | CI on every push |
| Integration (API) | Route → service → DB | Real PostgreSQL + Redis containers; upstreams stubbed | seconds | CI on every push |
| Integration (upstream) | Client ↔ real upstream image | Prometheus, Loki, Alertmanager, mock Jenkins, mock IdP containers | tens of seconds | CI on every push |
| Provider adapter | Adapter ↔ mocked cloud API | `moto`/response-level mocks | seconds | CI on every push |
| Worker | Task + state machine | Real PostgreSQL + Redis; Celery eager or a real worker | seconds | CI on every push |
| Compose smoke | Whole stack boots | Full `core` + `observability` profiles | minutes | CI on every push to main, nightly |
| E2E | Browser → stack | Compose stack with mocks | minutes | CI on PR (critical paths) + nightly (full) |
| Performance | Load against the stack | Seeded volume | long | Phase 6, on demand |
| Real-integration | Against actual AWS/Azure/Jenkins | Explicitly configured, non-production only | manual | Manual, recorded in `PROGRESS.md` |

### 2.1 Mocking policy

| Dependency | Automated tests | Real-integration tests |
|---|---|---|
| OIDC provider | Mock IdP container issuing signed tokens from a test key | Configured non-production IdP tenant |
| AWS | `moto` or response-level mocks, including throttle/auth-error/timeout paths | **Non-production account only**, documented, manual |
| Azure | Response-level mocks of ARM and Run Command | Non-production subscription only, manual |
| Jenkins | Mock HTTP server replaying realistic queue→build responses | Non-production Jenkins with a test pipeline |
| Prometheus | **Real container** with seeded series (easier and more faithful than mocking PromQL) | — |
| Loki | **Real container** with pushed test logs | — |
| Alertmanager | Real container, plus direct webhook posts for edge cases | — |
| Secrets manager | In-memory fake behind the same interface | Real store, manual |
| Notification channels | Local HTTP sink + SMTP sink | Manual, to a test destination |

**Rule:** a real-integration test never runs in the default suite, never runs in CI, and never touches production. It is invoked explicitly with its own marker and its result is recorded as a *manual check with a date*, not as a passing automated test.

---

## 3. Backend unit tests

PRD §12 names these; each gets a table-driven suite.

| Area | Cases |
|---|---|
| **Permission evaluation** | Exact match; wildcard per dimension; `environment:["*"]` **not** granting production; multi-dimension conjunction; explicit `DENY` over `ALLOW`; expired grant; empty scope ⇒ deny; malformed scope JSON ⇒ deny; resolution raises ⇒ deny; target environment taken from the stored row, not the request |
| **Server state classification** | Fresh; within warning; stale threshold crossed; unreachable threshold crossed; `last_seen_at IS NULL` ⇒ never-seen (not stale); exporter `up=0` with recent `last_seen_at`; monitoring disabled; clock-skewed sample in the future |
| **Service-state normalisation** | Every row of the table in [ARCHITECTURE.md §6.1](ARCHITECTURE.md#61-monitoring-read); host-stale **overrides** unit state; Windows ⇒ `UNSUPPORTED`; exporter series missing ⇒ `UNCOLLECTED`; `UNCOLLECTED` ≠ `INACTIVE` ≠ `FAILED` |
| **Alert condition evaluation** | Threshold crossed/not; sustained-duration logic; `dedup_key` stability across repeats and instability across distinct targets; unmatched labels ⇒ unlinked alert; acknowledgement does **not** set `resolved_at` |
| **Deployment state transitions** | Every legal edge; a representative set of illegal edges ⇒ raise; terminal states not re-enterable; `SUCCEEDED` requires pipeline success **and** health-check pass; `ROLLED_BACK` only after a successful rollback deployment |
| **Operation state transitions** | Every legal edge; illegal edges ⇒ raise; `SUCCEEDED` requires `observed_state_after`; `VERIFYING` surfaces as `RUNNING` externally; deadline arithmetic |
| **Provider adapter error handling** | Each error class mapped to the right API code and retry policy; `Throttled` honours `retry_after`; write timeouts are **not** retried; `UnsupportedOperation` is terminal and explicit |
| **Secret redaction** | Each pattern class (bearer token, `password=`, AWS key id, PEM block, connection string, JWT, cookie value); nested JSON; log-message args; traceback frames; **no length/prefix/hash leak**; a non-secret that merely looks similar is not over-redacted into uselessness |
| **Input validation** | Enum rejection; pattern rejection; length bounds; unknown field ⇒ `422`; immutable field on `PATCH`; `from > to`; limit above max; cursor tampering; `next` open-redirect forms (`//host`, `https://host`, `/\host`) |
| **Idempotency resolution** | New key; replay with identical body; replay with different body ⇒ conflict; different key with an in-flight target ⇒ conflict |
| **Query builders** | PromQL template rendering per OS family with label escaping; LogQL selector construction per filter combination with `contains` escaping; guard arithmetic |

---

## 4. Database and migration tests

Against a **real PostgreSQL container**.

- `alembic upgrade head` from an empty database creates every table, constraint, index, partition and grant.
- **Autogenerate reports no drift** against the models — the strongest form of the model/schema consistency check, and the reason `server_default` must be `text("now()")` rather than a bare string (a literal string never compares equal to the reflected default, so the check would cry wolf on every timestamp column and be ignored).
- `downgrade -1` then `upgrade head` round trip.
- Full `downgrade base` where supported; where a revision is irreversible, assert the documented comment exists.
- Seed revisions are idempotent when re-applied.
- Each unique and partial index actually rejects a duplicate: `(cloud_account_id, provider_resource_id)`; `(server_id, service_name)`; `(requested_by, idempotency_key)`; the one-in-flight operation index; `metrics_instance`; the open-alert `dedup_key` index.
- Each `CHECK` rejects an invalid value, including `ck_operations_verified_success` and the deployment health-check-evidence check.
- **Audit immutability:** `UPDATE`, `DELETE` and `TRUNCATE` on `audit_events` all fail, including when issued directly against a partition, and including as a superuser (AD-11). The suite also asserts that the test role *is* privileged, so the tests cannot pass for the wrong reason — grants alone were measured to be insufficient, because a superuser bypasses them.
- Partition routing: a row with a future `occurred_at` lands in the right partition; a missing partition **fails loudly** rather than silently losing an audit row.
- `updated_at` trigger fires on update, not on insert-only columns.
- Soft-delete semantics: a deleted row is excluded from scope resolution and can never be an operation target; a name can be reused after deletion.
- `ON DELETE RESTRICT` prevents deleting a referenced user, server or application.
- Concurrency: two sessions inserting the same idempotency key — exactly one succeeds; two sessions claiming the same operation row — exactly one wins.
- Timezone handling: a row written in one session timezone reads back identically in another.
- **Migration failure path (PRD §12):** a deliberately failing revision leaves the database consistent and the application refuses to start rather than running against a half-migrated schema.
- Schema drift: models autogenerate **no** pending diff against head.

---

## 5. API integration tests

For **every** endpoint in [API_CONTRACTS.md](API_CONTRACTS.md):

- Happy path with a correctly scoped identity.
- **Unauthenticated ⇒ `401`** (every protected endpoint; the public list is asserted to be exactly the five documented entries).
- Insufficient permission ⇒ `403` + an `AUTHZ_DENIED` audit row.
- Out-of-scope target ⇒ `404` or `403` exactly as the endpoint documents.
- Validation failures ⇒ `422` with the structured `details.fields` shape.
- Unknown request field ⇒ `422`.
- Missing CSRF token on a state-changing request ⇒ `403`.
- Missing `Idempotency-Key` where required ⇒ `422`; replay ⇒ `200` + `idempotent_replay: true`; differing body ⇒ `409`.
- Pagination: first page, next cursor, last page, `has_more`, invalid cursor, `limit` over max ⇒ `422` (**not** a silent clamp).
- Filtering: allowlisted values accepted, unknown rejected with the allowed set in `details`.
- Error envelope shape identical across every error path; **no stack trace, SQL, provider payload or internal hostname** in any message.
- `X-Correlation-ID` echoed; generated when absent; propagated into audit rows and worker logs.
- Read-only mode ⇒ `423` on writes, reads unaffected.
- Rate limit ⇒ `429` with `Retry-After`.
- Audit row written for every state change, in the same transaction (a rolled-back change leaves no audit row; a failed audit write fails the change).

A meta-test enumerates the route table and asserts each route is covered by at least one authentication test and one authorisation test — so a new endpoint cannot ship untested.

---

## 6. Provider-adapter tests

Per adapter (AWS, Azure, agent, demo), against mocked provider APIs:

- `get_server_info`, `check_connectivity`, `list_managed_services`, `execute_operation`, `get_operation_status` happy paths.
- Error matrix: authentication error, authorisation error, not found, throttled (with and without `retry_after`), timeout, unsupported operation, partial failure, provider unavailable, not configured.
- Retry behaviour: reads retried, **writes not retried** — instead reconciled from the persisted handle.
- `external_execution_id` persisted **before** polling; a restart resumes polling and does **not** re-invoke.
- Capability gating: an unsupported action returns `UnsupportedOperation`, surfaced as an explicit "not supported by this provider" rather than a generic failure (PRD §4.11 acceptance criterion).
- **Providers are asserted to differ** where they genuinely do: a test pins the capability matrix, so a future change that silently assumes parity fails ([ARCHITECTURE.md §7.2](ARCHITECTURE.md#72-capability-matrix--providers-are-not-interchangeable)).
- Demo adapter returns `not_configured` for all writes and flags `demo_mode` on every response.
- **Adversarial:** the adapter interface has no command/script/argv parameter — asserted by signature inspection, so adding one breaks the test suite.
- One provider's outage does not affect another account's operations (isolation test).

---

## 7. Background worker tests

- Task claims a `PENDING` row with a guarded update; a second delivery of the same task exits without a second execution.
- Two workers racing one row: exactly one claim, exactly one provider invocation.
- Terminal result written with status, timestamps, `sanitized_result` and (for success) `observed_state_after`.
- **Verification mismatch ⇒ `FAILED`**, never `SUCCEEDED` (mandatory rule 8).
- **Worker crash mid-operation** (PRD §12): kill the worker; assert the reaper moves the row to `TIMED_OUT` past `deadline_at`, that the provider is **not** re-invoked, and that the row is flagged for reconciliation when no handle was recorded.
- Worker restart mid-poll resumes from the persisted handle.
- Permission revoked while the task sat in the queue ⇒ the worker refuses to invoke and records a denial.
- Approval revoked or expired before execution ⇒ the worker refuses.
- Queue isolation: a saturated `operations` queue does not delay `notifications`.
- Soft/hard time limits enforced; a task exceeding them is terminal with a reason.
- Beat schedule: each scheduled task is registered, and running two beat instances is detected as a misconfiguration.
- Task payloads contain **ids only** — a test asserts no task signature accepts a credential, command, URL or domain object.
- Celery serialisation is JSON-only; a pickle payload is rejected.
- Notification retry: transient failure → retry → success; permanent failure → `ABANDONED` after the cap, visible in the API.

---

## 8. Security tests

The gate for every mandatory rule in [SECURITY.md §1](SECURITY.md#1-mandatory-rules). A failure here blocks release.

| Rule | Test |
|---|---|
| 1, 2 — no shell | Static gate: no command-execution sink (`subprocess`, `os.system`, `eval`, `exec`, shell-invoking SSM/Run Command documents) is reachable from request data; signature inspection of adapter and task parameters; a request body with extra `command`/`script`/`args` fields ⇒ `422` |
| 3 — no secrets in VCS | Secret scan over the working tree **and** git history; a planted test secret is detected (proving the scanner works) |
| 4 — no credentials in the frontend | Built bundle scanned for secret-shaped strings and for any non-`VITE_` config value |
| 5 — no frontend-only authz | For each privileged endpoint, a direct request with a valid session but insufficient permission ⇒ `403`, identical to the UI path |
| 6 — approvals | Approval-bypass suite: direct execution attempt while `AWAITING_APPROVAL`; self-approval; approval by an out-of-scope approver; policy row missing ⇒ deny; approval expired; worker-side re-check |
| 7 — never log secrets | Canary sweep: seed unique canary values into config, provider responses, Jenkins console output and log lines, then grep **application logs, API responses, `audit_events.sanitized_metadata`, `operations.sanitized_result`, Loki content and the frontend bundle**. Any hit is a release blocker |
| 8 — verified success | Verification-mismatch test; deployment with pipeline success + failing health checks ⇒ `FAILED`; the DB `CHECK` rejects a `SUCCEEDED` row with no evidence |
| 9 — fail closed | Fault injection into session lookup, scope resolution, policy evaluation, allowlist lookup and the rate-limit backend ⇒ `401`/`403`/denial, never permissive |
| 10 — attribution | Every privileged path produces an audit row with a non-null actor; `actor_type='USER'` ⇒ `actor_id` not null (DB `CHECK`); a request that cannot be attributed is rejected |

Additional suites:

- **Authorisation matrix:** all five roles × every privileged endpoint × in-scope/out-of-scope/production-vs-non-production.
- **Revocation:** grant → request succeeds → revoke → next request fails, with no re-login and no restart.
- **Cross-scope:** another environment's server; another server's service name; another team's application; an out-of-scope approval.
- **Injection:** SQL (inventory search, filters, sorts), PromQL (template parameters, label escaping), LogQL (`contains`, service name), header injection via `X-Correlation-ID`, and markup in a log line rendered in the UI (XSS).
- **SSRF:** health-check and webhook destinations against `169.254.169.254`, `127.0.0.1`, `[::1]`, `10.0.0.0/8`, `localhost` aliases, a redirect to an internal host, DNS rebinding, and an IPv4-mapped IPv6 address. All must be refused, and an unregistered URL must never be fetched at all.
- **CSRF:** missing token, mismatched token, token from another session, cross-origin `Origin` header.
- **CORS:** configuration asserted — no wildcard, no wildcard-with-credentials.
- **Rate limiting:** each class enforced; privileged classes fail **closed** when Redis is unavailable, read classes fail open with a warning.
- **Session:** idle expiry, absolute expiry, rotation on privilege change, termination, cookie flags, no session id in any log or URL.
- **Audit immutability:** no API path updates or deletes an audit event; the app DB role cannot either.
- **Alert payload safety:** a crafted alert containing instruction-like text, an operation-shaped body, and an injected target id **cannot cause any operation to execute** and is rendered as text.
- **No debug bypass:** grep gate proving no `if DEBUG/TESTING: skip_auth`-style path exists.
- **Dependency and image scans:** `pip-audit`, `npm audit`, container scan — high/critical block.

---

## 9. Authentication and RBAC tests

- Full OIDC login round trip against the mock IdP; session created; `/auth/session` correct.
- ID-token rejection matrix: bad signature, wrong `iss`, wrong `aud`, expired, future `iat` beyond skew, wrong/missing `nonce`, `alg: none`, key-confusion attempt.
- `state` missing, mismatched, replayed; PKCE verifier mismatch.
- `next` open-redirect matrix.
- JWKS rotation: a token signed with a newly published key is accepted after refresh; an unknown `kid` does not trigger unbounded refetching.
- Disabled user cannot log in; disabling an active user terminates sessions immediately.
- Just-in-time provisioning creates exactly one user; a second login reuses it; an email change in the IdP does not create a duplicate (subject is the key).
- Logout idempotent; post-logout requests ⇒ `401`.
- Role grant/revoke endpoints: delegation escalation refused; last-Administrator revocation refused; self-disable refused.
- Scope-change propagation: a server's `environment` change immediately alters who may operate on it.

---

## 10. Frontend component tests

- `StatusBadge` renders every state with icon **and** text, so status is never colour-only (PRD §8); contrast asserted in both themes.
- **A `null` metric renders "unavailable", never `0`** — the direct regression test for PRD §4.2.
- A stale section renders the stale banner with its `collected_at`.
- A series containing `null` renders a **gap**, not a zero or an interpolated line (PRD §4.4).
- An unreachable server's service list renders `UNKNOWN`, not `INACTIVE` (PRD §4.5).
- A Windows host's systemd panel renders "not supported".
- Loading, empty, error and unavailable states for every data view.
- The operation confirmation dialog shows server, service, environment and action, and cannot be submitted without them; production targets are visually distinct.
- Controls the user lacks permission for are hidden, **and** a test asserts hiding is cosmetic by calling the API directly.
- Validation errors from `details.fields` map to the right form fields.
- Log lines containing markup render as text.
- Polling backs off and stops on a terminal status.
- `axe` accessibility assertions on every gated flow: keyboard navigation, focus trapping in dialogs, labels, and screen-reader text for status.
- Dark and light themes render every status distinctly.

---

## 11. Docker Compose smoke tests

Script-driven, run in CI and nightly:

1. `docker compose --profile core up -d` → every service reports healthy within a timeout.
2. Migrations applied; `alembic current` is at head.
3. `GET /healthz` → `200`; `GET /readyz` → `200` with all dependencies true.
4. Frontend served and reachable through the proxy.
5. Seeded inventory visible through `GET /api/v1/servers`.
6. `demo_mode: true` present and the UI banner rendered.
7. Mock IdP login completes end to end.
8. With the `observability` profile: Prometheus has the bundled exporter target `up`; a metric query returns data; Loki accepts a push and returns it.
9. Celery worker and beat are connected; the `ping` task round-trips.
10. **Stopping PostgreSQL makes `/readyz` return `503` and the API degrade with honest error states — not with zeros.**
11. `docker compose down -v` then `up` again reaches the same healthy state (no hidden local state).
12. No container runs as root; no privileged flags; resource limits present in the production overlay.

---

## 12. End-to-end tests (Playwright)

**Critical paths (run on every PR)**

1. Login → dashboard renders → logout.
2. Register a server → appears in inventory → detail page loads.
3. Server health inspection: metrics tab shows values with timestamps; a stale server is labelled stale.
4. Service status inspection: a failed service is visible from the dashboard without visiting the server.
5. Log search returns results with source and timestamp.
6. Alert acknowledgement: status becomes `ACKNOWLEDGED` and **`resolved_at` stays empty**.
7. Trigger a non-production deployment (mock Jenkins) → progress → outcome.
8. **Unauthorised action rejected:** a Viewer cannot see or perform a restart, and the direct API call fails too.
9. **Production approval enforcement:** a production operation stops at `AWAITING_APPROVAL`; the requester cannot self-approve; an approver approves; execution proceeds and the verified state is shown.

**Extended (nightly)**

10. Allowlisted service operation end to end against the demo adapter, including the confirmation dialog contents.
11. Operation history and audit log filtering.
12. Admin flows: grant a role, observe the new capability on the next request, revoke it, observe the loss.
13. Deployment failure with passing pipeline but failing health checks ⇒ shown as **failed**.
14. Rollback where supported; rollback unavailable where not.
15. Dark/light theme pass over every page.
16. Loki and Prometheus down: pages render explicit unavailable states and remain navigable.

---

## 13. Performance and load tests

Run in Phase 6 ([IMPLEMENTATION_PLAN.md M6.3](IMPLEMENTATION_PLAN.md#m63--performance-testing)) against seeded volume (target: hundreds of servers, thousands of services, hundreds of thousands of audit rows, months of partitions).

| Scenario | Budget (PRD §10, a **goal**, not a measurement) |
|---|---|
| `GET /dashboard/summary` under concurrent dashboard users | p95 < 2 s |
| `GET /servers` with filters and pagination | p95 < 2 s |
| Metric range query, 24 h at 1 min step | p95 < 5 s |
| Log query, 1 h range, limit 200 | p95 < 5 s |
| Concurrent operation submissions | no duplicate executions, no lock contention stalls |
| Alert webhook burst | ingestion keeps up; dedup holds; no unbounded row growth |

Also measured: query plans on the hot paths (index use, no sequential scans on large tables), worker throughput per queue, Redis memory under session load, and partition-pruning effectiveness on audit queries.

**Until these tests run, no performance claim may be made in any document, commit message or status report.** PRD §10 explicitly states its figures are engineering goals, not guarantees.

---

## 14. Backup and recovery tests

- Backup job produces an **encrypted** artifact outside the application host, on schedule.
- **Restore rehearsal** into an isolated environment on a regular schedule: restore, run migrations to head, start the application, verify inventory, operation history and audit continuity.
- Point-in-time recovery to a chosen timestamp where WAL archiving is configured.
- Measured restore duration recorded against the RPO/RTO targets once OPEN-07 is decided.
- PRD §15 item 13 verified directly: application metadata is recovered using the **documented** procedure, by someone following only the document.
- Audit continuity after restore: no gap is silently created; the restore itself is audited.
- Backup access control verified: the application role cannot read or delete backups.
- A corrupt or truncated backup is detected by verification, not discovered during an incident.

**An untested backup is not a backup.** This section's results are recorded with dates in `PROGRESS.md`.

---

## 15. Failure-injection tests

Every PRD §12 failure scenario, with the assertion that matters:

| Scenario | Required behaviour |
|---|---|
| Server becomes unreachable | Status transitions to `UNREACHABLE`; **services show `UNKNOWN`, not `INACTIVE`**; alert raised |
| Monitoring data becomes stale | `data_state: "stale"` with `collected_at`; dashboard stale counter increments; no value substituted |
| Exporter stops reporting | `agent_status: UNREACHABLE`; services `UNCOLLECTED`; distinct from host failure |
| systemd service fails | `FAILED` surfaced with its timestamp; alert raised; visible from the dashboard |
| Cloud provider API times out | Operation `TIMED_OUT`, **not retried blind**; reconciliation flagged; other providers unaffected |
| Cloud provider throttles | `retry_after` honoured; backoff with jitter; terminal after the cap with a clear reason |
| Jenkins unavailable | Deployment state preserved; `last_poll_error` surfaced; poller retries to a deadline then `FAILED` / `DEPLOYMENT_TIMED_OUT`; **never silently "succeeded"** |
| Loki unavailable | Explicit `unavailable` state — **never "no logs found"** |
| Prometheus unavailable | Metric sections `unavailable`; dashboard still renders inventory; **no zeros** |
| Duplicate requests arrive | Exactly one execution; replay returns the original |
| Worker crashes during an operation | Reaper times it out; no duplicate invocation; diagnostics preserved |
| Failed approvals | Rejected operations never execute; audit records the rejection and the rejector |
| Deployment failure | Clear diagnostics; health-check results shown; no false success |
| Secret leakage | Canary sweep finds nothing in logs, responses, audit rows, Loki or the bundle |
| Database migration failure | Application refuses to start on a half-migrated schema; database left consistent |
| User permissions revoked during a session | Next request reflects the revocation; a queued operation is refused by the worker |
| Redis unavailable | Sessions fail closed (re-login required); queued work is not silently lost from PostgreSQL rows; privileged rate-limit classes fail closed |
| PostgreSQL unavailable | `/readyz` `503`; honest errors; no data invented |
| Alert storm | Dedup and rate limits hold; overflow behaviour explicit and logged |
| Clock skew on a managed host | Future-dated samples do not render as healthy; skew is surfaced |

---

## 16. How to run everything

**All commands below are [to implement].** The files they depend on do not exist yet; they are specified here so the implementation creates them under these names ([IMPLEMENTATION_PLAN.md M1.1](IMPLEMENTATION_PLAN.md#m11--repository-skeleton-compose-core-health-endpoint-ci)).

### Backend **[to implement]**

```bash
cd backend
uv sync                                  # or: poetry install — TS-03 undecided
uv run ruff check .                      # lint
uv run ruff format --check .             # formatting
uv run mypy app                          # type check
uv run pytest -q                         # all tests
uv run pytest -q -m "not integration"    # unit only
uv run pytest -q -m integration          # integration (needs TEST_DATABASE_URL)
uv run pytest --cov=app --cov-report=term-missing
uv run bandit -r app                     # security lint
uv run pip-audit                         # dependency CVEs
uv run alembic upgrade head              # migrate
uv run alembic downgrade -1              # roll back one revision
```

### Frontend **[to implement]**

```bash
cd frontend
npm ci
npm run lint
npm run format:check
npm run typecheck
npm run test
npm run test:coverage
npm run build
npm audit --audit-level=high
```

### Full stack and E2E **[to implement]**

```bash
docker compose --profile core --profile observability --profile mocks up -d
./scripts/smoke-test.sh
npm run test:e2e --prefix frontend
docker compose down -v
```

### Real-integration tests — explicit opt-in, never in CI **[to implement]**

```bash
# Requires a NON-PRODUCTION cloud account and an explicitly configured profile.
# Never run against production. Results are recorded in PROGRESS.md as manual checks.
uv run pytest -q -m real_integration --integration-profile=nonprod-sandbox
```

### Everything a contributor should run before opening a PR **[to implement]**

```bash
./scripts/check-all.sh
```

**Markers:** `unit` (default), `integration` (containers required), `security`, `slow`, `real_integration` (opt-in, excluded from CI). A test lacking a marker is treated as `unit` and must not touch the network or the filesystem outside a temporary directory. Everything under `backend/tests/integration/` is marked `integration` automatically by a collection hook, so a new file there cannot be silently excluded.

### Integration tests need a database **[implemented in M1.2]**

```bash
docker compose -f infra/compose/docker-compose.yml --profile core up -d postgres redis
docker compose -f infra/compose/docker-compose.yml exec -T postgres   psql -U dcp -d postgres -c 'CREATE DATABASE dcp_test OWNER dcp;'

cd backend
export TEST_DATABASE_URL='postgresql+psycopg://dcp:placeholder-password@127.0.0.1:5433/dcp_test'
uv run pytest -m integration
```

`TEST_DATABASE_URL` is deliberately **not** `DCP_`-prefixed: that prefix is reserved for application settings, and an unrecognised `DCP_*` variable is a startup error by design, so a test-harness variable wearing it would force that guard to be loosened.

Unset, the integration tests **skip with a reason**. Set but unreachable, they **fail** — "the database was configured and did not work" is a result, not a reason to skip. CI additionally fails the build if the integration suite reports zero collected tests, because a skipped suite and a passing suite look identical in a summary.

---

## 17. Critical scenario coverage matrix

PRD §12 and the task's required scenarios, each mapped to where it is tested.

| Scenario | Level | Section |
|---|---|---|
| Unauthenticated API requests | Integration + E2E | [§5](#5-api-integration-tests), [§12](#12-end-to-end-tests-playwright) |
| Unauthorized production operations | Unit + Integration + E2E | [§3](#3-backend-unit-tests), [§8](#8-security-tests), [§12](#12-end-to-end-tests-playwright) |
| Invalid server and service identifiers | Unit + Integration | [§3](#3-backend-unit-tests), [§5](#5-api-integration-tests) |
| Failed and unreachable servers | Unit + Failure injection | [§3](#3-backend-unit-tests), [§15](#15-failure-injection-tests) |
| Stale metrics | Unit + Component + Failure injection | [§3](#3-backend-unit-tests), [§10](#10-frontend-component-tests), [§15](#15-failure-injection-tests) |
| systemd service failures | Unit + Integration + E2E | [§3](#3-backend-unit-tests), [§15](#15-failure-injection-tests) |
| Cloud provider timeouts and throttling | Adapter + Worker | [§6](#6-provider-adapter-tests), [§7](#7-background-worker-tests) |
| Jenkins outages | Integration + Failure injection | [§5](#5-api-integration-tests), [§15](#15-failure-injection-tests) |
| Loki / Prometheus unavailability | Integration + Component + Smoke | [§5](#5-api-integration-tests), [§10](#10-frontend-component-tests), [§11](#11-docker-compose-smoke-tests) |
| Duplicate operation requests | Unit + Integration + Worker + DB | [§4](#4-database-and-migration-tests), [§7](#7-background-worker-tests) |
| Worker crashes | Worker + Failure injection | [§7](#7-background-worker-tests), [§15](#15-failure-injection-tests) |
| Failed approvals | Integration + Security | [§8](#8-security-tests), [§12](#12-end-to-end-tests-playwright) |
| Deployment failures | Unit + Integration + E2E | [§3](#3-backend-unit-tests), [§12](#12-end-to-end-tests-playwright) |
| Secret leakage | Security canary sweep | [§8](#8-security-tests) |
| Database migration failures | Migration | [§4](#4-database-and-migration-tests) |
| Permissions revoked mid-session | Integration + Worker | [§9](#9-authentication-and-rbac-tests), [§7](#7-background-worker-tests) |
| Backup and recovery | Recovery rehearsal | [§14](#14-backup-and-recovery-tests) |

---

## 18. CI gates

Every push: lint, format, type check, backend unit + integration, frontend unit, frontend build, security suite, secret scan, dependency audits, image build + scan, route-coverage meta-test, schema-drift check, OpenAPI/TypeScript regeneration check, documentation link check.

Every PR: the above plus critical-path E2E and the Compose smoke test.

Nightly: full E2E, extended failure injection, longer soak on the worker queues.

**Blocking, with no exception process:** any security-suite failure, any secret-scan finding, any high/critical dependency or image CVE, any migration-test failure, any route lacking authorisation coverage.

---

## 19. Coverage

Coverage is a signal, not a target. The floor applies to **changed code** (initially 80 %), with specific areas held higher because their edge cases are security-relevant: the permission matcher, both state machines, service-state normalisation, redaction, and idempotency resolution are expected to be exhaustively covered by table-driven tests. A high overall percentage with an untested approval-bypass path is a failing test suite regardless of the number.

---

## 20. Open decisions and assumptions

| ID | Question | Working default |
|---|---|---|
| TS-03 | Python package manager, which fixes the command prefix in §16 | Any, with a committed lockfile; commands shown with `uv` |
| OPEN-22 | Coverage floor and whether it gates per-file or per-diff | 80 % on changed code |
| OPEN-23 | Who owns the non-production sandbox for real-integration tests, and its lifecycle | Unassigned — needed before M4.3 |
| OPEN-24 | Load-test tooling (k6, Locust, Artillery) | Undecided; Phase 6 |
| OPEN-25 | Mutation testing on the permission matcher and state machines | Deferred; **[RECOMMENDED]** |

**Assumptions**

1. CI can run Docker containers (PostgreSQL, Redis, Prometheus, Loki, mocks).
2. No test requires production access or production credentials, ever.
3. Mock IdP and mock Jenkins are maintained as part of the repository, since the automated suite depends on them.
4. Real-integration results are recorded as dated manual checks, never as automated passes.

**No test in this document has been written or executed.** [`../PROGRESS.md`](../PROGRESS.md) records the actual state.
