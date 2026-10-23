# Testing and Verification — DevOps Control Plane

**Status:** Partly implemented. As of **M1.7** the backend and frontend suites, the Compose smoke test (§11) and the first end-to-end tests (§12) all exist and have been run; their recorded results are in [`PROGRESS.md`](../PROGRESS.md) §7. Sections still marked **[to implement]** describe tests whose subject does not exist yet — metrics, services, logs, alerts, deployments and approvals all arrive from Phase 2 onward.
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
| Jenkins | Mock HTTP server replaying realistic queue→build responses. **As built (M5.1, `OPEN-128`, `OPEN-130`):** `infra/compose/mock-jenkins/` (standard library, a WSGI app). Two layers: the client is driven **in process** against that app on an injected `httpx.WSGITransport` (the checked-address path refuses loopback, so a real loopback server cannot be used), covering trigger, queue→build, status, console, outage, slow, crumb, lost queue item, redirect and the circuit breaker; and `scripts/smoke_jenkins.py` drives it **over the real network path** inside the Compose stack, including the allowlist refusing an unlisted host. Passing against either is not evidence about a real Jenkins | Non-production Jenkins with a test pipeline |
| Prometheus | **Real container** with seeded series (easier and more faithful than mocking PromQL) | — |
| Loki | **Real container** with pushed test logs | — |
| Alertmanager | Real container, plus direct webhook posts for edge cases | — |
| Secrets manager | In-memory fake behind the same interface | Real store, manual |
| Notification channels | `infra/compose/notification-sink`: a local HTTP sink + SMTP sink with switchable modes (`ok`, `unavailable`, `notfound`, `redirect`, `ratelimited`), **visibly synthetic** (M3.4) | Manual, to a test destination |

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
- **Adversarial:** the adapter interface has no command/script/argv parameter — asserted by signature inspection, so adding one breaks the test suite. Every parameter annotation must be one of a closed set of types (no `str`, `dict`, `Any`, `*args`, `**kwargs`), so a new parameter has to be added to that set on purpose.

**What M4.1 tests, and what it cannot yet (`OPEN-61`, `OPEN-63`, `OPEN-65`).**
- A parametrised **conformance harness** every adapter must pass, plus a deliberately violating adapter that the harness must *fail* (a harness nothing can fail proves nothing). The test-only `ScriptedAdapter` lives under `tests/providers/`, never in `app/`.
- The "full operation lifecycle against `DemoAdapter`" in IMPLEMENTATION_PLAN is the **adapter** lifecycle (capabilities → server info → connectivity → services → execute → status): the demonstration adapter answers the first two and refuses the rest with `not_configured`, and a refused write leaves no handle. Request → `SUCCEEDED` with `observed_state_after` is the state machine's lifecycle (M4.2) and runs there against the test fake.
- **Deferred with M4.4 (`OPEN-92`): there is no second real adapter, so the disagreement assertions stay unwritten.** "Providers are asserted to differ" needs two real adapters. M4.1 pins the demonstration column of the §7.2 matrix and builds the comparison harness; the disagreement assertions arrive with M4.4. It is not claimed before then.
- `scripts/check_provider_branching.py` (in `check-all.sh`): no provider token outside `app/providers/` except in a reviewed, pinned allowlist; a planted violation must fail it.
- One provider's outage does not affect another account's operations (isolation test).

**What M4.3 tests for AWS (`OPEN-90`).** `botocore.stub.Stubber` at the botocore boundary (no `moto`): the full error matrix (success, throttle with and without `retry_after`, authentication, authorisation, not found, timeout, unavailable, a failed invocation), a write client that cannot retry, `Throttled` retried at most three times, static-key refusal, the capability column pinned to ARCHITECTURE §7.2, and parser tests on the committed SSM document and IAM policy (each shown to fail against a weakened copy). **None of this proves AWS accepts the document or the calls**; that is the real-integration check below.

---

## 7. Background worker tests

- Task claims a `PENDING` row with a guarded update; a second delivery of the same task exits without a second execution.
- Two workers racing one row: exactly one claim, exactly one provider invocation.
- Terminal result written with status, timestamps, `sanitized_result` and (for success) `observed_state_after`.
- **Verification mismatch ⇒ `FAILED`**, never `SUCCEEDED` (mandatory rule 8).
- **Worker crash mid-operation** (PRD §12): kill the worker; assert the reaper moves the row to `TIMED_OUT` past `deadline_at`, that the provider is **not** re-invoked, and that the row is flagged for reconciliation when no handle was recorded.
- **Built in M4.3 (`OPEN-84`):** a killed worker with a recorded handle is resumed by the healer's `resume` task without a second `_submit`; two resumers produce one winner; a row without a handle still times out with `reconciliation_required`. *(The sentence below describes M4.2.)* Worker restart mid-poll resumes from the persisted handle. **Moved to M4.3 (`OPEN-70`):** M4.2 has no resume (it needs a real provider to resume against). In M4.2 a row left `RUNNING` by a killed worker is timed out by the reaper with `reconciliation_required`, the provider is not re-invoked, and the persisted handle is kept for an operator.
- The reaper times out an unclaimed `PENDING` / `APPROVED` row past `deadline_at` (`EXPIRED_BEFORE_START`) without calling a provider; the healer re-enqueues a row whose queue message was lost.
- Every `SUCCEEDED` is refused by the state machine without a verification result **and** by `ck_operations_verified_success` without `observed_state_after`; a mutation that removes either guard alone fails a named test.
- A row needing approval without an approved decision, a re-labelled server, a disabled account and a policy that no longer allows the action are each refused by the worker with zero provider invocations.
- Permission revoked while the task sat in the queue ⇒ the worker refuses to invoke and records a denial.
- Approval revoked or expired before execution ⇒ the worker refuses.
- **The approval bypass suite (M4.5, `OPEN-99`):** each refused by the worker with **zero provider invocations** and `failure_reason = APPROVAL_REQUIRED` — a row set to `approval_status = 'APPROVED'` in SQL with no `approvals` row; a row approved by its own requester under a no-self-approval policy; an approver whose role or permission was revoked, or who was disabled, after deciding; an approval decided after its window; an `approvals` row that is `REJECTED` or `EXPIRED`; an approval row belonging to another operation or requester; a row that needed no approval when requested and is now governed by a policy. The resumer runs the same refusals. **Mutation checks** (a named test must fail with each change, then the code is restored and diffed): the evidence check reduced to the flag; the self-approval comparison removed; the scope check removed; the guarded `WHERE decision = 'PENDING'` removed.
- **Approval lifecycle (M4.5):** request → queue → approve → worker → `SUCCEEDED`; reject → no execution; expire → `CANCELLED` and the slot freed; approve twice ⇒ `409`; approve after expiry ⇒ `409 approval_expired`; an approver for staging cannot approve production and cannot see it; a self-approval is refused and audited; a reject without a reason is `422`; decide-versus-cancel and decide-versus-sweep have exactly one winner; a replayed request returns the waiting operation; the pending-approval alert opens and resolves; the route-coverage meta-test covers 22.1 – 22.4 and 25.11 – 25.14.
- **Approval policy editor (M4.5, `OPEN-105`):** create / edit / delete each audited `APPROVAL_POLICY_CHANGED` with before and after; a non-Administrator is `403`; `required_approvals` other than 1, an unknown role, a window outside 60 – 86 400 and an unknown field are `422`; a referenced policy cannot be deleted; a duplicate name is `409`.
- **Operations history (M4.6, `OPEN-107`, `OPEN-108`):** a requester always sees their own; an `operation:read` holder sees only operations whose **stored** environment and server they reach (a re-labelled server does not move an operation into or out of scope — and §17.2 agrees with the list); rows outside scope are never counted or paged; every filter; `from` / `to` defaults and the 90-day `422`; `sort=completed_at` with unfinished rows last and tied values straddling a page boundary (no repeat, no skip); §23.2 is `404` without `server:read`; the list carries `external_execution_id` and `observed_state_*` and never `sanitized_result`; the dashboard's `recent_operations` uses the same predicate and `[]` is distinct from `not_configured`.
- **Audit read (M4.6, `OPEN-109`, `OPEN-110`, `OPEN-111`):** `audit:read` is Approver and Administrator only; a production-scoped Approver sees production events and not events with no environment, while the bootstrap-shaped Administrator (all four environments named) sees them; a `server`-scoped grant sees no audit row; the SQL predicate is cross-checked against the matcher in the same file as `readable_servers`; an out-of-scope or unknown id is `404`; every `GET` writes `AUDIT_QUERIED` with filter values and counts and no rows, an empty result included, and a forced failure of that write fails the read; metadata is redacted again on read (a row stored with a raw canary is returned redacted); the window rules; paging across ties; one `EXPLAIN` on a seeded multi-month table shows partitions pruned to the window.
- **Audit export (M4.6, `OPEN-112`):** request → task → `READY` → one download → `RETRIEVED` with the content deleted; a window over 31 days is `422`; over 100 000 rows or 64 MiB ends `FAILED` / `export_too_large` and **never truncates**; one active export per requester (`409`); a requester demoted between request and task gets `FAILED` / `requester_not_authorised` and no data; the file contains only events the requester's scope reaches; a stranger's id is `404`; **two downloads race six times and, deterministically, a second session blocks on the first's uncommitted download and loses**; an expired export is never served; the task signature accepts an id and nothing else; `AUDIT_EXPORT_REQUESTED`, `AUDIT_EXPORTED` (at the download), `AUDIT_EXPORT_FAILED` and `AUDIT_EXPORT_EXPIRED` each written once. Mutation check: the guarded `WHERE status = 'READY'` removed from the download statement fails a named test.
- **Audit immutability (M4.6, `OPEN-113`):** no route under `/audit-events` other than `GET` and the export `POST`; `PATCH` / `PUT` / `DELETE` are `405`; `UPDATE`, `DELETE` and `TRUNCATE` are refused through both of the application's engines (sync and async), the row unchanged afterwards; a source gate (with a test that it matches what it is looking for) shows no application code issues anything but an `INSERT` against `audit_events`; a **strict `xfail`** records that a partition `DETACH` is *not* refused today and turns red the day M6.2 makes it so. The tests say in words that they do not show a least-privilege role would refuse the same.
- **Audit completeness and secrets (M4.6, `OPEN-114`, `OPEN-115`):** a registry (`tests/audit_registry.py`) checked against the live routing table (a new state-changing route without an entry fails; stale entries fail; every action named is one the code can write; every "covered elsewhere" claim names a test that exists and mentions the action; the count of each kind is pinned); each *driven* route (28) is called with a correlation id of its own and the events carrying that id are read back — expected actions present and successful, every `USER` row written by the caller; the eight routes that need a stubbed AWS layer or a seeded alert are covered by named existing tests; one route shows a failed audit insert failing the request and changing nothing; the canary sweep plants strings no redaction rule would catch in the free text of privileged requests and in a secret channel destination, then reads every column of every audit row written and the export file: an operation reason, a rejecter's reason and a destination must be absent, and the two fields recorded by design (the incident switch's reason, an inventory edit's before and after) must hold any secret-shaped value masked. **Mutation checks** (a named test must fail, then the code is restored and diffed): an `audit.record` removed from one handler; the scope predicate removed from §24.1; the re-redaction removed from the read path; `AUDIT_QUERIED` removed.
- **Frontend (M4.6, `OPEN-117`):** the Operations page sends filters as typed (only after Apply), repeats list parameters, turns a `datetime-local` into an instant with an offset, narrows to "my requests" by id, pages by the cursor it was given, renders a success with its read-back state and its execution id, a timeout as an unknown outcome, an approval with its decider, and markup in a name as text; the Audit log asks once to load and **never again on a timer, on focus or to open a detail**, refuses a non-token action before sending it, renders metadata containing markup inert, says why an empty answer is not "nothing happened", and offers the export only with `admin:integrations`; the export dialog states its rules before the click, keeps one idempotency key per dialog, polls only until the build ends, spends its download button on first use (a double click is one download), and says each failure code in words; axe on the open detail panel and the open dialog.
- Queue isolation: a saturated `operations` queue does not delay `notifications`.
- Soft/hard time limits enforced; a task exceeding them is terminal with a reason.
- Beat schedule: each scheduled task is registered, and running two beat instances is detected as a misconfiguration.
- Task payloads contain **ids only** — a test asserts no task signature accepts a credential, command, URL or domain object.
- Celery serialisation is JSON-only; a pickle payload is rejected.
- Notification retry: transient failure → retry → success; transient failures past the cap → `ABANDONED`; a permanent failure → `FAILED` at once (a refused destination is not retried, `OPEN-53`); both visible in the API and manually retryable.

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

**Implemented as [`scripts/smoke-test.sh`](../scripts/smoke-test.sh) and passing as of M1.7**, after two faults it exposed were fixed: the worker's healthcheck never passed (issue 49) and *nothing applied the migrations*, so a fresh volume produced a healthy-looking stack with no schema. The script's steps are numbered to match this list.

**Step 8's Prometheus half is asserted as of M2.1**, which is what turned `prometheus` and `node-exporter` from Compose placeholders into configured, scraped services. Script step 18 starts the `observability` profile and then, *inside the backend container and through `app/metrics/client.py`*, waits for `up{job="node"}` to reach 1, then queries **all ten templates**. Five are asserted to return a value in range (`agent_up`, `memory_utilisation`, `uptime_seconds`, `load_average`, `cpu_utilisation`); the other five are asserted only to be **valid PromQL**, with whatever value they produce printed. That split is deliberate: `node-exporter` runs in a container with no host mounts, so which filesystems, disks and interfaces exist is an environment detail — all five answer on the development machine, but a runner with a different storage driver could legitimately expose none of them. Prometheus answers a syntactically invalid query with `400`, which the client maps to `provider_error`, so "no exception" *is* the syntax assertion, and syntax is the real risk for exactly those five. Running it through the real client rather than through `curl` is deliberate: a hand-written query that happens to work proves nothing about the code the API uses, and it is also the only check that `DCP_PROMETHEUS_URL` actually reaches the backend — the half [`scripts/check_env_example.py`](../scripts/check_env_example.py) structurally cannot see (PROGRESS issue 54). The same step asserts the two honest-absence paths: an unscraped instance returns **no series rather than a zero**, and a `WINDOWS` render raises `unsupported` rather than producing a Linux query (`OPEN-09`).

**Step 18 also proves M2.3's acceptance criteria (2026-10-16).** `node-exporter` is no longer a static target, so the step first registers it through the API and then asserts, in order: the worker's target file lists it (and lists neither a seeded `[DEMO]` server nor any free text), the worker actually sees the tightened probe interval and unreachable window (the Compose-passthrough half that `scripts/check_env_example.py` cannot see), the probe marks it `HEALTHY` with a real `last_seen_at`, the seeded demo servers stay `UNKNOWN`, **stopping the exporter makes it `UNREACHABLE`**, and starting it again makes it `HEALTHY`. Traps the script now avoids and a future edit could reintroduce: Git Bash on Windows rewrites a bare `/var/...` argument into a Windows path (hence `sh -c '...'`); a `grep -o` that matches nothing ends the script silently under `set -e`; and `grep -q` at the end of a pipe under `set -o pipefail` can report a successful match as a failure.

**Step 18a — the status sweep and host health (M2.5, AD-14) — goes beyond §11.** It runs on the host step 18 registered, with `DCP_HEALTH_THRESHOLDS` tightened for the run to 30 / 45 / 75 s (exported at the top of the script and passed through Compose, which is also what proves the passthrough). It asserts, in order:

1. an administrator accepts the registration through the API (`PENDING`→`MANAGED`; the sweep never touches `PENDING`);
2. **the beat entry is real** — a `status_swept` line appears in the *worker container's* log, which only the scheduled task writes (a by-hand call logs in its own process);
3. the host is `healthy` and not stale, and the dashboard rollup is `ok`, counts it, and **sums to the server total**;
4. stopping the exporter makes it **`STALE`, then `UNREACHABLE`**, with the server detail's `health.state` agreeing each time, exactly **two** audited `SERVER_STATUS_CHANGED` moves (`MANAGED>STALE,STALE>UNREACHABLE`, by `system:health-sweep`), `updated_at` **unchanged** (migration 0006: otherwise an open edit form would answer `409` because a host went quiet), and the dashboard's `recent_health_changes` listing the move;
5. starting it again returns `MANAGED` and `healthy`, a third audited move;
6. **a Prometheus outage ages nothing** (issue 67): Prometheus is stopped for 90 s — longer than the 75 s `unreachable` line — with the sweep run by hand throughout; the host stays `MANAGED`, no move is audited, and the server detail reports health as **not evaluated and dated** (`state: null`, `data_state: stale`), not a frozen verdict; when Prometheus returns the host is `healthy` again with no extra move.

The sweep is run *by hand* inside the polling loops (`sweep_status()` in the worker container) so each transition is bounded by the thresholds and not by the one-minute schedule; the schedule itself is proven by (2). The lines are not tighter than this on purpose: a healthy host is last seen up to one scrape interval (15 s) plus one probe interval (10 s) before the reference, so a line inside ~25 s would flap a good host.

**Step 18b — systemd unit state against a real systemd (M2.4, `TS-02`) — goes beyond §11.** The ordinary `node-exporter` container has no system bus, so nothing in the normal stack can produce `node_systemd_*` series. Step 18b starts `systemd-host` (profile `fixtures`; [`infra/compose/systemd-host/`](../infra/compose/systemd-host/Dockerfile)): Debian with systemd as PID 1 and the *same pinned* `node_exporter` binary, copied from the image the normal stack runs, with its systemd collector enabled and a unit allowlist. It registers the host and four units through the API and drives real transitions, asserting what the **portal** says through the authenticated API:

| Action on the fixture | Asserted result |
|---|---|
| a unit that fails at boot | `FAILED` |
| `systemctl kill -s KILL` a running unit | `FAILED` (a clean `systemctl stop` of another is `INACTIVE` — two facts, two states) |
| a registered unit the host does not have | `UNCOLLECTED`, never `INACTIVE` |
| stop the system bus (host answers, collector fails) | `UNCOLLECTED`, `server_reachable: true`; back to `FAILED` when the bus returns |
| stop the host | `UNKNOWN` for **all four** units on the detail page and in the list; none `INACTIVE` or `FAILED` (PRD §4.5) |
| the list and the dashboard after the refresh job | the failed-service view lists exactly the two failed units; the dashboard counts them |
| an unregistered unit (`systemd-journald.service`) | `404`, and never queried |
| the unit fields this source cannot supply | `sub_state`, `load_state`, `enabled`, `main_pid` all `null` (`OPEN-29`) |

**"Stop the exporter" and "stop the host" are one observable.** The plan's M2.4 test list asks for both, expecting `UNCOLLECTED` for the first and `UNKNOWN` for the second. With the systemd collector inside `node_exporter` there is no separate systemd exporter to stop, so a stopped exporter *is* an unreachable host to Prometheus, and the result is `UNKNOWN`. What the plan meant by `UNCOLLECTED` — the host answers and has nothing about the unit — is the collector failing, which stopping the system bus produces. The step asserts that instead, and the substitution is recorded here rather than left implicit.

`SMOKE_SYSTEMD=auto|require|skip` (default `auto`). The fixture needs `SYS_ADMIN` and a writable cgroup tree ([SECURITY.md §7](SECURITY.md#7-network-isolation)), which not every Docker host grants. `auto` runs the step and, if the fixture cannot start, says so loudly and ends with **`SMOKE TEST PASSED, BUT INCOMPLETE: 1 step(s) NOT RUN`** — never a plain pass. `require` makes that a failure; `skip` omits it and reports the same. Once the fixture is up, every assertion must hold in every mode. The fallback coverage for a host that cannot run it is the unit and integration suites (`tests/services/`, `tests/integration/test_services_api.py`, `test_services_refresh.py`), which assert the same rules against scripted Prometheus answers — and **scripted answers prove the portal's reading of a series, not that a real systemd produces it**; that is exactly what this step adds.

**Step 8's Loki half is asserted as of M3.1 (step 18c):** a push with the seven allowed labels is accepted and comes back from a query carrying exactly those labels; an eighth label and an 8-day-old sample are each rejected with `400`; every label Loki holds is on the allowlist (and `service_name`, which Loki 3 adds by itself, is not among them); retention reads back as 14 days with the compactor on. What it does **not** yet show is Alloy shipping a file through to Loki (M3.1 commit 3). `grafana` still has no dashboards, so the script starts it nowhere.

**Step 18d (M3.1) drives the real log pipeline.** The `sample-logs` producer writes a managed server's layout (Nginx, PHP-FPM, syslog, a JSON application log, Docker `json-file`) with the canary secrets of `infra/compose/sample-logs/canaries.json` in every source; a real Alloy ships it to a real Loki; `scripts/smoke_logs.py ingestion` (run inside the producer's container, so it uses the same corpus) reads Loki back and asserts: Alloy runs non-root, publishes no port, is not privileged and has no Docker socket (AD-22); every label Loki holds is on the seven-label allowlist and `filename`/`job`/`service_name` are not among them; every stream carries exactly the required labels with `application_id` on the application source only; thirteen severity mappings (`err`→`error`, `fatal`→`critical`, `TRACE`, a hostile level and an access log → `unknown`); a container named `Weird Name; DROP TABLE` is `service="other"`; **eight canary secrets are absent from Loki in every source while the `[REDACTED]` marker is present in all five** (the pairing is deliberate: absence alone is also what a dead pipeline looks like); the multi-line PEM canary **does** leak, pinned so SECURITY.md §17's limit cannot drift; redacted JSON lines still parse. Then four throwaway Alloys with a malformed `server_id`, `environment`, `hostname` and `application_id` each assert the **drop counter** (`loki_process_dropped_lines_total{reason=…}`) before asserting Loki's silence — the first draft asserted silence alone and passed vacuously on a container that never started (PROGRESS, M3.1 commit 3). The last case is a positive control: the other sources under that hostname *do* arrive, only `application` does not.

**Step 18e (M3.1) reads a real systemd journal.** The `systemd-host` fixture writes a persistent journal to a volume; `alloy-journal` reads it through the same pipeline. Asserted: only allowed labels and closed values, no `application_id`, a real unit (`node-exporter.service`) keeps its name and a non-unit entry is `other`, `PRIORITY` maps (`err`→`error`, `info`→`info`), and a canary logged with `logger` is absent while its line carries the marker. It needs the same `SYS_ADMIN` fixture as 18b and follows `SMOKE_SYSTEMD`: `skip` and an unstartable fixture under `auto` list it as **NOT RUN** (the journal source is then configured but unobserved), `require` fails.

**Step 18f (M3.2) drives the log search API against the real pipeline and a real Loki.** `python -m app.inventory.seed --sample-log-host` registers the sample shipper's identity as a server (fixed ids, `[DEMO]`, hostname `sample-log-host.demo.invalid`, deliberately unlike the label the shipper sends); `scripts/smoke_logs_api.py search` (run inside the producer's container, as the SPA would call: session cookie and CSRF token, straight to the backend) asserts: `GET /logs/sources` and Loki's own `retention_period` agree on 14 days (`OPEN-37`); lines that really came through Alloy arrive carrying the **inventory's** hostname, never the shipper's label, and the shipper is `receiving`; each of the five file sources is searchable on its own; the hostile container is `other`; no canary is in the API's answer and the marker is. **Second layer:** every canary is pushed **raw** into Loki, bypassing Alloy, and none comes back through the portal (the marker does) while the multi-line PEM block does, pinned. A `contains` of quotes, backslashes, braces and a LogQL fragment matches literally in a real Loki; **all eight presets** are accepted and each returns exactly its own lines; `contains` and a preset are ANDed; **paging with a tie group of seven lines sharing one nanosecond across two streams** is run at sizes 1–9 in both directions and must equal the one-shot result with no repeat; an unknown server id is dropped and counted; a registered server that never shipped is `silent` and its empty page says so; fields that could carry LogQL and an unbounded query are `422`. Then Loki is **stopped**: the API must answer `503 provider_unavailable` with nothing that could read as an empty result, and after Loki returns the same query works (`recovered`). It does not need the systemd fixture.

**Step 18h (M3.4) runs notification delivery on 18g's real alert, to the `notification-sink` mock -- which is not Slack, Teams or a mail server.** Two channels are registered before the exporter stops (a WEBHOOK by reference `env://NOTIFY_SINK`, routed to `CRITICAL` in `staging`, and an EMAIL one through the sink's SMTP port), so the alert Prometheus and Alertmanager really produce is the one notified. `scripts/smoke_alerts.py` modes: **`notify_register`** -- both channels are created, the API returned the reference and no URL, a pasted URL is `422` without being echoed, and both integration rows are `degraded` (`no_delivery_attempted_yet`), not `ok`; **`notify_failures`** -- with the sink answering `503` (and SMTP `451`) each delivery is attempted four times (`DCP_NOTIFICATION_RETRY_DELAYS_SECONDS=5,5,5` for this run) and then `ABANDONED` with `http_503` / `smtp_busy`; the sink saw exactly four posts, each with the fixed `dcp.alert.v1` shape, the alert's id, `CRITICAL`, the registered `Host` and a link built from `DCP_PUBLIC_BASE_URL`; both rows are `unavailable` and name no address; **`notify_retry`** -- with the sink answering a redirect, a manual retry ends `FAILED` / `redirect_refused` after **one** attempt (a permanent failure, not followed, not retried: the sink's count rises by exactly one over 35 s); with the sink `ok`, a manual retry of the `FAILED` and of the `ABANDONED` delivery both end `SENT`, the sink holds exactly one HTTP message and one mail (`Subject: [DCP] [CRITICAL] HostNotReporting (staging)`), retrying a `SENT` delivery is `409`, and both rows are `ok`; **`notify_quiet`** -- after the source resolves the alert, nothing more is sent for 50 s (`OPEN-51`). The SSRF lists, the Docker bridge ranges, the retry delays, the SMTP settings and `DCP_PUBLIC_BASE_URL` are exported by the script and passed through Compose, which also proves the worker sees them.

**Step 18g (M3.3) runs alerting through a real rule file, a real Prometheus and a real Alertmanager into the webhook.** The rule file is generated **inside the backend image** with this run's thresholds (`DCP_HEALTH_THRESHOLDS`, so "stopped reporting" fires after 45 s, not 5 minutes) and mounted in place of the committed one through `ALERT_RULES_DIR`. `scripts/smoke_alerts.py` (inside the producer's container, as the SPA would call, plus Redis and Prometheus over the project network) asserts, in modes the shell interleaves with real stops and starts: **`pipeline`** -- Prometheus loaded the generated groups and every rule is healthy; the always-firing `DcpWatchdog` travelled Prometheus -> Alertmanager -> webhook, so the integration page's Alertmanager row is `ok` and the dashboard's `pipeline` is `confirmed`; **no row exists for the watchdog**; a missing and a wrong secret are each `401`, each **counted** (`dcp:alerts:refused:bad_secret:<day>` rises by exactly 2) and each stores nothing. **`down`** (the exporter stopped) -- a real `HostNotReporting` alert row appears, `CRITICAL`, `staging`, linked to the right server with its display name; a repeat posted to the real endpoint with the real secret is counted on the **same** row (Alertmanager re-notifies only every `repeat_interval`, 1 h, so the repeat cannot be waited for -- this proves the endpoint's deduplication, not Alertmanager's scheduling, and the script says so); acknowledging leaves it open, `resolved_at` null, and it shows on the dashboard and the server page. **`recovered`** (the exporter back) -- the **source** resolves it; who acknowledged it and the timeline are kept; the server page counts none open. **`outage`** (Alertmanager stopped) -- the integration row is `unavailable` with a closed reason code and no address in it (`alertmanager_timeout` for a stopped container on the Docker network, `alertmanager_unreachable` for a refused connection -- the first real run expected only the second, and the harness now accepts either); **`restored`** -- `ok` again. A source address outside the allowlist cannot be exercised here (every Compose peer is a private address) and is covered by `tests/alerts/test_webhook_unit.py` and `tests/integration/test_alert_webhook.py`. It does not need the systemd fixture.

**The rule file's own checks (M3.3, in `check-all.sh`).** `gen_alert_rules.py --check` fails when `infra/prometheus/rules/dcp-alerts.yml` is stale against the two threshold settings and the metric catalogue; `promtool check rules` and `promtool test rules` (the `prom/prometheus` image carries `promtool`, so they need Docker and are listed as skipped where it is missing) run the **real** file against seeded series in `infra/prometheus/tests/dcp-alerts.test.yml`: each of CPU, memory and disk fires its warning at the line and **not** its critical (mutually exclusive), a value just under the line fires nothing, a full `tmpfs` does not count as disk, a failed `*.timer` is not a failed service, one environment's series never fire another's rules, and the watchdog always fires. Mutation-checked: replacing `unless` with `or`, or dropping the environment matcher from a rule, each fails the suite.

**Unit-level tie between the two redaction layers:** `backend/tests/test_canary_corpus.py` runs the same corpus through `redact_text` *and* through the `stage.replace` expressions parsed out of `config.alloy`, applied as Alloy applies them (only the capture group is replaced). Python's `re` is not RE2, so this is a fast tripwire and not proof — the proof is step 18d. Mutation-checked: deleting the AWS stage from `config.alloy`, and dropping `Bearer` from the backend, each fail the expected tests. Step 12's "resource limits in the production overlay" is partial: no production overlay exists yet, so what exists is checked instead (non-root, no privileged flag, no added capabilities, limits declared in the file we have).

Script-driven, run in CI and nightly:

1. `docker compose --profile core up -d` → every service reports healthy within a timeout.
2. Migrations applied; `alembic current` is at head.
3. `GET /healthz` → `200`; `GET /readyz` → `200` with all dependencies true.
4. Frontend served and reachable through the proxy.
5. Seeded inventory visible through `GET /api/v1/servers`.
6. `demo_mode: true` present and the UI banner rendered.
7. Mock IdP login completes end to end.
8. With the `observability` profile: Prometheus has the bundled exporter target `up`; a metric query returns data **(both asserted since M2.1)**; Loki accepts a push and returns it **(asserted since M3.1, step 18c)**.
9. Celery worker and beat are connected; the `ping` task round-trips.
10. **Stopping PostgreSQL makes `/readyz` return `503` and the API degrade with honest error states — not with zeros.**
11. `docker compose down -v` then `up` again reaches the same healthy state (no hidden local state).
12. No container runs as root; no privileged flags; resource limits present in the production overlay.

---

## 12. End-to-end tests (Playwright)

**Paths 1, 2 and 8 are implemented as of M1.7** — `frontend/e2e/critical-path.spec.ts` and `frontend/e2e/authorisation.spec.ts`, run by `npm run test:e2e` against the Compose stack and by the `e2e` CI job. Paths 3–7 and 9 are **not** stubbed: each needs metrics, services, logs, alerts, deployments or approvals, and a test named for a feature that does not exist would report a working feature.

Two things the implementation settled, both worth knowing before adding to the suite:

- **The suite seeds its own preconditions and is idempotent** — synthetic inventory, an administrator through the bootstrap CLI, then a Viewer granted `VIEWER`. It reuses saved sessions when they still authenticate, because signing in is rate limited (the `/auth/*` class, 10 per 300 s, failing closed) and a setup that signed in unconditionally began answering `429` after a few local re-runs.
- **A direct API assertion must send an `Origin` header.** `app/core/csrf.py` checks origin *before* the route's permission dependency and also answers `403 forbidden`, so a `403` assertion on a request without one passes on a CSRF rejection and proves nothing about permissions. Path 8's helper asserts the refusal is *not* a CSRF refusal for that reason.

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

**The commands below exist and have been run** as of M1.7, except where a line says otherwise (`test:e2e` for paths that need Phase 2, and the real-integration profile, which has no sandbox — `OPEN-23`). They were originally specified here before any code existed, which is why the names were already fixed.

### Backend

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

### Frontend

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
npm run generate:api                     # regenerate src/api/schema.ts from the backend
```

`generate:api` runs [`scripts/generate-api-types.sh`](../scripts/generate-api-types.sh), which reads the OpenAPI schema out of the backend (`python -m app.openapi`, no server and no database) and writes `frontend/src/api/schema.ts`. It needs **both** toolchains, `uv` and `npm`, because the contract has two ends.

Run it in the same commit as any change to a route or a response model. The `contracts` CI job and `scripts/check-all.sh` both regenerate and fail if the committed file moves ([TECH_STACK.md §11](TECH_STACK.md#11-contract-synchronisation)); the fix is to run it and commit the result. Never hand-edit `schema.ts`.

### Full stack and E2E

```bash
# Brings the stack up itself, asserts §11, and tears it down (KEEP_STACK=1 to leave it).
./scripts/smoke-test.sh

# The E2E suite runs against an already-running stack and does not start one.
docker compose --env-file .env -f infra/compose/docker-compose.yml \
  --profile core --profile mocks up -d --wait
npm run test:e2e --prefix frontend
docker compose -f infra/compose/docker-compose.yml --profile core --profile mocks down -v
```

**`--env-file .env` matters.** Compose takes its project directory from the compose file's location, so with `-f infra/compose/docker-compose.yml` it reads `infra/compose/.env` and ignores the one at the repository root. Every `${VAR}` has a default, so the stack works either way — which is why this went unnoticed until M1.7 — but without the flag anything set in the root `.env` is silently discarded.

Two settings are worth overriding for E2E, and both are passed through by the compose file:

```bash
DCP_DASHBOARD_CACHE_TTL_SECONDS=0   # issue 50: a dashboard aggregate is cached for 15s
DCP_AUTH_RATE_LIMIT_MAX=100         # the suite signs in legitimately, several times per run
```

**Never run two pytest sessions against `dcp_test` at once**, and do not run the smoke test beside one: the session-scoped `migrated` fixture drops the schema under the other session, and `scripts/smoke-test.sh` ends in `docker compose down -v`.

### Real-integration tests — explicit opt-in, never in CI **[to implement]**

```bash
# Requires a NON-PRODUCTION cloud account and an explicitly configured profile.
# Never run against production. Results are recorded in PROGRESS.md as manual checks.
uv run pytest -q -m real_integration --integration-profile=nonprod-sandbox
```

**As built in M4.3 (`OPEN-90`):** the `real_integration` marker exists and is excluded from the default run and from CI; without `--integration-profile` its tests skip with a reason. The runbook is in `infra/aws/README.md`. The result is a dated manual check in `PROGRESS.md`, never an automated pass.

### Everything a contributor should run before opening a PR

```bash
./scripts/check-all.sh
```

**Windows note.** `tests/conftest.py` forces a selector-based event loop through
pytest-asyncio's loop-factory hook. Without it psycopg's async driver rejects the
`ProactorEventLoop` asyncio picks by default on Windows, and every test touching
the async engine fails with `InterfaceError`. This is a harness fix only — the
application still needs `--reload` (or Linux) there, because uvicorn selects its
loop through a factory and ignores the asyncio policy.

**Markers:** `unit` (default), `integration` (containers required), `security`, `slow`, `real_integration` (opt-in, excluded from CI). A test lacking a marker is treated as `unit` and must not touch the network or the filesystem outside a temporary directory. Everything under `backend/tests/integration/` is marked `integration` automatically by a collection hook, so a new file there cannot be silently excluded.

### Integration tests need a database **[implemented in M1.2]**

```bash
docker compose -f infra/compose/docker-compose.yml --profile core up -d postgres redis
docker compose -f infra/compose/docker-compose.yml exec -T postgres   psql -U dcp -d postgres -c 'CREATE DATABASE dcp_test OWNER dcp;'

cd backend
export TEST_DATABASE_URL='postgresql+psycopg://dcp:placeholder-password@127.0.0.1:5433/dcp_test'
uv run pytest -m integration
```

### Authentication tests also need Redis **[implemented in M1.3]**

```bash
export TEST_REDIS_URL='redis://127.0.0.1:6380/1'
uv run pytest -m integration
```

Database `1`, not `0`: `0` is the Celery broker, and the suite clears its own
key prefixes rather than issuing `FLUSHDB`, which would discard queued work in a
developer's own stack.

Unset, only the authentication suite skips — the rest of the integration tests
still run, so the summary can look complete while the session store, the
single-use `state` and the rate limiter went untested. `scripts/check-all.sh`
reports that case explicitly for exactly that reason.

`TEST_DATABASE_URL` and `TEST_REDIS_URL` are deliberately **not** `DCP_`-prefixed: that prefix is reserved for application settings, and an unrecognised `DCP_*` variable is a startup error by design, so a test-harness variable wearing it would force that guard to be loosened.

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

**Enforced as of M1.7** by [`scripts/check_coverage_diff.py`](../scripts/check_coverage_diff.py), which reads a Cobertura report plus `git diff` and fails under 80 % of the **changed** lines. It gates per-diff, on pull requests only: a push to `main` has no meaningful diff to measure against, and a total-coverage gate is the thing this section explicitly declines to use. Both ends of the stack are held to one rule because vitest emits Cobertura alongside lcov. `scripts/check-all.sh` runs the same check locally against `origin/main`.

Lines the report does not mention are not counted — a blank line, a comment, an `if TYPE_CHECKING:` import — and a diff that touches no measurable file passes with a message saying so, rather than becoming noise people learn to override.

Coverage is a signal, not a target. The floor applies to **changed code** (initially 80 %), with specific areas held higher because their edge cases are security-relevant: the permission matcher, both state machines, service-state normalisation, redaction, and idempotency resolution are expected to be exhaustively covered by table-driven tests. A high overall percentage with an untested approval-bypass path is a failing test suite regardless of the number.

---

## 20. Open decisions and assumptions

| ID | Question | Working default |
|---|---|---|
| TS-03 | Python package manager, which fixes the command prefix in §16 | Any, with a committed lockfile; commands shown with `uv` |
| OPEN-22 | Coverage floor and whether it gates per-file or per-diff | 80 % on changed code |
| ~~OPEN-23~~ | Who owns the non-production sandbox for real-integration tests, and its lifecycle | **RESOLVED 2026-10-20 (M4.3, `OPEN-90`): the project owner.** No one else is named and the implementation sessions have no cloud account. The lifecycle is the runbook in `infra/aws/README.md` |
| OPEN-24 | Load-test tooling (k6, Locust, Artillery) | Undecided; Phase 6 |
| OPEN-25 | Mutation testing on the permission matcher and state machines | Deferred; **[RECOMMENDED]** |

**Assumptions**

1. CI can run Docker containers (PostgreSQL, Redis, Prometheus, Loki, mocks).
2. No test requires production access or production credentials, ever.
3. Mock IdP and mock Jenkins are maintained as part of the repository, since the automated suite depends on them.
4. Real-integration results are recorded as dated manual checks, never as automated passes.

**No test in this document has been written or executed.** [`../PROGRESS.md`](../PROGRESS.md) records the actual state.
