# API Contracts — DevOps Control Plane

**Status:** Draft for review. **No endpoint has been implemented.** This is the contract Phase 1–6 must satisfy; the implementation's OpenAPI schema becomes the executable form of it.
**Source of truth:** [`../PRD.md`](../PRD.md) §7. Consistent with [DATABASE.md](DATABASE.md), [SECURITY.md](SECURITY.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
**All JSON below uses placeholder data.** No example contains a real identifier, hostname, token or credential.

---

## 1. Conventions

| Aspect | Rule |
|---|---|
| Base path | `/api/v1` (AD-17) |
| Content type | `application/json; charset=utf-8`. No XML, no form bodies except the OIDC callback |
| Casing | `snake_case` field names, matching the Python models; the frontend consumes generated types ([TECH_STACK.md §11](TECH_STACK.md#11-contract-synchronisation)) |
| Timestamps | RFC 3339 UTC with `Z`, e.g. `2026-01-15T09:30:00Z`. Never a local-time string, never an epoch integer |
| Durations | Integer seconds, with a `_seconds` suffix |
| Identifiers | UUID strings. A client never constructs one except for `Idempotency-Key` |
| Nullability | Explicit `null` for unknown. **`null` never means zero** (PRD §4.2) |
| Unknown request fields | Rejected with `422` (`extra="forbid"`) — a silently ignored field is a contract bug |
| Trailing slashes | Not used; `/servers`, never `/servers/` |
| Methods | `GET` read-only; `POST` create/action; `PATCH` partial update; `DELETE` soft delete where supported. `PUT` is not used |

### 1.1 Standard request headers

| Header | Required | Notes |
|---|---|---|
| `Cookie: dcp_session=…` | Yes (except public endpoints) | `HttpOnly`; set by the server, never by script |
| `X-CSRF-Token` | Yes on state-changing requests | Double-submit against the CSRF cookie ([SECURITY.md §11](SECURITY.md#11-csrf)) |
| `X-Correlation-ID` | Optional | UUID. Generated if absent; echoed on every response (AD-18) |
| `Idempotency-Key` | Yes where stated | Client-generated UUID; see [§7](#7-idempotency) |
| `If-Unmodified-Since` | Optional on `PATCH` | Optimistic concurrency; mismatch ⇒ `409` |

### 1.2 Standard response headers

`X-Correlation-ID`, `X-Request-Duration-ms`, `Retry-After` (on `429`/`503`), `RateLimit-Remaining`, `RateLimit-Reset`, and the security headers in [SECURITY.md §7](SECURITY.md#7-network-isolation).

### 1.3 Versioning rules (AD-17)

Within `v1`, **only additive changes**: new endpoints, new optional request fields, new response fields, new enum values *in fields documented as extensible*. Breaking changes — removing or renaming a field, narrowing a type, changing a default, adding a required request field, or adding a value to a **closed** enum — require `/api/v2`. The state enums in [§9](#9-enums) are **closed**: a client may switch exhaustively on them. `failure_reason`, `action_type` (audit) and `rule_name` are **extensible**: clients must tolerate unknown values. Two versions run in parallel at most, with a deprecation header and a published sunset date.

---

## 2. Authentication and authorization behaviour

- Session cookie only; no bearer tokens from browsers ([SECURITY.md §2](SECURITY.md#2-authentication-oidc)).
- **Public endpoints (the complete list):** `GET /healthz`, `GET /readyz`, `GET /api/v1/auth/login`, `GET /api/v1/auth/callback`, `POST /api/v1/webhooks/alerts` (authenticated by shared secret, not session).
- Every other endpoint: no/invalid/expired session ⇒ `401 unauthenticated`. Valid session, insufficient permission ⇒ `403 forbidden` with an `AUTHZ_DENIED` audit event.
- **Fail closed.** Any error resolving the session, scope or policy ⇒ `401`/`403`, never a permissive fallback (mandatory rule 9).
- **Scope filtering, not post-filtering.** Collections apply the caller's readable set inside the query, so `total` never counts unreadable rows and totals cannot be used to infer hidden inventory.
- **404 vs 403 (deliberate per endpoint).** For `servers`, `services`, `applications` and `deployments`, an out-of-scope target returns **`404`**, because confirming existence is itself information. For `operations`, `deployments` and `approvals` the caller *created* or is named on, an in-scope-but-insufficient-action attempt returns **`403`**. Each endpoint states which applies.
- Permissions are re-resolved per request from PostgreSQL — no cache, no version counter — so a revocation takes effect on the next request with no re-login (PRD §4.1; amended in M1.4, see [ARCHITECTURE.md §9.2](ARCHITECTURE.md#92-authorization--rbac-with-scope-evaluated-per-request-ad-03)).
- **Read-only mode.** While the incident switch is on ([§25.10](#2510-read-only-mode)), a state-changing request to any permission-bearing endpoint is refused with `423 read_only_mode`. Reads continue working.

---

## 3. Error format

Every non-2xx response uses exactly this envelope:

```json
{
  "error": {
    "code": "forbidden",
    "message": "You do not have permission to restart services in production.",
    "correlation_id": "00000000-0000-0000-0000-000000000000",
    "details": null
  }
}
```

`message` is safe to display to an operator: never a stack trace, SQL fragment, provider payload, internal hostname or path ([SECURITY.md §20](SECURITY.md#20-api-authorization-requirements)).

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `bad_request` | Malformed request outside schema validation |
| 401 | `unauthenticated` | No/invalid/expired session |
| 403 | `forbidden` | Authenticated, not permitted |
| 403 | `csrf_failed` | CSRF token missing or mismatched |
| 404 | `not_found` | Absent, soft-deleted, or out of scope |
| 409 | `conflict` | Optimistic-concurrency failure or duplicate resource |
| 409 | `duplicate_resource` | Unique constraint violation (e.g. server already registered) |
| 409 | `operation_in_progress` | One in-flight operation already exists for this target |
| 409 | `invalid_state_transition` | Action not legal from the current status |
| 422 | `validation_failed` | Schema/semantic validation; `details` populated ([§8](#8-validation-errors)) |
| 422 | `not_permitted` | The caller holds the permission; the action is refused for what it would do (self-disable, delegation escalation, unknown scope reference) |
| 422 | `query_limit_exceeded` | Range, point count or result limit exceeded |
| 422 | `unsupported_operation` | The provider cannot do this (added in M4.1, `OPEN-60`); never rendered as a generic failure. `details.provider_error_class = "unsupported_operation"` |
| 423 | `read_only_mode` | Portal is in incident read-only mode ([SECURITY.md §27](SECURITY.md#27-incident-response)) |
| 429 | `rate_limited` | Includes `Retry-After` |
| 500 | `internal_error` | Unexpected; details in server logs only |
| 502 | `provider_error` | Upstream returned an error; `details.provider`, `details.provider_error_class` |
| 503 | `provider_unavailable` | Upstream unreachable or circuit open |
| 503 | `not_configured` | Integration not configured (includes demonstration mode) |
| 504 | `provider_timeout` | Upstream exceeded its timeout |

Provider error classes surfaced in `details.provider_error_class` map 1:1 to the adapter taxonomy ([ARCHITECTURE.md §7.1](ARCHITECTURE.md#71-error-taxonomy)): `authentication`, `authorization`, `not_found`, `throttled`, `timeout`, `unsupported_operation`, `partial_failure`, `provider_unavailable`, `not_configured`.

**Class → status and code (M4.1, `OPEN-60`).** `authentication`, `authorization`, `not_found` and `partial_failure` → `502 provider_error` (`not_found` is **not** a `404`: that code means DCP's own resource is absent, while this says the provider no longer has it); `throttled` → `503 provider_unavailable` with `Retry-After` (not `429`, which is the caller's own limit); `provider_unavailable` → `503 provider_unavailable`; `timeout` → `504 provider_timeout`; `unsupported_operation` → `422 unsupported_operation`; `not_configured` → `503 not_configured`. The `message` is a fixed operator-safe sentence per class; the provider's own text is never copied into `message` or `details`.

---

## 4. Partial data and `data_state`

A monitoring portal must distinguish "healthy", "unknown" and "broken" (PRD §4.2, §4.4). Any payload carrying collected data includes:

```json
{
  "data_state": "ok",
  "collected_at": "2026-01-15T09:29:45Z",
  "source": "prometheus",
  "stale_after_seconds": 300,
  "unavailable_reason": null
}
```

`data_state` ∈ `ok | stale | unavailable | unsupported | not_configured`. Rules:

1. A failed upstream makes **that section** `unavailable` and leaves the rest of the response usable, with HTTP `200` — one provider's outage does not break the page (PRD §4.11).
2. `unavailable` and `stale` **never** carry substitute values. `value` is `null`.
3. `collected_at` is always present when `data_state = ok` or `stale`.
4. `unsupported` is distinct from `unavailable` — a Windows host has no systemd state; that is not a failure (PRD §4.5).
5. Responses affected by demonstration mode also carry `"demo_mode": true` (AD-15).

---

## 5. Pagination

**Cursor-based** for time-ordered collections (operations, deployments, audit, alerts, logs), **offset-based** for small administrative collections (users, roles, cloud accounts).

Cursor request: `?limit=50&cursor=<opaque>&order=desc`. Response:

```json
{
  "items": [],
  "page": {
    "limit": 50,
    "next_cursor": "b3BhcXVlLXBsYWNlaG9sZGVy",
    "has_more": true,
    "total": null
  }
}
```

Offset request: `?limit=50&offset=0`. Response `page` carries `limit`, `offset`, `total`, `has_more`.

Rules: `limit` default 50, max 200 (logs: default 200, max 1 000); `limit > max` ⇒ `422`, never a silent clamp. Cursors are opaque, signed, and encode the sort key plus a tiebreaker id — never a raw offset. An invalid or expired cursor ⇒ `422 validation_failed`. `total` is `null` on cursor pagination by design: counting a large partitioned table on every page is a latency trap, and on scope-filtered data a total invites inference about hidden rows.

---

## 6. Filtering and sorting

Shared query parameters (all optional, all repeatable where plural):

`environment`, `cloud_account_id`, `provider`, `region`, `application_id`, `server_id`, `operating_system`, `status`, `severity`, `owner_team`, `q` (free-text search), `from`, `to`.

Rules: parameter names and sort keys come from a per-endpoint **allowlist**; an unknown value ⇒ `422` naming the allowed set. `q` is matched against specific indexed columns, never interpolated into SQL. `from`/`to` are RFC 3339; `from > to` ⇒ `422`; maximum range per endpoint. Sorting: `?sort=created_at&order=desc`, allowlisted fields only. Filters never widen scope — they intersect with it.

---

## 7. Idempotency

Required on: `POST /servers`, `POST /applications`, `POST /servers/{server_id}/services/{service_name}/operations`, `POST /deployments`.

- Header `Idempotency-Key: <uuid>`, client-generated, unique per logical intent. Absent ⇒ `422`.
- Scope is `(requesting user, endpoint, key)`.
- **Replay with an identical body** ⇒ `200` with the original resource and `"idempotent_replay": true`. Nothing is re-executed (PRD §4.6, §4.10).
- **Replay with a different body** ⇒ `409 conflict` (`details.reason = "idempotency_key_reused_with_different_payload"`).
- Keys are retained for the row's lifetime for operations and deployments, and at least 24 hours for inventory creates.
- This is independent of the one-in-flight-per-target rule: a *different* key targeting a service with a live operation returns `409 operation_in_progress`.

---

## 8. Validation errors

`422` responses populate `details` with a per-field list — the frontend maps them to fields without parsing prose:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "The request contains invalid fields.",
    "correlation_id": "00000000-0000-0000-0000-000000000000",
    "details": {
      "fields": [
        { "loc": ["body", "action"], "code": "enum", "message": "Must be one of: start, stop, restart.", "allowed": ["start", "stop", "restart"] },
        { "loc": ["body", "environment"], "code": "not_permitted", "message": "Environment not permitted for this action." },
        { "loc": ["query", "limit"], "code": "max_value", "message": "Must be 200 or less.", "max": 200 }
      ]
    }
  }
}
```

`code` values: `required`, `type`, `enum`, `pattern`, `min_length`, `max_length`, `min_value`, `max_value`, `not_permitted`, `not_registered`, `unknown_field`, `invalid_cursor`, `invalid_range`. Validation errors **never** echo back a value from a secret-shaped field.

---

## 9. Enums

**Closed** — safe to switch on exhaustively.

```
HealthState        healthy | warning | critical | stale | unreachable | unknown
ServerStatus       PENDING | MANAGED | STALE | UNREACHABLE | RETIRED
ServiceState       ACTIVE | INACTIVE | FAILED | ACTIVATING | DEACTIVATING | UNCOLLECTED | UNSUPPORTED | UNKNOWN
OperationAction    start | stop | restart | status | logs_tail
OperationStatus    PENDING | AWAITING_APPROVAL | APPROVED | RUNNING | SUCCEEDED | FAILED | TIMED_OUT | CANCELLED | REJECTED
DeploymentStatus   REQUESTED | AWAITING_APPROVAL | APPROVED | QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED | ROLLED_BACK
ApprovalStatus     NOT_REQUIRED | PENDING | APPROVED | REJECTED | EXPIRED
ApprovalDecision   PENDING | APPROVED | REJECTED | EXPIRED
AlertSeverity      INFO | WARNING | CRITICAL
AlertStatus        FIRING | ACKNOWLEDGED | RESOLVED | EXPIRED
Environment        development | staging | production | other
Provider           AWS | AZURE | ON_PREM | OTHER
DataState          ok | stale | unavailable | unsupported | not_configured
```

`OperationStatus` and `DeploymentStatus` are **exactly** the PRD §4.6 and §4.10 state lists. The internal `VERIFYING` step (AD-09) is reported as `RUNNING` so the public enum stays as specified.

**Extensible** — clients must tolerate unknown values: `failure_reason`, audit `action_type`, `target_type`, `rule_name`, `management_method`, `notification channel_type`.

Legal transitions are in [ARCHITECTURE.md §6.2](ARCHITECTURE.md#62-management-write) (operations) and [§8.1](ARCHITECTURE.md#81-deployment-state-machine) (deployments). Note that `DeploymentStatus` has no `TIMED_OUT` member — a deployment deadline breach is `FAILED` with `failure_reason = DEPLOYMENT_TIMED_OUT`. An illegal transition requested through the API ⇒ `409 invalid_state_transition`.

---

## 10. Audit requirements for state-changing endpoints

Every state-changing endpoint writes an `audit_event` **in the same transaction** as the change ([DATABASE.md §12](DATABASE.md#12-transaction-boundaries)). The audit row records actor, action type, target, environment, correlation id, outcome and redacted metadata. Rejections — `403`, policy denial, allowlist denial, target-validation failure — are also audited with `outcome = DENIED`. Per-endpoint `action_type` values are listed in each section below. If the audit write fails, the operation fails ([SECURITY.md §18](SECURITY.md#18-audit-logging-and-audit-protection)).

---

## 11. Health and readiness (public)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness. `200 {"status":"ok"}`. No dependency checks, no data |
| GET | `/readyz` | Readiness: database reachable, migrations at head, broker reachable. `200`/`503` with per-dependency booleans only — **no versions, hostnames or error text** |

---

## 12. Authentication endpoints

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 12.1 | GET | `/api/v1/auth/login` | Begin OIDC login; 302 to the IdP | Public |
| 12.2 | GET | `/api/v1/auth/callback` | OIDC redirect; create session; 302 to `next` | Public |
| 12.3 | POST | `/api/v1/auth/logout` | Destroy session, clear cookie | Authenticated |
| 12.4 | GET | `/api/v1/auth/session` | Current identity + effective permissions (for UI rendering only) | Authenticated |

**12.1** `?next=<relative path>`. Validation: `next` must be a same-origin **relative** path matching `^/[A-Za-z0-9_\-/?=&.]{0,512}$` — an absolute URL is rejected (`422`), because an open redirect here is a phishing primitive. Generates `state` + PKCE verifier + `nonce` in Redis with a 10-minute TTL. Rate limit 10 / 5 min per IP.

**12.2** `?code&state`. Validation: `state` must exist and be unconsumed (single-use); ID token signature/`iss`/`aud`/`exp`/`nonce` all verified. Errors: `400 bad_request` (missing/replayed state), `401 unauthenticated` (token verification failed), `503 provider_unavailable` (IdP unreachable). Timeout: 10 s on token exchange. Audit: `LOGIN_SUCCEEDED` / `LOGIN_FAILED` (never any token material). A disabled user gets `403` and an audited `LOGIN_DENIED`.

Also `403` + `LOGIN_DENIED` (M1.3, assumption A-05): a **new** identity whose email is already held by an **active** user is refused rather than merged, renamed or duplicated — `users` has a case-insensitive unique index over active users, so the case is reachable (a re-provisioned directory account, two providers). `403` rather than `409 duplicate_resource` deliberately: a `409` would confirm to an unauthenticated caller that the address is registered. An administrator resolves it by retiring the existing row, after which the address is reusable. Identity is keyed on `(issuer, subject)`, so an email *change* on an existing subject updates that user and never creates a second one.

**12.3** Requires CSRF token. Idempotent: logging out twice succeeds. Audit `LOGOUT`.

**12.4** Response:

```json
{
  "user": {
    "id": "00000000-0000-0000-0000-000000000000",
    "email": "operator@example.invalid",
    "display_name": "Example Operator",
    "status": "ACTIVE"
  },
  "roles": [
    { "name": "DEVOPS_ENGINEER", "scope": { "environment": ["staging"], "server": ["*"] } }
  ],
  "permissions": ["dashboard:read", "server:read", "service:restart"],
  "session": { "expires_at": "2026-01-15T17:30:00Z", "idle_timeout_seconds": 1800 },
  "demo_mode": false,
  "read_only_mode": false
}
```

**The `permissions` array exists to render the UI and carries no authority.** Every action is independently authorised server-side (mandatory rule 5). It is also **scope-blind**: it lists a permission reachable through any unexpired grant, without applying the grant's scope, so it can say yes to something the server then refuses on a specific target. Proven rather than asserted — `tests/integration/test_admin_roles.py::test_the_permissions_array_is_advisory` grants an empty-scope `ADMINISTRATOR`, reads `admin:users` out of this array, and watches the endpoint answer `403`.

`read_only_mode` reflects the durable setting in [§25.10](#2510-read-only-mode) and is read per request, like the permissions.

---

## 13. Dashboard

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 13.1 | GET | `/api/v1/dashboard/summary` | Aggregate counters and utilisation rollups | `dashboard:read` |
| 13.2 | GET | `/api/v1/dashboard/health` | Server health breakdown for the status table | `dashboard:read` |

**13.1** Query: `environment`, `provider`, `region`, `application_id`, `operating_system`, `owner_team` (all repeatable). Timeout: 2 s database, 10 s Prometheus; a Prometheus failure degrades only the `utilisation` section (PRD §10 target: p95 < 2 s — a design budget, unmeasured).

```json
{
  "servers": {
    "total": 6, "pending": 1, "managed": 3, "stale": 1, "unreachable": 1, "retired": 0,
    "data_state": "ok", "collected_at": "2026-01-15T09:30:00Z", "source": "postgresql"
  },
  "server_health": {
    "healthy": 3, "warning": 1, "critical": null, "stale": 1, "unreachable": 1, "unknown": 0,
    "data_state": "ok", "collected_at": "2026-01-15T09:29:50Z", "source": "prometheus"
  },
  "utilisation": {
    "cpu_percent_avg": null, "memory_percent_avg": null,
    "disk_percent_max": null, "network_bytes_per_second": null,
    "data_state": "unavailable",
    "unavailable_reason": "provider_unavailable",
    "collected_at": null,
    "source": "prometheus"
  },
  "failed_services": { "count": 0, "unobserved": 0, "data_state": "ok", "collected_at": "2026-01-15T09:29:00Z", "source": "postgresql" },
  "active_alerts": { "critical": null, "warning": null, "info": null, "data_state": "not_configured" },
  "recent_deployments": { "items": null, "data_state": "not_configured" },
  "recent_operations": {
    "items": [ { "operation_id": "…", "action_type": "restart", "server_id": "…", "server_display_name": "web-01",
                 "service_name": "nginx.service", "environment": "staging", "execution_status": "SUCCEEDED",
                 "requested_by": { "id": "…", "display_name": "Example Operator" },
                 "created_at": "2026-01-15T09:12:00Z" } ],
    "data_state": "ok", "collected_at": "2026-01-15T09:30:00Z", "source": "postgresql" },
  "recent_health_changes": {
    "items": [ { "server_id": "…", "display_name": "web-01", "environment": "staging",
                 "previous_status": "MANAGED", "new_status": "STALE", "health_state": "stale",
                 "occurred_at": "2026-01-15T09:20:00Z" } ],
    "data_state": "ok", "collected_at": "2026-01-15T09:30:00Z", "source": "postgresql" },
  "filters_applied": { "environment": ["staging"] },
  "demo_mode": false,
  "cache_ttl_seconds": 15
}
```

The `utilisation` block illustrates the required behaviour: Prometheus is down, so values are `null` with `data_state: "unavailable"` — **never `0`** (PRD §4.2 acceptance criterion).

**Two shape decisions, taken in M1.6 and recorded here rather than left as a silent divergence.** An earlier draft of this section drew `healthy`, `warning` and `critical` inside `servers` beside `total` and `managed`, and the three recent-activity keys as bare `[]`.

1. **`server_health` is a sibling section, not three fields inside `servers`.** `total`, `pending`, `managed`, `stale`, `unreachable` and `retired` are counts of the `servers.status` column — a real source, `data_state: "ok"`. `healthy`, `warning`, `stale`, `unreachable`, `unknown` and `critical` are a *health* rollup from a different source (Prometheus, M2.5). One envelope carries one `data_state` (§4), and a block that mixed the two would have to claim either that the status counts are unavailable or that the health counts are collected. Both are false. The field names are unchanged, so a client reads `summary.server_health.healthy` instead of `summary.servers.healthy`.
2. **`recent_deployments` and `recent_operations` are `DataSection`s carrying `items: null`, not bare arrays** (`recent_health_changes` stopped being one in M2.5: it reads `audit_events`, so it can say `[]` and mean it, below). There is no deployments table and no operations table. `[]` asserts "nothing happened recently", which is a claim this product cannot make before M5.1 and M4.2 — and §4 rule 2 plus PRD §4.2 forbid a substitute value for an absent one just as firmly for an empty list as for a zero. Each carries an `unavailable_reason` naming the milestone that will fill it.

   **Amended in M4.6 (`OPEN-116`): `recent_operations` is now a real section.** `operations` exists (M4.2) and §23's scoping function exists, so the section lists the caller's **ten** most recent operations by the same predicate as §23.1 (requested by the caller, or `operation:read` over the stored environment), newest first, with `data_state: "ok"`, `collected_at` and `source: "postgresql"`. **`items: []` now means "nothing happened that you may see"** and is distinct from `not_configured` with `items: null`. Each item carries `operation_id`, `action_type`, `server_id`, `server_display_name`, `service_name`, `environment` (the stored one), `execution_status`, `requested_by {id, display_name}` and `created_at` — no reason, no result, no execution id (the detail page has them). The section rides the dashboard's scope-keyed cache, so a change shows after `cache_ttl_seconds`. `recent_deployments` stays `not_configured` until M5.1.

**`failed_services` counts what units *show*, and says how many it could not read (M2.4).** `count` is the number of registered units on readable servers whose displayed state is `FAILED` — the §16.1 state, host-aware, not the raw `services.last_observed_state` column — so a unit cached `FAILED` on a host that has since gone quiet is not counted as failed. The new **`unobserved`** is the number that show `UNKNOWN` or `UNCOLLECTED` (including a cache that has not been refreshed in 180 s). It exists so `count: 0` beside `unobserved: 12` cannot be read as `0` of `12`; a client must show both. `data_state` stays `ok`: both numbers are exact counts of a real table.

**`server_health` is the host-health rollup (M2.5).** It counts **every readable server** — the same population as `servers.total` — so the five counts sum to it: a retired, unsupported, unmonitored or never-scraped server is `unknown`, not omitted. They are exactly the states the §13.2 `health_status` filter selects, by the same rule, so a card's number is the length of the list it links to. It is `ok` only while Prometheus has answered within three probe intervals; otherwise it is `stale` (with `collected_at` = when Prometheus last answered), `unavailable` (never answered) or `not_configured`, and **every count is `null`, never `0`** (§4 rule 2). **Nothing is judged against an old answer**: during a Prometheus outage hosts are not aged into `unreachable` (`OPEN-30`, PROGRESS issue 67). `critical` is always `null` — it would come from utilisation (`OPEN-28`), which is not evaluated, and `null` says so where `0` would say "none". `stale` and `unreachable` here are *health* (computed from `last_seen_at` against `DCP_HEALTH_THRESHOLDS` on read); the same-named counts under `servers` are the stored `status`, which the sweep writes within about a minute, and which also depend on administrators' `PENDING`/`RETIRED`.

**`recent_health_changes` (M2.5, D4)** is the latest 10 `SERVER_STATUS_CHANGED` audit rows from the last 30 days, newest first, on servers the caller can read (scope is applied through the audit row's target, so a move on an unreadable server is never listed). Only the sweep's moves appear; an administrator's edit is `SERVER_UPDATED`. `[]` means no recorded change.

`cache_ttl_seconds` reports `DCP_DASHBOARD_CACHE_TTL_SECONDS`, so a client need not guess how often polling is worth anything. The cached entry is keyed on the caller's **resolved scope** as well as the filters (docs/SECURITY.md §20); a write is not reflected until the entry expires.

**13.2** Query: as 13.1 plus `health_status` and cursor pagination. Returns per-server `{server_id, display_name, hostname, environment, provider, health_state, status, last_seen_at, stale, failed_service_count, active_alert_count}`. Selecting a status filter updates this list (PRD §4.2 acceptance criterion).

```json
{
  "items": [
    {
      "server_id": "…", "display_name": "web-01", "hostname": "web-01.internal.example",
      "environment": "staging", "provider": "ON_PREM", "status": "MANAGED",
      "health_state": "healthy", "last_seen_at": "2026-01-15T09:29:45Z", "stale": false,
      "failed_service_count": 0, "active_alert_count": null
    }
  ],
  "page": { "limit": 50, "next_cursor": null, "has_more": false, "total": null },
  "health": { "healthy": 3, "warning": 1, "critical": null, "stale": 1, "unreachable": 1, "unknown": 0, "data_state": "ok", "collected_at": "2026-01-15T09:29:50Z", "source": "prometheus" },
  "alerts": { "data_state": "not_configured", "unavailable_reason": "Alerting is not implemented yet (M3.3)." },
  "collected_at": "2026-01-15T09:30:00Z",
  "filters_applied": {},
  "demo_mode": false,
  "cache_ttl_seconds": 15
}
```

`health_state` is **never defaulted to `healthy`**. It is one of `healthy | warning | stale | unreachable | unknown` when a fresh Prometheus reference exists (`critical` is never produced, `OPEN-28`), and **`null` when nothing could be evaluated** — Prometheus unconfigured, silent or stale. `unknown` is an evaluated answer: the server is retired, on an unsupported OS, has monitoring off, has no `metrics_instance`, or has never been scraped. `stale` is the verdict `stale` or `unreachable`, `false` for `healthy`/`warning`, and `null` for `unknown` and `null` (never seen is not stale). The `health` section is the §13.1 rollup and carries the reason once for the page when the rows are `null`; `failed_service_count` is a real count over `services`.

Filtering on `health_status` is applied in SQL by the twin of the rule that produces `health_state` (a parity test holds them together), so cursor paging works over the filtered set. With no usable reference it matches nothing and returns an empty page with `health.data_state` saying why: the filter is **honoured rather than ignored**, because dropping it would return the whole list looking like the healthy subset.

Errors: `401`, `403`, `422` (unknown filter value), `503` (database unavailable). No audit (read).

---

## 14. Server inventory

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 14.1 | GET | `/api/v1/servers` | List/search inventory | `server:read` |
| 14.2 | POST | `/api/v1/servers` | Register a server | `server:write` (Administrator) |
| 14.3 | GET | `/api/v1/servers/{server_id}` | Server detail | `server:read` |
| 14.4 | PATCH | `/api/v1/servers/{server_id}` | Edit metadata, enable/disable monitoring | `server:write` |
| 14.5 | DELETE | `/api/v1/servers/{server_id}` | Soft delete (retire) | `server:write` |
| 14.6 | POST | `/api/v1/servers/{server_id}/applications` | Associate an application | `server:write` |
| 14.7 | DELETE | `/api/v1/servers/{server_id}/applications/{application_id}` | Remove association | `server:write` |
| 14.8 | POST | `/api/v1/servers/{server_id}/connectivity-check` | Verify management connectivity | `server:write` |

**14.1** Query: `environment`, `provider`, `cloud_account_id`, `region`, `status`, `operating_system`, `application_id`, `owner_team`, `q` (hostname/display name/provider id prefix), `monitoring_enabled`, cursor pagination, `sort` ∈ `{display_name, hostname, last_seen_at, created_at, environment}`. Scope-filtered.

**14.2** Requires `Idempotency-Key`.

```json
{
  "cloud_account_id": "00000000-0000-0000-0000-000000000000",
  "provider_resource_id": "PLACEHOLDER-RESOURCE-ID",
  "hostname": "placeholder.internal.example",
  "display_name": "Placeholder Web 01",
  "operating_system": "LINUX",
  "os_version": "PLACEHOLDER 1.0",
  "environment": "staging",
  "region": "placeholder-region-1",
  "private_ip": "10.0.0.10",
  "owner_team": "platform",
  "management_method": "AWS_SSM",
  "monitoring_enabled": true,
  "metrics_instance": "placeholder.internal.example:9100",
  "application_ids": []
}
```

Validation: `cloud_account_id` must reference an `ACTIVE`, non-deleted account (`422 not_registered`); `provider_resource_id` 1–256 chars matching the provider's expected pattern; `hostname` a valid DNS name ≤ 253 chars; `environment`, `operating_system`, `management_method` closed enums; `private_ip` a valid IP; `metrics_instance` unique across servers; `management_method` must be supported by the account's provider (`AZURE_RUN_COMMAND` on an AWS account ⇒ `422`). Created with `status: "PENDING"` — **registration grants no operational permission** (PRD §4.3). Errors: `409 duplicate_resource` on `(cloud_account_id, provider_resource_id)` (PRD §4.3 acceptance criterion), `422`, `403`. Audit `SERVER_REGISTERED`. Response `201`.

**Implemented in M2.3 — `metrics_instance` is policed (§14.2, §14.4).** Because the value becomes a scrape target, a *new or changed* `metrics_instance` is refused with `422 not_permitted` (field `body.metrics_instance`) unless its port is one of `DCP_METRICS_TARGET_PORTS` (default `9100`, listed in `allowed` when that is the problem) and it is not a loopback, link-local, unspecified, multicast or reserved address, nor in the `.invalid` TLD. A `PATCH` that resubmits the row's *current* value is not refused, so a seeded row stays editable. The value is never echoed. Registering, or editing `metrics_instance`, `monitoring_enabled`, `environment` or `status`, enqueues a rebuild of the target file (best effort; a periodic rebuild heals a missed one).

**14.3** Returns the full row plus `health` (`state`, `data_state`, `collected_at` = `last_seen_at`, `stale_after_seconds` = the server's environment line — the same rule as §13.2, with `state: null` and the reason when nothing could be evaluated), `agent_status`, computed `stale`, associated applications, registered service count, open alert count and `management_capabilities` (from the provider's capability set — an explicit statement of what this provider can and cannot do, [ARCHITECTURE.md §7.2](ARCHITECTURE.md#72-capability-matrix--providers-are-not-interchangeable)). Out of scope ⇒ `404`.

**14.4** Mutable fields only: `display_name`, `owner_team`, `environment`, `monitoring_enabled`, `metrics_instance`, `status` (`PENDING`↔`MANAGED`↔`RETIRED` only), `os_version`, `private_ip`, `region`. Immutable: `id`, `cloud_account_id`, `provider_resource_id`, `created_at` (`422 not_permitted`). **Changing `environment` is a privilege-relevant change** — it alters the authorisation boundary for every future operation on this server, so it requires Administrator and is audited as `SERVER_ENVIRONMENT_CHANGED`. Supports `If-Unmodified-Since` ⇒ `409 conflict`. Audit `SERVER_UPDATED`. **`STALE` and `UNREACHABLE` are written only by the status sweep (M2.5, `OPEN-30`)**, which audits each move as `SERVER_STATUS_CHANGED` with `actor_type = SYSTEM`, `actor_label = system:health-sweep` and `{from, to, health_state, age_seconds, reference_at}`; its moves do not advance `updated_at`, so they cannot cause a `409` here (migration 0006).

**14.5** Soft delete; `status → RETIRED`, `deleted_at` set. Blocked with `409 invalid_state_transition` while any non-terminal operation targets the server. History is retained. Audit `SERVER_RETIRED`.

**14.8** **Implemented in M4.3 (`OPEN-86`).** Synchronous probe through the provider adapter; 10 s timeout. Returns `{reachable, checked_at, provider_status, data_state, message}`. `503 not_configured` in demonstration mode or when the account lacks credentials. Rate limit 10 / min per server. Audit `SERVER_CONNECTIVITY_CHECKED`.

---

## 15. Server metrics

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 15.1 | GET | `/api/v1/servers/{server_id}/metrics` | Current metric values | `metrics:read` |
| 15.2 | GET | `/api/v1/servers/{server_id}/metrics/range` | Historical time series | `metrics:read` |
| 15.3 | POST | `/api/v1/metrics/compare` | Compare several servers or environments | `metrics:read` |
| 15.4 | GET | `/api/v1/metrics/templates` | List available metric templates | `metrics:read` |

**No endpoint accepts PromQL** (AD-04). Clients choose a template id; the backend renders the query.

**15.1** Query: `metrics` (repeatable template ids; default all).

```json
{
  "server_id": "00000000-0000-0000-0000-000000000000",
  "metrics": [
    { "template": "cpu_utilisation", "unit": "percent", "value": 12.5,
      "data_state": "ok", "collected_at": "2026-01-15T09:29:45Z" },
    { "template": "memory_utilisation", "unit": "percent", "value": null,
      "data_state": "stale", "collected_at": "2026-01-15T09:10:00Z",
      "stale_after_seconds": 300 },
    { "template": "disk_utilisation", "unit": "percent", "value": null,
      "data_state": "unavailable", "unavailable_reason": "provider_timeout", "collected_at": null }
  ],
  "thresholds": { "cpu_utilisation": { "warning": 80, "critical": 90 } },
  "source": "prometheus",
  "demo_mode": false
}
```

**15.2** Query: `metrics` (required, 1–5 templates), `from`, `to` (required), `step_seconds` (required). Validation: `from < to`; max range 30 days; `(to - from) / step ≤ 11 000` else `422 query_limit_exceeded` with the allowed maximum in `details`; `step_seconds ≥ 15`. Timeout 10 s. Series carry `[[timestamp, value|null], …]` — **gaps are `null`, never interpolated or zeroed** (PRD §4.4), and the renderer must draw them as gaps ([TECH_STACK.md §2.5](TECH_STACK.md#25-charting-library)).

**15.3** Body: `{"metric":"cpu_utilisation","server_ids":[…]}` or `{"metric":"cpu_utilisation","environments":["staging"]}`, plus `from`, `to`, `step_seconds`. Validation: at most 20 servers or 4 environments per request; the same point-count cap; scope-filtered — out-of-scope servers are dropped silently and reported in `excluded_count` rather than erroring (so the comparison is never silently *wrong* about what it included).

**15.4** Static catalogue: `{template, unit, description, applies_to_os: ["LINUX"], default_thresholds}`. Linux and Windows have different templates because the exporters expose different series — a parity assumption here would produce wrong numbers instead of an error.

**Implemented in M2.1.** The response is `{templates: [...], source: "prometheus", demo_mode}` and the endpoint accepts **no query parameter at all** — including one that looks like a filter, which is a `422` rather than the full catalogue pretending the filter applied (§6). `applies_to_os` is `["LINUX"]` for all ten templates: there are **no Windows queries**, so a Windows server resolves to the `unsupported` data state (`OPEN-09`'s recorded default). `default_thresholds` is `null` for seven of the ten — present only on `cpu_utilisation`, `memory_utilisation` and `disk_utilisation`, because no default is universally meaningful for a byte count, a throughput or a load average. **An absent threshold is not a zero threshold.**

**Implemented in M2.2 (§15.1–§15.3).** All three are reads; none writes an audit row, and §15.3 — a `POST` only because it carries a list — is allowed in read-only mode so a contained portal can still be looked at. Scope is `metrics:read` against the **server** target, checked after the row loads (single-server reads) or inside the query (`readable_servers`, §15.3). Out of scope reads as `404`, indistinguishable from an unknown id. Divergences from the examples above, each forced by the honest-states rule rather than chosen:

- **A reading is a value or a reason.** `value` is `null` unless `data_state` is `ok`; a `stale` reading carries no value either (§4 rule 2), only `collected_at` and `stale_after_seconds`. `data_state` is one of `ok | stale | unavailable | unsupported | not_configured`, and `unavailable_reason` is a closed set: `provider_timeout`, `provider_unreachable`, `provider_error`, `circuit_open`, `no_data`, `monitoring_disabled`, `no_metrics_instance`, `prometheus_not_configured`, `unsupported_os`. `no_data` is deliberately blunt: until M2.3 verifies `metrics_instance`, "nothing was scraped" and "the label matches no target" cannot be told apart.
- **`threshold_state` is added to each §15.1 reading**: `normal | warning | critical`, or `null` when there is no value **or** no threshold. A value exactly on a threshold is *at* it (`>=`). `thresholds` lists only templates that have one; absent is not zero.
- **`collected_at` is the host's latest scrape**, not the query time: it comes from `timestamp(up{…})`, because an instant query stamps every result with its own evaluation time. `stale` means that scrape is more than **60 s** old (a constant in `app/metrics/thresholds.py`), deliberately below Prometheus's 5-minute lookback, above which the series simply vanishes and the reading is `unavailable` instead. Host-level staleness (AD-14, per-environment) remains M2.5.
- **§15.2 returns `start`, `end` and `step_seconds`** (the window actually queried) so the UI can show it, and each series' `points` is the **full requested grid** with `null` at every hole, `null` for a `NaN`, and `points: null` (not `[]`) when nothing at all came back. `from`/`to` must carry a timezone offset (`422` otherwise). The rate window widens to the step, never narrower than 300 s.
- **§15.3 returns one series per included server, ordered by display name**, each with `server_id`, `display_name`, `environment`, `data_state` and `points` — a server with no data is *listed with its reason*, not dropped. The 20-server cap counts the servers that will actually be queried **after** scoping, so an environment selection that resolves to more than 20 is `422 query_limit_exceeded` rather than being silently truncated. `excluded_count` is the number of requested `server_ids` that did not resolve (not found and out of scope are indistinguishable by design); it is always `0` for an environment selection, where a real count would disclose how many servers exist outside the caller's scope.
- **Thresholds are the catalogue defaults only** (`OPEN-28`): CPU 80/90, memory 85/95, disk 85/95. There is no per-server or per-environment override yet.

---

## 16. systemd services

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 16.1 | GET | `/api/v1/services` | Cross-server service list (failed-service view) | `service:read` |
| 16.2 | GET | `/api/v1/servers/{server_id}/services` | Services on one server | `service:read` |
| 16.3 | POST | `/api/v1/servers/{server_id}/services` | Register a service for monitoring | `server:write` |
| 16.4 | GET | `/api/v1/servers/{server_id}/services/{service_name}` | Service detail | `service:read` |
| 16.5 | PATCH | `/api/v1/servers/{server_id}/services/{service_name}` | Edit monitoring/action policy | `server:write` |
| 16.6 | DELETE | `/api/v1/servers/{server_id}/services/{service_name}` | Deregister (soft delete) | `server:write` |
| 16.7 | GET | `/api/v1/servers/{server_id}/services/{service_name}/logs` | Recent service logs | `logs:read:system` |

`{service_name}` is path-encoded and must match a **registered** `services` row for that server (`404 not_found` otherwise). It is validated against `^[A-Za-z0-9@._\-]{1,128}\.(service|socket|timer|target)$` as defence in depth, but **registration — not the pattern — is the control** ([SECURITY.md §15](SECURITY.md#15-service-name-and-operation-allowlists)).

**16.1** Query: `state` (repeatable, e.g. `state=FAILED`), `environment`, `application_id`, `server_id`, `q`, cursor pagination. Each item:

```json
{
  "service_id": "00000000-0000-0000-0000-000000000000",
  "server_id": "00000000-0000-0000-0000-000000000000",
  "server_display_name": "Placeholder Web 01",
  "environment": "staging",
  "service_name": "placeholder-api.service",
  "state": "FAILED",
  "sub_state": "failed",
  "load_state": "loaded",
  "enabled": true,
  "main_pid": null,
  "restart_count": 3,
  "last_state_change_at": "2026-01-15T09:15:00Z",
  "data_state": "ok",
  "collected_at": "2026-01-15T09:29:00Z",
  "server_reachable": true,
  "allowed_actions": ["restart"],
  "approval_required": false,
  "approval_window_seconds": null
}
```

**Acceptance-critical behaviour (PRD §4.5):** when `server_reachable` is `false`, every service on that server reports `state: "UNKNOWN"` with `data_state: "stale"` or `"unavailable"` — **never `INACTIVE`**. A Windows host reports `state: "UNSUPPORTED"`, `data_state: "unsupported"`. `UNCOLLECTED` (exporter not reporting this unit) is distinct from both.

`allowed_actions` is the intersection of the service's action policy and the caller's permissions — a UI convenience that carries no authority (mandatory rule 5).

**16.3** Body `{"service_name","display_name","application_id","monitoring_enabled","action_policy_id"}`. Validation: name pattern; `action_policy_id` must exist (`422 not_registered`); **omitting it means no actions are permitted** (fail closed). Errors: `409 duplicate_resource` on `(server_id, service_name)`. Audit `SERVICE_REGISTERED`.

**16.5** Mutable: `display_name`, `application_id`, `monitoring_enabled`, `action_policy_id`. Changing `action_policy_id` is privilege-relevant: Administrator only, audited as `SERVICE_ACTION_POLICY_CHANGED`.

**16.7** Query: `limit` (default 100, max 1 000), `from`, `to` (max 24 h range), `level`. Served from Loki filtered to this unit — **not** by executing a command on the host. `503 provider_unavailable` when Loki is down. Response lines pass through the redaction pass ([SECURITY.md §17](SECURITY.md#17-sensitive-log-redaction)). Timeout 15 s. No audit (read), but `logs:read:system` denials are audited.

**Implemented in M2.4 (§16.1–§16.7).** The reads need `service:read`; registration, edit and deregistration need `server:write`; every one is checked against the **server's** scope, so an out-of-scope server or unit is `404`. Divergences and decisions, each forced by the honest-states rule or by what the source can supply rather than chosen:

- **Four fields are always `null` (`OPEN-29`):** `sub_state`, `load_state`, `enabled` and `main_pid`. Neither `node_exporter`'s systemd collector nor `systemd_exporter` exposes them (`TS-02`, [TECH_STACK.md §5.3](TECH_STACK.md#53-systemd-metrics-integration)). `last_state_change_at` is `null` for the same reason: the exporter's start time describes activation only, so §16.4 adds **`started_at`** (null unless the unit is running) rather than mislabelling it. `active/running` and `active/exited` are both `ACTIVE`.
- **Lists read a cache; detail reads Prometheus.** §16.1 and §16.2 are served from `services.last_observed_*` and never fan out per row, so `restart_count` is `null` there. §16.4 queries Prometheus now and is the only place it is known. A cache older than 180 s, or a host the probe calls `UNREACHABLE`, shows `UNKNOWN` with `data_state: "stale"` and the time it was last true — never the cached state as though it were current. The `state` filter selects on the state a row **shows**, not on the raw column.
- **Added item fields:** `display_name`, `application_id`, `monitoring_enabled`, and `unavailable_reason` (the closed set from §15). `data_state` is `ok | stale | unavailable | unsupported | not_configured`; `UNCOLLECTED` is `unavailable` + `no_data`.
- **`allowed_actions` / `approval_required` are hints.** The policy's actions intersected with the caller's permissions (`status` ⇒ `service:read`, `logs_tail` ⇒ `logs:query`); approval is required when the policy's approval policy covers the server's environment. Neither carries authority. **M4.5:** `approval_window_seconds` is that policy's window (how long a request would wait for an approver) when `approval_required`, else `null`; a production `stop` is gated by default whether or not a policy row exists, so this hint can say `false` for a request the backend will still queue — the response to §17.1 is what is true.
- **An action policy is a privilege.** Assigning one at registration, or changing it with §16.5, is Administrator-only (`admin:users` is the stand-in, as for a server's environment change) and refuses as `404`. A change is audited as `SERVICE_ACTION_POLICY_CHANGED` in addition to `SERVICE_UPDATED`; resubmitting the current policy is not a change. `monitoring_enabled: null` is `422`.
- **§16.7 is routed and validated but cannot answer.** The permission is `logs:read:system` (renamed from the seeded `logs:query` by migration 0007, `OPEN-32`). A valid request for a registered unit returns **`503 not_configured`** until Loki exists (M3.1) — an empty page would read as "no errors". `limit` ≤ 1 000, `from`/`to` carry a timezone and span ≤ 24 h, and any other query parameter is `422`.
- **No field carries a command.** Bodies are `extra="forbid"`; `command`, `script`, `argv`, `exec` and `action` are `422 unknown_field`, and the unit name is validated with a full match (a trailing newline is refused).
- **`GET /servers/{id}/services` accepts only `state`, `application_id`, `q` and paging;** `environment` and `server_id` are `422` there, since the path already fixes the server.

---

## 17. Service operations

The most security-sensitive group ([SECURITY.md §16](SECURITY.md#16-safe-start--stop--restart-workflow)).

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 17.1 | POST | `/api/v1/servers/{server_id}/services/{service_name}/operations` | Request an allowlisted operation | `service:{action}` on the target's environment |
| 17.2 | GET | `/api/v1/operations/{operation_id}` | Operation status and result | `operation:read` (requester or scoped reader) |
| 17.3 | POST | `/api/v1/operations/{operation_id}/cancel` | Cancel before execution | requester or `operation:approve` |

**17.1** Headers: `Idempotency-Key` (required), `X-CSRF-Token` (required).

```json
{ "action": "restart", "reason": "Placeholder change reference CR-0000" }
```

**The entire request body is one closed enum plus a free-text reason.** There is no command, script, argument, document name, timeout override, host or URL field, and none may ever be added (mandatory rules 1 and 2).

Validation, in order ([SECURITY.md §16](SECURITY.md#16-safe-start--stop--restart-workflow)): `Idempotency-Key` present → `action` ∈ `{start, stop, restart}` (`status` and `logs_tail` are `422` here: §16.4 and §16.7 serve them) → server exists, `MANAGED`, not deleted → cloud account `ACTIVE` → service registered **to this server** → action in the service's policy `allowed_actions` → caller holds `service:{action}` on the server's **stored** environment → **idempotency replay** → an adapter resolves and supports the action → approval policy evaluated → rate limit → no in-flight operation for this target. The replay lookup precedes the rate limit and the in-flight guard so that a replay of a running operation returns the original and spends no budget (`OPEN-73`). `reason` is required for `production` and wherever approval is required, 1–500 chars, stored and audited.

**An operation that needs approval is queued, not refused (M4.5, `OPEN-96`).** A policy that requires approval, a `stop` on `production`, or an unreadable policy creates the operation `AWAITING_APPROVAL` with an `approvals` row and answers `202`; nothing is enqueued until an approver decides (§22). A policy with `required_approvals > 1` gives `503 not_configured` with `details.reason = "approval_policy_unsupported"` and no row (`OPEN-95`). While the request waits its `deadline_at` is the end of the approval window, and it holds the target's one in-flight slot. **Until a real provider exists (M4.3)** a request gives `503 not_configured` with `details.reason` `demonstration_mode` or `no_adapter_for_provider`, and `422 unsupported_operation` when the adapter's capability for the action is `unsupported` (`OPEN-75`). None of these creates an `operations` row. *(The M4.2 refusal `approval_workflow_unavailable` no longer exists.)*

Response `202`:

```json
{
  "operation_id": "00000000-0000-0000-0000-000000000000",
  "execution_status": "AWAITING_APPROVAL",
  "approval_status": "PENDING",
  "action_type": "restart",
  "target": {
    "server_id": "00000000-0000-0000-0000-000000000000",
    "server_display_name": "Placeholder Web 01",
    "service_name": "placeholder-api.service",
    "environment": "production"
  },
  "requested_by": { "id": "00000000-0000-0000-0000-000000000000", "display_name": "Example Operator" },
  "approval": { "approval_id": "00000000-0000-0000-0000-000000000000", "required_approvals": 1, "expires_at": "2026-01-15T10:30:00Z" },
  "deadline_at": "2026-01-15T09:35:00Z",
  "correlation_id": "00000000-0000-0000-0000-000000000000",
  "poll_after_seconds": 2,
  "idempotent_replay": false
}
```

The echoed `target` is what the UI must have displayed in the confirmation dialog (PRD §4.6).

Errors: `403 forbidden` (permission or action not allowlisted — audited `AUTHZ_DENIED`), `404 not_found` (unknown/out-of-scope server or unregistered service), `409 operation_in_progress`, `409 conflict` (idempotency key reused with a different body), `422 validation_failed`, `422 unsupported_operation`, `423 read_only_mode`, `429 rate_limited`, `503 not_configured` (demonstration mode, an unconfigured provider, or an unsupported approval policy — see above). Timeouts: the API call returns in under 1 s — execution is asynchronous (PRD §5.3); the operation's own deadline comes from its action policy (default 300 s). Audit `SERVICE_OPERATION_REQUESTED`, and on denial `SERVICE_OPERATION_DENIED`.

**17.2** Response adds `observed_state_before`, `observed_state_after`, `sanitized_result`, `failure_reason`, `external_execution_id`, `started_at`, `completed_at`, `approval` detail, and `poll_after_seconds` while non-terminal. **Visibility (M4.6, `OPEN-107`):** the requester, or `operation:read` over the operation's **stored** environment and its server's account and id — never the server's current label (this was implemented against the current label before M4.6; PROGRESS issue 164). §17.2 and §23 now ask the same question.

```json
{
  "operation_id": "00000000-0000-0000-0000-000000000000",
  "execution_status": "SUCCEEDED",
  "action_type": "restart",
  "observed_state_before": "FAILED",
  "observed_state_after": "ACTIVE",
  "verified_at": "2026-01-15T09:31:12Z",
  "sanitized_result": { "provider": "AWS", "exit_code": 0, "stdout_excerpt": "[redacted-or-placeholder]" },
  "failure_reason": null,
  "completed_at": "2026-01-15T09:31:12Z"
}
```

`verified_at` is derived from `completed_at` (written in the same statement as the terminal status); there is no separate column. `parent_operation_id` is returned and is `null` until a retry trigger exists (`OPEN-77`). `failure_reason` is a closed code set in M4.2 (`PERMISSION_REVOKED`, `APPROVAL_REQUIRED`, `ACCOUNT_DISABLED`, `ACTION_NOT_ALLOWED`, `TARGET_VALIDATION_FAILED`, `NOT_CONFIGURED`, `UNSUPPORTED_OPERATION`, `PROVIDER_AUTHENTICATION`, `PROVIDER_AUTHORIZATION`, `PROVIDER_NOT_FOUND`, `PROVIDER_THROTTLED`, `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`, `PROVIDER_PARTIAL_FAILURE`, `PROVIDER_REPORTED_FAILURE`, `VERIFICATION_MISMATCH`, `VERIFICATION_UNAVAILABLE`, `RESUME_REFUSED` (M4.3: a resumer's re-check refused to continue a command that is already running, so the outcome is unknown; `sanitized_result.reason_code` names the refusal), `DEADLINE_EXCEEDED`, `EXPIRED_BEFORE_START`), and `sanitized_result.reconciliation_required` is `true` when the provider may have acted. `404` for an unknown operation or an out-of-scope server; the requester always reads their own; others need `operation:read` over the stored environment, else `403`.

`SUCCEEDED` is accompanied by `observed_state_after` and `verified_at` — the schema itself refuses a success without verification evidence ([DATABASE.md §6.1](DATABASE.md#61-constraints-that-carry-security-meaning), mandatory rule 8). A failure returns `sanitized_result` and a `failure_reason` code, readable only by identities scoped to the target (PRD §4.6). Visibility: the requester always; others need `operation:read` on the target's environment ⇒ `403`.

**17.3** Legal only from `PENDING` or `AWAITING_APPROVAL`; otherwise `409 invalid_state_transition`. A `RUNNING` operation cannot be cancelled in v1 — the provider call is already in flight and a false "cancelled" would be a lie about system state. Audit `SERVICE_OPERATION_CANCELLED`. **From `AWAITING_APPROVAL` (M4.5, `OPEN-100`)** the cancel also ends the operation's approval (`decision = EXPIRED`, `reason_code = cancelled`) and resolves its pending-approval alert in the same transaction, and a cancel that loses a race with a decision gets `409`.

**The `approval` object (M4.5).** Present on §17.1 and §17.2 whenever the operation has an approval request, otherwise `null`: `approval_id`, `required_approvals` (always `1` in M4.5), `expires_at`, and — `PENDING` while it waits — `decision` (`PENDING | APPROVED | REJECTED | EXPIRED`), `required_role`, `approved_by` (`{id, display_name}`, the decider, also for a rejection; `null` until decided) and `decided_at`. `approval_status` on the operation is `NOT_REQUIRED | PENDING | APPROVED | REJECTED | EXPIRED`; `EXPIRED` means nobody decided before the window closed, or the request was cancelled. An operation ended by the sweep is `CANCELLED` with `failure_reason = null` and `sanitized_result.reason_code = "approval_expired"`.

---

## 18. Applications

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 18.1 | GET | `/api/v1/applications` | List applications | `server:read` |
| 18.2 | POST | `/api/v1/applications` | Register an application | `admin:integrations` |
| 18.3 | GET | `/api/v1/applications/{application_id}` | Detail: servers, services, targets | `server:read` |
| 18.4 | PATCH | `/api/v1/applications/{application_id}` | Edit metadata | `admin:integrations` |
| 18.5 | DELETE | `/api/v1/applications/{application_id}` | Archive (soft delete) | `admin:integrations` |
| 18.6 | GET | `/api/v1/applications/{application_id}/health` | Health per environment | `server:read` |
| 18.7 | GET | `/api/v1/applications/{application_id}/deployments` | Deployment history | `deployment:read` |

**18.2** Body `{"name","slug","description","owner_team","repository_reference","health_check_config"}`, `Idempotency-Key` required. `health_check_config` is stored as `{"checks": [{"name", "url", "environment", "expected_status"}]}` — a shape §18 did not pin, chosen in M1.5 and recorded as assumption `A-10` in [`../PROGRESS.md`](../PROGRESS.md). Validation: `slug` matches `^[a-z0-9][a-z0-9-]{1,62}$`, unique; `repository_reference` must be an `https` URL on the allowlist or an `org/repo` form; **`health_check_config` URLs are validated against the SSRF host allowlist at write time and again at request time** ([SECURITY.md §19](SECURITY.md#19-ssrf)). Errors `409 duplicate_resource`, `422`. Audit `APPLICATION_REGISTERED`.

**18.6** Query: `environment` (optional). **Application health is reported separately from host health** (PRD §4.9 acceptance criterion):

```json
{
  "application_id": "00000000-0000-0000-0000-000000000000",
  "environments": [
    {
      "environment": "staging",
      "application_health": { "state": "warning", "data_state": "ok", "collected_at": "2026-01-15T09:28:00Z",
        "checks": [ { "name": "api-health", "healthy": false, "status_code": 503, "checked_at": "2026-01-15T09:28:00Z", "latency_ms": 42 } ] },
      "host_health": { "servers_total": 3, "healthy": 2, "warning": 0, "critical": 0, "stale": 1, "unreachable": 0 },
      "service_states": { "ACTIVE": 5, "FAILED": 1, "UNCOLLECTED": 0 },
      "current_version": { "commit_sha": "0000000", "deployed_at": "2026-01-14T18:00:00Z", "deployment_id": "00000000-0000-0000-0000-000000000000" },
      "open_alerts": { "critical": 0, "warning": 2 }
    }
  ],
  "demo_mode": false
}
```

`current_version` is `null` when no deployment has been recorded — never a guess. Checks run against **registered** endpoints only; an unconfigured application returns `data_state: "not_configured"`.

**Note (~~OPEN-02~~, resolved 2026-10-14):** applications are environment-agnostic (`AD-12`, confirmed by the product owner), so this response shape is settled rather than provisional. The per-environment alternative — which would have given `applications` an `environment` column and collapsed this endpoint to a single object — was rejected.

---

## 19. Centralized logs

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 19.1 | POST | `/api/v1/logs/query` | Search logs across authorised servers | `logs:read:application` or `logs:read:system` |
| 19.2 | GET | `/api/v1/logs/sources` | Available label values for filter UIs | `logs:read:application` |
| 19.3 | GET | `/api/v1/deployments/{deployment_id}/logs` | Build/deployment logs | `deployment:read` |

**19.1 is `POST` for a read** because the filter object is structurally too large for a query string and must not land in access logs or browser history ([SECURITY.md Privacy](SECURITY.md#17-sensitive-log-redaction)). It is non-state-changing, requires a CSRF token, and is not audited as a change (denials are audited).

```json
{
  "from": "2026-01-15T08:00:00Z",
  "to": "2026-01-15T09:00:00Z",
  "server_ids": ["00000000-0000-0000-0000-000000000000"],
  "application_id": null,
  "environment": "staging",
  "service": "placeholder-api.service",
  "source_type": "journald",
  "level": ["error", "warn"],
  "contains": "placeholder error text",
  "pattern_preset": "http_5xx",
  "limit": 200,
  "direction": "backward",
  "cursor": null
}
```

**No LogQL, no regex, no label selector is accepted from the client** (AD-05). `contains` is a literal substring, escaped before being placed in a line filter. `pattern_preset` selects from a server-defined catalogue of common error patterns (PRD §4.7 "search for common error patterns") — which is how regex power is offered without accepting regex.

Validation: `from < to`, max range **7 days** (`422 query_limit_exceeded`); `limit` default 200, max 1 000; `contains` ≤ 256 chars; `level` and `source_type` from closed enums; at least one of `server_ids`, `application_id`, `environment` is required — an unbounded query across the estate is rejected; `server_ids` ≤ 50.

**Authorisation is applied as a filter, not a check:** the caller's readable servers/environments are intersected into the selector before dispatch, so an over-broad filter narrows silently rather than leaking which servers exist. `excluded_by_scope_count` reports how many requested servers were dropped. `logs:read:system` is required for `source_type` ∈ `{journald, syslog, nginx, php_fpm, docker}`; `logs:read:application` suffices for application sources (PRD §4.7 production-log restriction).

```json
{
  "entries": [
    { "timestamp": "2026-01-15T08:59:59.123Z", "server_id": "00000000-0000-0000-0000-000000000000",
      "hostname": "placeholder.internal.example", "service": "placeholder-api.service",
      "source_type": "journald", "level": "error", "line": "Placeholder log line [redacted]" }
  ],
  "page": { "limit": 200, "next_cursor": "b3BhcXVl", "has_more": true, "total": null },
  "query_stats": { "duration_ms": 0, "bytes_scanned": 0, "truncated": false },
  "excluded_by_scope_count": 0,
  "data_state": "ok",
  "source": "loki"
}
```

Every line passes through redaction. Errors: `403`, `422 query_limit_exceeded`, `503 provider_unavailable` (Loki down — rendered as an explicit unavailable state, never as "no logs found", which would be a dangerous lie during an incident), `504 provider_timeout`. Timeout 15 s. Rate limit 30 / min.

**Implemented in M3.2 (§19.1, §19.2 and §16.7).** Where the build differs from, or adds to, the text above, each for a reason found while building:

- **The route's permission is `logs:read:application`** (every role holding `logs:read:system` also holds it). The system permission is enforced **per server and per source type inside the query** (`OPEN-32`). A request naming a source type the caller can read on none of the selected servers is `403` and an audited denial; where only some servers lack it, those are narrowed away and counted in `excluded_by_scope_count`.
- **Extra response fields.** `servers_searched` (servers covered after scoping; `0` is an honest "nothing in scope"), `ingestion` (below), per-entry `line_truncated`. `query_stats.bytes_scanned` is **nullable**: `null` when Loki does not report it, never `0`. `hostname` is the inventory's, not the shipper's label. `excluded_by_scope_count` counts only *explicitly requested* `server_ids`; for an `environment` or `application_id` selection it is `0` (a count would say how many servers exist outside the caller's reach).
- **`ingestion` (`OPEN-34`, PROGRESS issue 93)** is a list of `{server_id, hostname, state}` for the servers searched, on the **first page only**: `receiving` (lines in the last 15 minutes), `quiet` (none in 15 minutes, some in 24 hours), `silent` (none in 24 hours), `unknown` (Loki could not say; never reported as `silent`). **A client must not render an empty page as "no matching lines" unless every state is `receiving`.** It is computed for the whole server (any source type), so it says whether the shipper is talking, not what it said.
- **`application_id` is the application's UUID** (`OPEN-33`). It is a narrowing filter only: it adds no reach.
- **Cursor (`OPEN-36`)** is signed, bound to the filter, direction and the caller's source-type groups (a replay against another filter, or after the caller's reach changed, is `422 invalid_cursor`), and carries a timestamp plus how many lines at that nanosecond were already delivered. A page whose boundary tie group cannot be reproduced is `422 invalid_cursor`, never a repeat or a gap. A group of lines sharing one nanosecond larger than a query can return (5 000) is `503 provider_unavailable`.
- **Other errors.** `422 query_limit_exceeded` also covers a `from` older than retention (`details.limit = retention_seconds`, `OPEN-37`) and more than 200 servers after scoping (`details.limit = servers`); the request-level `server_ids` cap of 50 is an ordinary `422`. `contains` is case-sensitive and control characters are refused. `level` takes the closed values `debug|info|warn|error|critical|unknown` (AD-21).
- **`503 not_configured`** with no `DCP_LOKI_URL`; `503 provider_unavailable` (`details.reason` is one of a closed set, including `circuit_open`); `504 provider_timeout`. Loki's response body is never returned or logged.
- **§19.2 is the static catalogue**, not a Loki query: `source_types` (only those the caller can read on some server), `levels`, `environments`, `presets` (`id`, `label`, `description`; the expressions stay server-side) and `limits`. Servers come from §14.1.
- **§16.7 is answered by the same engine**, restricted to one registered unit and `source_type = journald`; its response is the §19.1 shape. Its `level` takes the same closed values (M2.4 had accepted `notice` and `warning`, which Loki never holds; §16.7 had never answered, so nothing depended on them). A unit whose name the shipper cannot carry as a plain label (an instance unit such as `getty@tty1.service`, or one over 63 characters) is shipped as `other` and is refused with `422` rather than answered empty.
- **Rate limit** 30 a minute per identity, failing open if Redis is down. `POST /logs/query` is allowed in read-only mode.
- **Not implemented:** §19.3 (deployment logs) is M5.

**19.3** Streams Jenkins console output, paginated, redacted, gated on `deployment:read` for that environment. `503` when Jenkins is unavailable, with `last_known_offset` preserved.

---

## 20. Alerts

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 20.1 | GET | `/api/v1/alerts` | List alerts | `alert:read` |
| 20.2 | GET | `/api/v1/alerts/{alert_id}` | Alert detail and history | `alert:read` |
| 20.3 | POST | `/api/v1/alerts/{alert_id}/acknowledge` | Acknowledge | `alert:acknowledge` |
| 20.4 | POST | `/api/v1/alerts/{alert_id}/assign` | Assign to a user | `alert:acknowledge` |
| 20.5 | POST | `/api/v1/webhooks/alerts` | Alertmanager ingress | Shared secret (not a session) |
| 20.6 | POST | `/api/v1/alerts/{alert_id}/notifications/{delivery_id}/retry` | Retry a failed or abandoned notification delivery | `alert:acknowledge` |

**Implementation status (2026-10-18, M3.3): §20.1 - §20.5 are implemented.** What the build added or changed, each for a reason found while building or measuring:

**20.1** Query: `status` (repeatable; default `FIRING` and `ACKNOWLEDGED`, i.e. what is still open), `severity` and `environment` (repeatable), `target_type`, `target_id`, `from` and `to` (applied to `last_seen_at`: "active in the window"), `q` (free text over summary and rule name, `%` and `_` literal), `cursor`, `limit` (1 - 200, default 50). An unknown parameter is `422 unknown_field`, never a silent unfiltered list. Newest activity first; keyset-paged on `(last_seen_at, id)`; a cursor is signed **and bound to the filters that minted it** (`422 invalid_cursor` against any other). The response adds **`coverage`**: `produced` (the conditions that can fire) and `not_produced_yet` (PRD §4.8's conditions with no producer, each with the milestone that brings it), so a page cannot imply a condition is monitored when nothing can raise it (`OPEN-40`). Items carry `{alert_id, source, rule_name, severity, status, summary, target, environment, first_seen_at, last_seen_at, occurrence_count, acknowledged_by, acknowledged_at, assigned_to, resolved_at, links}`; `target` is `{type, id, display_name}` **as found by lookup**, `acknowledged_by` and `assigned_to` are `{id, display_name}` (no email), `links` are API routes (`server`, `service`).

**Scope.** An alert is reached through the server it is about (a service alert through the service's server) and the *current* server row decides, not the snapshot in `environment`. **An alert about no server -- unlinked, or a `DCP_SYSTEM` alert -- is visible only to a grant that constrains no dimension** (every dimension `["*"]`, with `environment` allowed to list all four); a scoped reader never sees one (`OPEN-45`). An alert the caller may not read is `404`, the same answer as one that does not exist.

**20.2** adds to the item: `details` (the stored labels and annotations, **untrusted text**), `history` (`first_seen`, `acknowledged`, `assigned`, `unassigned`, `resolved`, `expired`, each with actor, subject and note where they apply) and `previous_occurrences` (the last 10 closed alerts with the same dedup key). `resolved_at` is set **only** when the source said the problem cleared; acknowledgement never sets it and expiry does not (a schema CHECK: `resolved_at` is non-null exactly when `status = RESOLVED`).

**20.3** is as written: `FIRING` only, `409 invalid_state_transition` (with `details.current_status`) otherwise, and a lost race with another acknowledgement or with the source's resolve is the same `409` -- one winner, one audit row. Audit `ALERT_ACKNOWLEDGED`, target `alert_event`; the note is redacted.

**20.4** body is `{"assignee_id": "<uuid>" | null, "note": "<=500 chars"}`; `null` unassigns. Legal on `FIRING` and `ACKNOWLEDGED`, `409` otherwise. **The assignee must be an active user holding `alert:acknowledge` whose scope reaches this alert**; every failure is the same `422 invalid_assignee`, so the endpoint cannot be used to probe which users exist. Audit `ALERT_ASSIGNED`. There is **no manual resolve** and no "close": only the source resolves, and an alert nobody confirms either way becomes `EXPIRED` (below).

**20.5** is implemented as specified, with the order and the numbers pinned (`OPEN-43`): `503 not_configured` without `DCP_ALERT_WEBHOOK_SECRET` (never open); the **TCP peer** against `DCP_ALERT_WEBHOOK_ALLOWED_CIDRS` (empty denies all; `X-Forwarded-For` is never read) -> `403`; a **rate limit of 120 per minute per peer, applied before the secret is compared**, `429` with `Retry-After`, failing open when Redis is down; `Authorization: Bearer` compared in constant time -> `401`; a body ceiling of 256 KiB enforced while streaming -> `413`; a strict envelope (`version` `"4"`, unknown fields refused) -> `422`. The payload shape is **what a real Alertmanager 0.27 sends**, including `truncatedAlerts`, which appears only when Alertmanager's own `max_alerts` dropped alerts. Inside a `202`, each alert is accepted (`created`, `updated`, `resolved`, `ignored` for a `resolved` that matches nothing open, `watchdog`) or rejected (`malformed`, `unknown_rule`, `overflow` past 100 in one request, `database_rejected`); a *database* failure is a `5xx`, because answering `202` to an alert that was not stored would have Alertmanager believe it delivered it. **Measured against a real Alertmanager, a `4xx` or `429` is not retried within one attempt but the group is re-sent every `group_interval` until accepted** (a `503` is retried with backoff as well), so an individually bad alert must be a per-alert rejection and never a `4xx`. Only the rules DCP generates are accepted (`unknown_rule` otherwise); severity, the discriminators of the dedup key, the target and the environment are all **looked up**, never read from the payload; `generatorURL` is dropped. `DcpWatchdog` (always firing) records only "last seen" in Redis and stores no row. Audit `ALERT_INGESTED` is one row per delivery that stored or rejected something (not for a watchdog-only delivery), actor `system:alertmanager`, metadata counts only.

**Notifications (M3.4, `OPEN-50` - `OPEN-56`).** **20.1's `coverage` gains `notification_channels_active`** (the number of `ACTIVE` channels): `0` means alerts are shown and sent nowhere, which a page says rather than leaves to be assumed. **20.2 gains `notifications[]`**, one entry per (alert, channel): `delivery_id`, `channel_id`, `channel_name`, `channel_type`, `status` (`PENDING` | `SENT` | `FAILED` | `ABANDONED`), `attempt_count`, `last_error` (**a closed reason code**, never a URL or the peer's words: `http_404`, `timeout`, `connect_failed`, `dns_failed`, `destination_refused`, `url_not_allowed`, `redirect_refused`, `secret_unresolvable`, `channel_disabled`, `channel_rules_invalid`, `alert_missing`, `smtp_busy`, `smtp_rejected`, `smtp_auth_failed`, `smtp_tls_unavailable`, `smtp_not_configured`, `tls_failed`; clients tolerate unknown values), `next_attempt_at`, `sent_at`, `created_at`, `retryable`. `FAILED` is a **permanent** failure; `ABANDONED` is **transient failures that used the whole retry budget** (`OPEN-53`). A delivery is created only when an alert **row** is created (`OPEN-51`): not for a repeat, an acknowledgement, a resolve or an expiry.

**20.6** `202`, no body. Legal only for `FAILED` and `ABANDONED` on an `ACTIVE` channel, through an alert the caller can read (`404` otherwise, also for a delivery that belongs to a different alert). `409 invalid_state_transition` (`details.current_status`) for `PENDING` and `SENT` -- **a delivered message is never re-sent by a button** -- and for a lost race (two people retrying at once: one winner, one audit row); `409 conflict` with `details.reason = "channel_disabled"` when the channel is off. Resets `attempt_count` to 0 and makes the delivery due now. Audit `NOTIFICATION_RETRY_REQUESTED`, target `notification_delivery`, with the previous status and attempt count. Blocked in read-only mode (`423`). The response is the updated `DeliveryOut`.

**Lifecycle statuses** (`OPEN-44`): `FIRING` and `ACKNOWLEDGED` are *open*; `RESOLVED` is set by the source; **`EXPIRED`** is set by a worker task when an open alert has not been refreshed for three Alertmanager repeat intervals (3 h) -- "not confirmed either way", never shown as resolved. A recurrence after any close is a **new row**. Warning and critical for one condition are different rules, mutually exclusive, and so different rows (`OPEN-41`).

**Dashboard and server page.** `active_alerts` (§13.1) is now a real section: open counts by severity over the servers the caller may read and the filters keep, `unavailable` with reason `alert_read_not_permitted` for a caller with no `alert:read`, and **`pipeline`** (`confirmed`, `not_confirmed`, `unknown`, or `null` when no Alertmanager is configured) saying whether the always-firing watchdog has recently proved alerts would arrive -- `0` beside `not_confirmed` means "cannot tell". §13.2's `active_alert_count` is a real per-server count (`null` only when the caller may not read alerts for that server) and §14.3's `open_alerts` carries `critical` and `warning` counts. §26.1's Alertmanager row is `ok` only when `/-/ready` answers **and** a watchdog arrived within two repeat intervals; otherwise it says which failed, as a reason code from a closed set.

---

## 21. Deployments

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 21.1 | GET | `/api/v1/deployments` | List deployments | `deployment:read` |
| 21.2 | POST | `/api/v1/deployments` | Request a deployment | `deployment:create` on the environment |
| 21.3 | GET | `/api/v1/deployments/{deployment_id}` | Detail incl. health checks | `deployment:read` |
| 21.4 | POST | `/api/v1/deployments/{deployment_id}/approve` | Approve | `deployment:approve` |
| 21.5 | POST | `/api/v1/deployments/{deployment_id}/reject` | Reject | `deployment:approve` |
| 21.6 | POST | `/api/v1/deployments/{deployment_id}/cancel` | Cancel | requester or `deployment:approve` |
| 21.7 | POST | `/api/v1/deployments/{deployment_id}/rollback` | Roll back, where supported | `deployment:create` on the environment |
| 21.8 | GET | `/api/v1/deployment-targets` | List registered targets (what may be deployed) | `deployment:read` |

**21.2** `Idempotency-Key` required.

```json
{
  "application_id": "00000000-0000-0000-0000-000000000000",
  "environment": "production",
  "ref": "release/0.0.0",
  "reason": "Placeholder change reference CR-0000"
}
```

**No Jenkins URL, job name, build parameter, node label or credential field exists in this body, and none may be added** (PRD §4.10). The pipeline, parameters and allowed refs come from the registered `deployment_target` for `(application_id, environment)`.

Validation: a registered `ACTIVE` target must exist for the pair (`422 not_registered` — not `404`, because the application does exist); `ref` must match one of the target's `allowed_ref_patterns` (`422 not_permitted`, with the allowed patterns in `details`); caller must hold `deployment:create` on that **environment** (a Developer holding only non-production scope gets `403` for production); `reason` required by policy for production. Approval policy evaluated server-side.

Response `202` echoes the resolved target so the UI can show the exact deployment target before and after submission (PRD §4.10):

```json
{
  "deployment_id": "00000000-0000-0000-0000-000000000000",
  "execution_status": "AWAITING_APPROVAL",
  "approval_status": "PENDING",
  "target": {
    "application_id": "00000000-0000-0000-0000-000000000000",
    "application_name": "Placeholder App",
    "environment": "production",
    "pipeline_reference": "placeholder/job/path",
    "requested_ref": "release/0.0.0",
    "rollback_supported": true
  },
  "approval": { "approval_id": "00000000-0000-0000-0000-000000000000", "required_approvals": 1, "expires_at": "2026-01-15T10:30:00Z" },
  "deadline_at": "2026-01-15T10:30:00Z",
  "correlation_id": "00000000-0000-0000-0000-000000000000",
  "poll_after_seconds": 5,
  "idempotent_replay": false
}
```

Errors: `403`, `409 conflict` (idempotency reuse), `409 operation_in_progress` (an active deployment already exists for this application+environment), `422`, `423`, `429`, `503 not_configured`. Audit `DEPLOYMENT_REQUESTED` / `DEPLOYMENT_DENIED`.

**21.3** Adds `commit_sha`, `external_execution_id`, `started_at`, `completed_at`, `health_check_result`, `sanitized_result`, `last_poll_error`, `approvals[]`, `rollback_of_deployment_id`, `links.logs`.

```json
{
  "deployment_id": "00000000-0000-0000-0000-000000000000",
  "execution_status": "FAILED",
  "commit_sha": "0000000000000000000000000000000000000000",
  "external_execution_id": "placeholder-build-0",
  "health_check_result": {
    "policy": "all_must_pass",
    "checks": [ { "name": "api-health", "healthy": false, "status_code": 503, "attempts": 3, "checked_at": "2026-01-15T10:05:00Z" } ],
    "passed": false
  },
  "failure_reason": "HEALTH_CHECKS_FAILED",
  "pipeline_result": "SUCCESS",
  "last_poll_error": null,
  "completed_at": "2026-01-15T10:05:30Z"
}
```

This example is the required behaviour from PRD §4.10: the pipeline succeeded but the health checks did not, so the deployment is `FAILED` — **not** `SUCCEEDED` (AD-10, mandatory rule 8).

**21.4 / 21.5** Body `{"reason": "Placeholder approval note"}` (required on reject). Legal only from `AWAITING_APPROVAL`. **Self-approval is rejected with `403 forbidden` (`details.reason = "self_approval_not_permitted"`) unless the governing policy explicitly allows it** (PRD §3.6). A second decision by another approver on an already-decided request gets `409`. Audit `DEPLOYMENT_APPROVED` / `DEPLOYMENT_REJECTED` with the approval reference.

**21.7** Only when the target declares `rollback_supported` — otherwise `422 not_permitted` (`details.reason = "rollback_not_supported_by_target"`). DCP never invents a rollback mechanism. Creates a **new** deployment with `kind: "ROLLBACK"`; the original moves to `ROLLED_BACK` only once the rollback deployment succeeds.

---

## 22. Approvals

**Built in M4.5** (`OPEN-93` – `OPEN-104`): the `approvals` table these endpoints read arrives in migration `0012`. Only `operation_type = SERVICE_OPERATION` exists; deployments reuse the table and these rules in M5.4 (§21.4, §21.5).

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 22.1 | GET | `/api/v1/approvals` | Pending/decided approval queue | `operation:approve` |
| 22.2 | GET | `/api/v1/approvals/{approval_id}` | Request detail for a decision | `operation:approve` |
| 22.3 | POST | `/api/v1/operations/{operation_id}/approve` | Approve an operation | `operation:approve` |
| 22.4 | POST | `/api/v1/operations/{operation_id}/reject` | Reject an operation | `operation:approve` |

A holder of only `deployment:approve` has no use for these in M4.5 and is refused by the route (`403`).

**22.1** Query: `decision` (`PENDING` by default; `APPROVED`, `REJECTED`, `EXPIRED`), `operation_type` (`SERVICE_OPERATION` only: `DEPLOYMENT` is `422 not_permitted` until M5.4), `environment`, `limit` (default 50, max 200: more is `422`, §5), `cursor` (§5; `invalid_cursor` is `422`), ordered newest first. A `PENDING` request whose window has already passed is not listed (it is the sweep's, and no one can still decide it). **Scope is applied in the SQL:** only requests whose target server the caller may approve on (`operation:approve` over the server's stored environment) are counted or returned, so a request outside the caller's reach is absent, not forbidden. Items carry the full decision context:

```json
{
  "items": [{
    "approval_id": "00000000-0000-0000-0000-000000000000",
    "operation_id": "00000000-0000-0000-0000-000000000000",
    "operation_type": "SERVICE_OPERATION",
    "requested_by": { "id": "00000000-0000-0000-0000-000000000000", "display_name": "Example Operator" },
    "target": { "server_id": "00000000-0000-0000-0000-000000000000", "server_display_name": "Placeholder Web 01", "service_name": "placeholder-api.service", "environment": "production" },
    "action_type": "restart",
    "reason": "Placeholder change reference CR-0000",
    "decision": "PENDING",
    "expires_at": "2026-01-15T10:30:00Z",
    "created_at": "2026-01-15T09:30:00Z",
    "decided_at": null,
    "approved_by": null,
    "self_requested": false,
    "self_approval_allowed": false
  }],
  "page": { "limit": 50, "next_cursor": null, "has_more": false, "total": null }
}
```

`self_requested` is true when the requester is the caller; `self_approval_allowed` is the governing policy's `allow_self_approval` (the built-in default is `false`). The UI disables the control when the first is true and the second is false — that is advice, and §22.3 refuses it again.

**22.2** Adds `policy` (`required_approvals`, `allow_self_approval`, `required_role`, `approval_window_seconds`, and `policy_id`, `null` for the built-in default), `decisions_so_far[]` (`approved_by`, `decision`, `reason`, `decided_at`; at most one in M4.5) and `target_state`: the service's **last observed** state with `observed_at` and `data_state` (`ok` | `stale` | `unavailable`) — a pre-flight read is not made at request time, and the approver is told that. `404` for an unknown approval or one the caller may not approve on.

**22.3 / 22.4** Body `{"reason": "Placeholder approval note"}` (`reason` is optional on approve and **required on reject**, 1 – 500 printable characters). The response is the §17.2 operation detail. Checks, in order: session → `operation:approve` → the operation exists and is visible to the caller (`404`) → it is `AWAITING_APPROVAL` with a `PENDING` approval (`409 invalid_state_transition`; `details.reason = "approval_expired"` when the window has passed — the sweep's effect is applied first — otherwise `"not_awaiting_approval"`) → scope: `operation:approve` over the stored environment and server (`403`, audited `SERVICE_OPERATION_DENIED`) → **separation of duties** (`403` with `details.reason = "self_approval_not_permitted"` unless the governing policy explicitly allows it, PRD §3.6) → the policy's `required_role` (`403`, `details.reason = "approver_role_required"`) → a reject without a reason is `422`.

The decision is one transaction: the approval row, the operation (`APPROVED` with `approval_status = APPROVED` and `deadline_at = decided_at + the action policy's timeout`; or `REJECTED`), the audit row, and the resolution of the pending-approval alert commit together. The loser of any race — two approvers, an approver against a cancel, an approver against the expiry sweep — gets `409`, never a false success. On approval the execution task is enqueued **after the commit** (the healer re-enqueues an unclaimed `APPROVED` row if the message is lost). The worker **re-checks from evidence** — an `approvals` row with `decision = APPROVED`, an approver who is still active, in scope and in role, not the requester unless the policy allows it, inside the window — and refuses with `failure_reason = APPROVAL_REQUIRED` and a closed `sanitized_result.reason_code` (`approval_missing`, `approval_not_approved`, `approver_revoked`, `self_approval`, `approval_expired`) if any of it is gone ([SECURITY.md §16](SECURITY.md#16-safe-start--stop--restart-workflow), §22). Audit `SERVICE_OPERATION_APPROVED` / `SERVICE_OPERATION_REJECTED` with the approval reference; a refusal is `SERVICE_OPERATION_DENIED` (`reason` one of `self_approval_not_permitted`, `approver_role_required`, `out_of_scope`). A rejecter's reason is audited by its length only.

**Expiry.** A request nobody decides becomes `EXPIRED`, its operation `CANCELLED`, and its slot is freed, by the reaper's sweep (every 30 s), audited `SERVICE_OPERATION_CANCELLED` by `system:approval-sweeper` on behalf of the requester. A decision after the window is `409 approval_expired` whether or not the sweep has run.

**Notification.** A pending request opens a `WARNING` alert `ApprovalPending` on the target service (§20), delivered to the existing notification channels by their existing routing; it resolves on approve, reject, expiry or cancel (`OPEN-101`).

---

## 23. Operations history

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 23.1 | GET | `/api/v1/operations` | Operation history | `operation:read` |
| 23.2 | GET | `/api/v1/servers/{server_id}/operations` | Per-server history | `operation:read` |

Query: `execution_status`, `action_type`, `environment`, `server_id`, `service_id`, `requested_by`, `from`, `to`, cursor pagination, `sort` ∈ `{created_at, completed_at}`. Scope-filtered. Items carry status, target, actor, approval reference, timings, `failure_reason` and `correlation_id`; `sanitized_result` is returned only on the detail endpoint (17.2) to keep list payloads small and reduce redaction surface. Max range 90 days per query.

**Specified in M4.6 (`OPEN-107`, `OPEN-108`).**

- **Whose rows (23.1).** A row is listed when the caller **requested it, or** holds `operation:read` over the operation's **stored** `environment` and the target server's account and id. One SQL predicate, applied inside the query: rows outside it are never fetched, counted or paged over. This is the same basis as §17.2, which now also asks about the *stored* environment (it asked about the server's current label before M4.6; PROGRESS issue 164). `requested_by` narrows the result, it never widens it.
- **Per server (23.2).** The server must be visible to the caller through `server:read`, else `404` (never an empty list); the query is then 23.1's with the server fixed. A caller with `operation:read` but no `server:read` over a server therefore gets that server's operations from 23.1 and `404` from 23.2.
- **Filters.** `execution_status`, `action_type` and `environment` are the closed enums of §9 (`422` otherwise); `server_id`, `service_id` and `requested_by` are UUIDs. Plural filters repeat (§6). `from` / `to` bound **`created_at`**, whichever `sort` is chosen. Omitted ⇒ the last 7 days; `from` after `to`, or a range over 90 days ⇒ `422`.
- **Paging and sort.** The project's signed cursor (§5), default `limit` 50, max 200, `order` default `desc`, `total: null`. `sort=created_at` is the default. **`sort=completed_at` orders operations with no completion time (still in flight) last in both directions**; the cursor carries that as an explicit marker and compares on `(completed_at, id)` as a pair, so a page boundary neither repeats nor skips a row.
- **Item fields.** `operation_id`, `execution_status`, `approval_status`, `action_type`, `target {server_id, server_display_name, service_name, environment}`, `requested_by {id, display_name}`, `approval {approval_id, decision, approved_by, decided_at}` or `null`, `created_at`, `started_at`, `completed_at`, `deadline_at`, `failure_reason`, `correlation_id`, and — because PRD §4.12 says history "includes the result and associated execution id" — **`external_execution_id`, `observed_state_before` and `observed_state_after`**. Not on a list item: `requested_reason` (user text), `sanitized_result`, `parent_operation_id` — those are §17.2's.
- **Errors.** `401`, `403` (no `operation:read` anywhere), `404` (23.2's server), `422`.
- **Not audited as a read.** The history is not the audit trail; only reads of the trail are audited (§24).

---

## 24. Audit events

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 24.1 | GET | `/api/v1/audit-events` | Query the audit trail | `audit:read` |
| 24.2 | GET | `/api/v1/audit-events/{event_id}` | Single event | `audit:read` |
| 24.3 | POST | `/api/v1/audit-events/export` | Request a bounded export | `audit:read` + `admin:integrations` |
| 24.4 | GET | `/api/v1/audit-events/exports/{export_id}` | Export status (added in M4.6, `OPEN-112`) | `audit:read` + `admin:integrations` |
| 24.5 | GET | `/api/v1/audit-events/exports/{export_id}/download` | Download the export, once (added in M4.6, `OPEN-112`) | `audit:read` + `admin:integrations` |

**There is no `POST`, `PATCH` or `DELETE` on an audit event.** Audit events are written only as a side effect of the actions they describe, and the application's database role has no `UPDATE`/`DELETE` grant on the table (AD-11, PRD §4.12 acceptance criterion: "users cannot alter audit records through ordinary application APIs").

**24.1** Query: `actor_id`, `actor_type`, `action_type`, `target_type`, `target_id`, `environment`, `outcome`, `correlation_id`, `from`, `to`, cursor pagination. Max range 90 days per query; `from`/`to` required beyond 7 days. Returns redacted `sanitized_metadata` only. Audited itself as `AUDIT_QUERIED` — reading the audit trail is a privileged act.

**24.3** Body `{"from","to","format":"jsonl"}`; max 31-day window. Runs asynchronously on the `maintenance` queue, returns `202` with an export id; the result is retrievable once by an authorised identity and is audited as `AUDIT_EXPORTED`.

**Specified in M4.6 (`OPEN-109` – `OPEN-113`).**

**Scope (24.1, 24.2, and what an export contains) — `OPEN-109`.** `audit:read` is evaluated against the **event's `environment`** by the matcher's rules (a deny wins; production only when named; malformed means deny). An audit event has no `cloud_account`, `server` or `application` dimension, so **a grant that constrains one of those reaches no audit row** (SECURITY §4: a dimension the target does not have is a denial). **An event with no environment** (a login, a role grant, a user change, a system event) **is visible only to a grant that constrains nothing** — every dimension absent or `["*"]`, which is the seeded Administrator. A production-scoped Approver sees production events and not these. A request for an id the caller may not see is `404`, never `403`.

**24.1** Fields returned (`OPEN-111`): `id`, `occurred_at`, `actor_type`, `actor_id`, `actor_label`, `action_type`, `target_type`, `target_id`, `environment`, `correlation_id`, `approval_id`, `outcome`, `source_ip`, `user_agent` and `sanitized_metadata`. Metadata is redacted on write and **again on the way out**. Filters are enums and UUIDs only (`actor_type`, `outcome`, `target_type` closed; `actor_id`, `target_id`, `correlation_id` UUIDs; `action_type` is an exact match on a value of `^[A-Z_]{1,64}$` — the catalogue is extensible, so it is not an enum — and never a pattern); there is no free-text search and no search of metadata. **Window:** `from` / `to` are optional; omitted ⇒ the last 7 days; any range beyond 7 days needs both, and a range over 90 days or `from` after `to` ⇒ `422`. Cursor pagination on `(occurred_at, id)` descending, default `limit` 50, max 200, `total: null`. **24.2** returns one event under the same scope; unknown or out of scope ⇒ `404`.

**Audited as a read (`OPEN-110`).** 24.1 and 24.2 write one `AUDIT_QUERIED` row in the request's own transaction: actor the caller, `target_type = 'audit'`, no environment, outcome `SUCCESS`, metadata the **filter names and their id/enum values, the page size, the count returned and `has_more`** — never the rows and never a cursor. An empty result is audited too. If the audit write fails the read fails (SECURITY §18). A caller refused by permission or scope gets the existing `AUTHZ_DENIED`, not `AUDIT_QUERIED`. Both routes take the read-rate budget.

**24.3 – 24.5, the export (`OPEN-112`).**

- **24.3** requires `Idempotency-Key` (§7), `audit:read` **and** `admin:integrations` (so Administrator-only), and answers `202 {export_id, status: "QUEUED", expires_at}`. Window ≤ 31 days and `from` before `to` (`422`); `format` accepts only `jsonl`; unknown fields `422`. **One active (`QUEUED` or `RUNNING`) export per requester** — a second is `409 export_in_progress`. A replay of the key returns the original. Audit `AUDIT_EXPORT_REQUESTED` (window only).
- **The task** `dcp.audit.export` takes the export id and nothing else. It re-resolves the requester's grants and re-checks both permissions (a requester demoted since asking ends `FAILED`, `failure_code = requester_not_authorised`, and no data is produced), applies the same scope predicate as 24.1, reads in keyset chunks, redacts again and writes `jsonl`, one event per line with the 24.1 fields. **Bounds, design budgets and not measurements:** at most 100 000 rows and 64 MiB. Over either, the export ends `FAILED` with `failure_code = export_too_large` — **never truncated** — and the requester asks for a narrower window. Other closed codes: `requester_not_authorised`, `internal_error`. Audit `AUDIT_EXPORT_FAILED` (actor `system:audit-export`, on behalf of the requester, the code only).
- **24.4** returns `{export_id, status, from, to, row_count, size_bytes, sha256, failure_code, created_at, completed_at, expires_at, retrieved_at}`. `status` ∈ `QUEUED | RUNNING | READY | FAILED | RETRIEVED | EXPIRED`. Visible to the requester only; anyone else, and an unknown id, is `404`.
- **24.5** returns the file (`application/x-ndjson`, `Content-Disposition: attachment`, `Cache-Control: no-store`) **exactly once**, to its requester, while `READY` and before `expires_at`. The read is one guarded statement that spends the export (`RETRIEVED`, content deleted) and returns the bytes; a second call, another user's call, an expired or failed export are `409 export_not_available` or `404` and **never a file**. If the transfer then fails the export is spent and the requester asks again — the response and the UI say so. Audit `AUDIT_EXPORTED` **at this moment — the point where data leaves** — with row count, size and hash, and never content.
- **Expiry.** A `READY` export not downloaded within 24 hours becomes `EXPIRED` with its content deleted, by the existing reaper, audited as `AUDIT_EXPORT_EXPIRED` (actor `system:audit-export-sweeper`).
- **Storage** is the `audit_exports` table ([DATABASE.md §8.1](DATABASE.md#81-audit_exports--added-entity-m46)); the broker carries the id only.

**No audit event is ever written, changed or deleted by an API.** The only `POST` under `/audit-events` is 24.3, which creates an export row, not an event; `PATCH`, `PUT` and `DELETE` on any `/audit-events` path are `405` (`OPEN-113`, tested against the live routing table).

---

## 25. User and role administration

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 25.1 | GET | `/api/v1/admin/users` | List users | `admin:users` |
| 25.2 | POST | `/api/v1/admin/users` | Pre-create a user | `admin:users` |
| 25.3 | GET | `/api/v1/admin/users/{user_id}` | Detail incl. role grants | `admin:users` |
| 25.4 | PATCH | `/api/v1/admin/users/{user_id}` | Enable/disable, edit display name | `admin:users` |
| 25.5 | POST | `/api/v1/admin/users/{user_id}/roles` | Grant a role with scope | `admin:roles` |
| 25.6 | DELETE | `/api/v1/admin/users/{user_id}/roles/{user_role_id}` | Revoke a grant | `admin:roles` |
| 25.7 | POST | `/api/v1/admin/users/{user_id}/sessions/terminate` | Terminate all sessions | `admin:users` |
| 25.8 | GET | `/api/v1/admin/roles` | List roles and their permissions | `admin:roles` |
| 25.9 | GET | `/api/v1/admin/permissions` | Permission catalogue | `admin:roles` |

**No endpoint sets, resets or returns a password** — none exists ([DATABASE.md §3.1](DATABASE.md#31-users)).

**25.5** Body:

```json
{
  "role_name": "DEVOPS_ENGINEER",
  "scope_definition": { "environment": ["staging"], "server": ["*"] },
  "expires_at": null
}
```

Validation: `role_name` ∈ the five roles; `scope_definition` keys ∈ `{environment, cloud_account, server, application}`, values UUIDs or `"*"`, `"*"` not combined with specific values; referenced ids must exist; **granting `environment: ["production"]` requires the granting Administrator to hold production scope themselves** (no privilege escalation through delegation — refused with `422 not_permitted` and audited `AUTHZ_DENIED`); `expires_at` must be in the future and carry an offset. Errors `409 duplicate_resource` on an identical grant, `422`, `403`, `404` (unknown user), `423` in read-only mode. Side effects: **none beyond the row and its audit event** — grants are resolved per request from PostgreSQL with no cache, so the change takes effect on the target's next request without a re-login and without an invalidation step (PRD §4.1 acceptance criterion; amended in M1.4, see [ARCHITECTURE.md §9.2](ARCHITECTURE.md#92-authorization--rbac-with-scope-evaluated-per-request-ad-03)). Audit `ROLE_GRANTED` with before/after scope.

**25.2** Body:

```json
{
  "email": "operator@example.invalid",
  "display_name": "Example Operator",
  "identity_provider_subject": "00000000-0000-0000-0000-000000000000",
  "identity_provider_issuer": null
}
```

`identity_provider_subject` is **required**: the column is `NOT NULL` ([DATABASE.md §3.1](DATABASE.md#31-users)) and keying a pre-created row on email instead would require adopting it at first login, which is the identity merge assumption `A-05` refuses (`OPEN-26`). The administrator reads the subject from the IdP (`sub`, `oid`, object id). `identity_provider_issuer` defaults to the configured issuer, the only one this deployment accepts tokens from. The user is created `ACTIVE` with **no roles**; grant them separately with 25.5. Errors `409 duplicate_resource` (identity or active email already present — which of the two is deliberately not disclosed), `422`. Audit `USER_CREATED`.

**25.4** Setting `status: "DISABLED"` terminates the user's sessions in the same unit of work. Audit `USER_DISABLED`, or `USER_ENABLED` when re-enabling. A user cannot disable themselves (`422 not_permitted`) — locking the last Administrator out is a self-inflicted outage. `email` and the identity key are **not** editable: the email is refreshed from the IdP on every login, and editing the identity key would silently point one person's audit history at another's account.

**25.6** Audit `ROLE_REVOKED`. The system refuses to remove the last Administrator grant (`409 conflict`), counting only unexpired grants held by active users.

**25.7** Audit `SESSION_TERMINATED`, with the count and never a session identifier. Zero is a success: the user simply had none live.

### 25.11 – 25.14 Approval policies

Added in M4.5 at the owner's instruction (`OPEN-105`). Approval policies were seeded only; without an editor a team that cannot separate requester from approver had no way to say so from the application.

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 25.11 | GET | `/api/v1/admin/approval-policies` | List approval policies | `admin:policies` |
| 25.12 | POST | `/api/v1/admin/approval-policies` | Create a policy | `admin:policies` |
| 25.13 | PATCH | `/api/v1/admin/approval-policies/{policy_id}` | Edit a policy | `admin:policies` |
| 25.14 | DELETE | `/api/v1/admin/approval-policies/{policy_id}` | Delete an unreferenced policy | `admin:policies` |

```json
{
  "name": "production-service-operations",
  "environment": "production",
  "applies_to": "SERVICE_OPERATION",
  "required_approvals": 1,
  "allow_self_approval": false,
  "required_role": "APPROVER",
  "approval_window_seconds": 3600
}
```

Validation (`extra="forbid"`): `name` 1 – 128 characters from `[A-Za-z0-9 ._-]`, unique (`409 duplicate_resource`); `environment` one of the four environments or `*`; `applies_to` `SERVICE_OPERATION` or `DEPLOYMENT`, **fixed at creation** (not accepted by 25.13); `required_approvals` **only `1`** (a policy needing more is refused at request time, so it cannot be saved); `required_role` must name an existing role; `approval_window_seconds` 60 – 86 400. 25.13 changes only the fields it names. **25.14 answers `409 conflict` while a service action policy links the policy or any approval request references it** — removing a policy that something points at would silently remove a gate. A change takes effect on the **next** request and the next worker re-check; a request already waiting keeps the window it was created with. Each of 25.12 – 25.14 writes one `APPROVAL_POLICY_CHANGED` audit row in the same transaction, with `change` (`created`, `updated`, `deleted`) and the before and after of every field (closed values and the policy's name only). `423` in read-only mode. The response of 25.11 – 25.13 is the policy (`policy_id`, the seven fields above, `created_at`, `updated_at`); 25.14 answers `204`.

**What the editor can do, so it is not a surprise:** it can set `allow_self_approval` to `true`, and it can move a policy to another environment, either of which weakens a gate. Both are privileged, Administrator-only and audited; neither is otherwise guarded in M4.5.

### 25.10 Read-only mode

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 25.10 | POST | `/api/v1/admin/settings/read-only-mode` | Enable or disable incident read-only mode | `admin:manage` |

Added in M1.4. [SECURITY.md §27](SECURITY.md#27-incident-response) requires this flag and makes it the first containment step in an incident involving this portal; §28 already listed `READ_ONLY_MODE_CHANGED` while no endpoint existed to produce it.

```json
{ "enabled": true, "reason": "Investigating unexpected production restarts" }
```

Response: `{ "read_only_mode": true }`. `reason` is **required when enabling** (`422 not_permitted` without it) and is recorded in the audit event — a containment switch with no stated cause is an unexplained outage to whoever finds it later. The state is a durable row ([DATABASE.md §3.6](DATABASE.md#36-system_settings--added-entity-m14)), so it survives a restart and a Redis flush, and it is reported on `GET /auth/session` as `read_only_mode` ([§12](#12-authentication-endpoints)).

**While enabled**, every state-changing request to a permission-bearing endpoint is refused with `423 read_only_mode` and audited as `AUTHZ_DENIED` with `reason: read_only_mode`. Reads are untouched. Three paths deliberately keep working: the health probes, `/auth/login` and `/auth/callback` (public, no permission dependency), `/auth/logout` (containment must not trap the operator at their keyboard), and this endpoint itself — otherwise the switch could not be turned off. Audit `READ_ONLY_MODE_CHANGED`.

---

## 26. Cloud integrations

| # | Method | Path | Purpose | Permission |
|---|---|---|---|---|
| 26.1 | GET | `/api/v1/integrations/status` | Health of every integration | `admin:integrations` |
| 26.2 | GET | `/api/v1/cloud-accounts` | List accounts | `admin:integrations` |
| 26.3 | POST | `/api/v1/cloud-accounts` | Register an account | `admin:integrations` |
| 26.4 | PATCH | `/api/v1/cloud-accounts/{id}` | Edit or disable | `admin:integrations` |
| 26.5 | POST | `/api/v1/cloud-accounts/{id}/verify` | Verify credentials and permissions | `admin:integrations` |
| 26.6 | POST | `/api/v1/cloud-accounts/{id}/sync` | Trigger inventory synchronisation | `admin:integrations` |
| 26.7 | GET | `/api/v1/cloud-accounts/{id}/capabilities` | Adapter capability set | `admin:integrations` |
| 26.8 | GET | `/api/v1/notification-channels` | List channels and how their deliveries are going | `admin:integrations` |
| 26.9 | POST | `/api/v1/notification-channels` | Register a channel | `admin:integrations` |
| 26.10 | PATCH | `/api/v1/notification-channels/{id}` | Edit, enable or disable a channel | `admin:integrations` |

**26.1** Per-integration `{name, type, status, last_success_at, last_error_at, last_error_message (redacted), circuit_state, demo_mode}` for the IdP, each cloud account, Jenkins, Prometheus, Loki, the secret store and each notification channel. This is the page that answers "is the portal broken, or is the thing it monitors broken?" (PRD §4.11).

**Implemented in M2.1, with two divergences recorded rather than taken silently.** `status` is one of `ok | degraded | unavailable | not_configured`, and `not_configured` is a first-class answer: an integration nobody has configured is not "unavailable", and conflating the two sends an operator to debug a host that was never configured. Only **Prometheus** is probed live (a `vector(1)` query, which touches no series); the IdP reports configured-or-not, because a discovery fetch on every poll of a status page would be a request to the IdP per poll. The rest report `not_configured` with the milestone that brings them, which is deliberate rather than an omission — a page listing only the integrations that exist reads as "this is everything", and an operator could not tell a missing integration from a healthy one. The divergences:

1. **One `Cloud accounts` row, not one row per account.** M4.1 adds the adapter layer but no adapter that can probe an account (only the demonstration adapter exists), so N rows would carry one fact N times, and enumerating them would mean a database session on an endpoint that otherwise needs none. It becomes per-account when there is a per-account answer: §26.5 (`OPEN-64`). Its reason text says whether demonstration mode is on or that no adapter is implemented for a provider yet.
2. **`degraded` is never emitted.** Nothing persisted today can decide it honestly — a successful probe resets the circuit breaker's consecutive-failure count, so a flap verdict built on that count is always false. The row carries `last_success_at`, `last_error_at` and the previous failure's reason instead, so a caller can see that an `ok` Prometheus failed a minute ago. The value stays in the enum for the integrations that will be able to answer it.

`circuit_state` is **per-process** (`PROGRESS.md` issue 58): with more than one backend replica it describes whichever replica answered.

**26.3** Body:

```json
{
  "provider": "AWS",
  "account_reference": "000000000000",
  "display_name": "Placeholder Non-Production Account",
  "region_configuration": { "allowed_regions": ["placeholder-region-1"], "default_region": "placeholder-region-1" },
  "credential_reference": "secretstore://placeholder/path/or/role-arn"
}
```

**`credential_reference` is a pointer into the secret store or a role ARN — never a key, token or password.** A request containing a secret-shaped value is rejected with `422` (`details.reason = "credentials_must_be_stored_in_secret_manager"`) and the value is **not** echoed back ([SECURITY.md §9](SECURITY.md#9-secrets-management)). `GET` responses return the reference plus `{fingerprint, last_rotated_at}` and never a value.

**26.5** Performs a read-only permission probe (e.g. list managed instances) with a 15 s timeout and returns `{reachable, permissions_ok, missing_permissions[], checked_at, message}`. Audit `CLOUD_ACCOUNT_VERIFIED`.

**26.6** `202` with a task reference; `409 operation_in_progress` if a sync is already running for this account (advisory-locked). Audit `CLOUD_ACCOUNT_SYNC_REQUESTED`.

**26.7** Returns the capability matrix for this account's adapter so the UI can disable unsupported actions and label them "not supported by this provider" rather than letting them fail (PRD §4.11 acceptance criterion).

**Implemented in M4.1 (`OPEN-61`, `OPEN-64`).** `admin:integrations`, read budget, no audit row, no query parameters; an unknown or soft-deleted id is `404`. Response `{account_id, provider, account_status, adapter_state (demonstration | configured | not_configured), adapter_id | null, capabilities | null, data_state, reason | null, demo_mode}`. `capabilities` is `{inventory_sync, connectivity_check, actions: {start, stop, restart, status, logs_tail}, async_handle, concurrency, latency}`, each capability one of `supported | unsupported | not_configured` (*unsupported*: the provider cannot; *not_configured*: DCP has not set it up -- different answers, never merged).
- **Demonstration mode:** `adapter_state = demonstration`, `data_state = ok` (the set is a true statement about the demonstration adapter), every capability `not_configured`, `demo_mode = true`.
- **A real adapter is registered** (none until M4.3 / M4.4): `adapter_state = configured`, `data_state = ok`, `demo_mode = false`. *Configured* means a factory exists for the provider; it says nothing about credentials or reachability, which is what §26.5 verifies.
- **No adapter for the provider** (every real provider until M4.3 / M4.4, and `ON_PREM` / `OTHER`, `OPEN-62`): **`200`** with `adapter_state = not_configured`, `data_state = not_configured`, `adapter_id = null`, **`capabilities = null`** (null is unknown, never an empty set that reads as "supports nothing") and `reason = no_adapter_for_provider`. A `503` would hide the one fact an operator needs.
- `account_status` is reported, not enforced: containing a `DISABLED` account is the operation gate's job (M4.2, [SECURITY.md §14](SECURITY.md#14-server-and-service-identity-validation)).
- There is no list or fetch route for cloud accounts yet (§26.2 – §26.6 arrive with M4.3), so an id is obtained from the server inventory, which carries `cloud_account_id`.
- *(M4.3 built §26.2 – §26.6; this note described M4.1.)* An `adapter_state = configured` for AWS now means a factory exists **and** no static key is in the process environment.

**26.8 - 26.10: notification channels (M3.4, `OPEN-13`, `OPEN-48`, `OPEN-52`, `OPEN-56`, `OPEN-57`).** A channel is `{id, name, channel_type (EMAIL | TEAMS | SLACK | WEBHOOK), destination_reference, routing_rules, routing_rules_valid, status (ACTIVE | DISABLED), created_at, updated_at, deliveries}`. **`destination_reference` is a reference, never a destination:** `env://NAME` for TEAMS, SLACK and WEBHOOK (the value is the process variable `DCPSECRET_NAME`, resolved by the worker at send time), and the (non-secret) address itself for EMAIL. **A URL or token in its place is `422` with `code = credentials_must_be_stored_in_secret_manager` and is not echoed**; other `422` codes on that field: `invalid_destination_reference`, `reference_not_resolvable` (the reference does not resolve in this deployment), `destination_not_allowed` (what it resolves to is not an allowed outbound destination: host allowlist, scheme -- SECURITY.md §19), and on `channel_type`: `not_configured` (EMAIL without `DCP_SMTP_HOST`). `routing_rules` is `{severity: [...], environment: [...], target_type: [...]}`, each an optional subset of a closed enum, `extra="forbid"`; **empty or absent means "no restriction" and `{}` sends every alert**, and a channel that restricts a dimension does not receive an alert that has no value for it (an unlinked or `DCP_SYSTEM` alert has no environment). `deliveries` is `{pending, sent_24h, failed_24h, last_success_at, last_failure_at, last_error}`: counts and a closed reason code, never a destination. The list also returns `email_available` and `demo_mode`. The channel **type cannot change** (it would change what the reference means): `PATCH` with `channel_type` is `422`. A duplicate name is `409 duplicate_resource`. **Enabling** a channel (and changing its reference) re-runs the destination check. **There is no delete** (the foreign key is `RESTRICT`; disable instead) and **no send-test or verify route** (a delivery needs an alert row, and a synthetic one is the mocked-data pattern CLAUDE.md §7 forbids): a configuration mistake appears as a `FAILED` delivery with a reason code. Audit `NOTIFICATION_CHANNEL_CHANGED`, target `notification_channel`, metadata `{change: created | updated, fields: [names], channel_type, status}` -- **field names, never the reference and never a value**.

**26.1 notification rows (M3.4, `OPEN-56`).** One row per channel, named `Notifications: <channel name>`, decided by the newest *finished* delivery: `SENT` -> `ok`; `FAILED` / `ABANDONED` -> `unavailable` with that delivery's reason code; a channel that has never delivered -> **`degraded`, `no_delivery_attempted_yet`** (or `delivery_in_progress` while the first delivery is still being retried) -- unproven is not `ok`, and this is the first use of `degraded`, which M2.1 held back because nothing persisted could decide it; `DISABLED` -> `not_configured`, `channel_disabled`. No channels at all: one `Notification channels` row, `not_configured`, `no_channel_registered`. If the database cannot answer: one row, `unavailable`, `database_unreadable`. No row carries a URL or an address.

**26.4** Setting `status: "DISABLED"` immediately blocks all operations against servers in this account — a containment control ([SECURITY.md §27](SECURITY.md#27-incident-response)). In-flight operations are allowed to reach a terminal state. Audit `CLOUD_ACCOUNT_DISABLED`.
**Implemented in M4.3 (`OPEN-86` - `OPEN-88`).**
- **14.8** reads one instance through a worker thread (10 s). `reachable` is `PingStatus == Online`; an instance SSM does not know is `200` with `reachable = false`, `provider_status = not_registered_with_ssm` (an answer, not an error). `404` for an unknown or out-of-scope server, `503 not_configured` (reason `demonstration_mode`, `no_adapter_for_provider`, `account_disabled`), `422 unsupported_operation`, `423` read-only. **It changes nothing about the server**: `agent_status`, `status` and `last_seen_at` stay owned by the Prometheus probe and the sweep (`OPEN-30`).
- **26.2** `GET` lists accounts as `{items}`, **at most 200, ordered by display name, with no cursor** (a deployment has a handful; the cap is stated rather than hidden); each row has `{id, provider, account_reference, display_name, region_configuration, credential_reference, credential_fingerprint, status, last_sync_at, last_sync_error, verification, created_at, updated_at}`. `verification` is the cached §26.5 result or `null` (never verified, or the cache cannot be read). **`last_rotated_at` (§26.3's wording) is not returned**: no column records it, and an invented value would be worse than none; `credential_fingerprint` (a short digest of the reference) is.
- **Rate limits (M4.3):** §26.5 is 6 / min per account and §14.8 is 10 / min per server, both failing closed when Redis is unavailable.
- **26.3** registers **`AWS` only** in M4.3 (`422 no_adapter_for_provider` for the rest). `credential_reference` must be a role ARN `arn:aws:iam::<12 digits>:role/<name>` whose account id equals `account_reference` (`422 credential_account_mismatch` otherwise); a `secretstore://` or `env://` reference is `422 invalid_credential_reference`; a secret-shaped value is `422 credentials_must_be_stored_in_secret_manager` and is never echoed. `region_configuration.default_region` must be in `allowed_regions`. Duplicate `(provider, account_reference)` is `409 duplicate_resource`. Audit `CLOUD_ACCOUNT_REGISTERED`.
- **26.4** edits `display_name`, `region_configuration`, `credential_reference` and `status` (`ACTIVE` or `DISABLED`; `ERROR` is not settable). Changing the reference clears the cached verification. Audit `CLOUD_ACCOUNT_UPDATED` (field *names* only) and `CLOUD_ACCOUNT_DISABLED`.
- **26.5** `POST` runs `AssumeRole`, `DescribeInstanceInformation` (one page) and `DescribeDocument` for the registered document at the configured version, within 15 s, and returns `{reachable, permissions_ok, missing_permissions[], not_probed[], checked_at, message}`. `ssm:SendCommand` is **listed in `not_probed`**, never guessed: the only runtime probe would be an attempt to send a command. The result is cached for one hour and feeds §26.1. The execution role's **deny of `AWS-RunShellScript` / `AWS-RunPowerShellScript` is not probed at runtime**; it is verified offline (tests on `infra/aws/iam-execution-policy.json`, and `scripts/verify_aws_role.py`). Audit `CLOUD_ACCOUNT_VERIFIED`.
- **26.6** `202` with `{task_id}`; a Postgres advisory lock per account (held by the task for its duration) makes a second request `409 operation_in_progress`; the response is `{task_id}` and there is no route to poll it (the outcome is the audit row `CLOUD_ACCOUNT_SYNCED` and the account's `last_sync_at` / `last_sync_error`). The task **reconciles registered servers and never registers one, never changes `environment`, `status` or `management_method`** (`OPEN-88`). Audit `CLOUD_ACCOUNT_SYNC_REQUESTED` (request) and `CLOUD_ACCOUNT_SYNCED` (the task, attributed to `system:cloud-sync` on behalf of the requester, with counts: `servers_checked`, `servers_updated`, `servers_not_in_ssm`, `unregistered_managed_instances`, `unregistered_count_truncated`). **A value a person entered (`os_version`, `private_ip`) is filled in only when empty**; what SSM reported is kept in `provider_metadata.ssm` (`platform_type`, `agent_version` only: a ping status that flips would make every edit form's `If-Unmodified-Since` stale).
- **26.1 per-account rows.** One row per AWS account named `Cloud account: <display name>`, decided by the cached §26.5 result: never verified is `degraded` / `not_verified_yet` (unproven is not `ok`); a good result is `ok`; a failed one is `unavailable` with its closed reason; `DISABLED` is `not_configured` / `account_disabled`. No AWS call is made on a status-page poll. With no AWS account the single `Cloud accounts` row remains.


---

## 27. Timeout summary

| Endpoint class | API budget | Upstream timeout | On exceed |
|---|---|---|---|
| Dashboard, inventory, history (reads) | < 2 s p95 (design budget) | DB 5 s | `503` / section `unavailable` |
| Metric instant / range | < 2 s / < 5 s | Prometheus 10 s | Section `unavailable`, `504` |
| Log query | < 5 s | Loki 15 s | `504 provider_timeout` |
| Connectivity / credential verify | < 15 s | Provider 10–15 s | `504` |
| Operation / deployment **creation** | < 1 s | — | Work is asynchronous |
| Operation execution | n/a | Action policy, default 300 s | `TIMED_OUT` |
| Deployment execution | n/a | Target policy, default 3 600 s | `FAILED` with `failure_reason = DEPLOYMENT_TIMED_OUT` |
| Health check (single) | n/a | 10 s, 3 attempts | Check fails |

Performance figures are PRD §10 design budgets, explicitly **not** measured results.

---

## 28. Audit `action_type` catalogue (initial, extensible)

`LOGIN_SUCCEEDED`, `LOGIN_FAILED`, `LOGIN_DENIED`, `LOGOUT`, `SESSION_TERMINATED`, `AUTHZ_DENIED`, `SERVER_REGISTERED`, `SERVER_UPDATED`, `SERVER_ENVIRONMENT_CHANGED`, `SERVER_RETIRED`, `SERVER_STATUS_CHANGED`, `SERVER_CONNECTIVITY_CHECKED`, `SERVICE_REGISTERED`, `SERVICE_UPDATED`, `SERVICE_ACTION_POLICY_CHANGED`, `SERVICE_DEREGISTERED`, `APPLICATION_REGISTERED`, `APPLICATION_UPDATED`, `APPLICATION_ARCHIVED`, `SERVICE_OPERATION_REQUESTED`, `SERVICE_OPERATION_DENIED`, `SERVICE_OPERATION_APPROVED`, `SERVICE_OPERATION_REJECTED`, `SERVICE_OPERATION_CANCELLED`, `SERVICE_OPERATION_STARTED`, `SERVICE_OPERATION_COMPLETED`, `DEPLOYMENT_REQUESTED`, `DEPLOYMENT_DENIED`, `DEPLOYMENT_APPROVED`, `DEPLOYMENT_REJECTED`, `DEPLOYMENT_CANCELLED`, `DEPLOYMENT_STARTED`, `DEPLOYMENT_COMPLETED`, `DEPLOYMENT_ROLLBACK_REQUESTED`, `ALERT_ACKNOWLEDGED`, `ALERT_ASSIGNED`, `ALERT_INGESTED`, `ROLE_GRANTED`, `ROLE_REVOKED`, `USER_CREATED`, `USER_DISABLED`, `USER_ENABLED`, `CLOUD_ACCOUNT_REGISTERED`, `CLOUD_ACCOUNT_UPDATED`, `CLOUD_ACCOUNT_DISABLED`, `CLOUD_ACCOUNT_VERIFIED`, `CLOUD_ACCOUNT_SYNC_REQUESTED`, `CLOUD_ACCOUNT_SYNCED` (M4.3), `SERVICE_OPERATION_RESUMED` (M4.3), `DEPLOYMENT_TARGET_REGISTERED`, `DEPLOYMENT_TARGET_UPDATED`, `APPROVAL_POLICY_CHANGED`, `NOTIFICATION_CHANNEL_CHANGED`, `NOTIFICATION_RETRY_REQUESTED`, `AUDIT_QUERIED`, `AUDIT_EXPORTED`, `READ_ONLY_MODE_CHANGED`. **M4.6 produces `AUDIT_QUERIED` (a read of the trail, §24.1 / §24.2) and `AUDIT_EXPORTED` (the one-time download, §24.5), and adds `AUDIT_EXPORT_REQUESTED` (§24.3), `AUDIT_EXPORT_FAILED` and `AUDIT_EXPORT_EXPIRED` (system actors, on behalf of the requester; `OPEN-112`).** M4.5 produces `SERVICE_OPERATION_APPROVED`, `SERVICE_OPERATION_REJECTED` and `APPROVAL_POLICY_CHANGED` (all three were already listed) and uses `SERVICE_OPERATION_DENIED` and `SERVICE_OPERATION_CANCELLED` for the approval paths; `approval_id` is set on every row that concerns an approval.

---

## 29. Endpoints deliberately **not** provided

| Not provided | Why |
|---|---|
| `POST /execute`, `/run-command`, `/shell`, or any command/script field | Mandatory rules 1 and 2 — no such surface may exist |
| Raw PromQL or LogQL passthrough | AD-04 / AD-05: injection and scope-bypass |
| Arbitrary URL fetch, proxy, or webhook test against a user-supplied URL | [SECURITY.md §19](SECURITY.md#19-ssrf) |
| Audit write/update/delete | AD-11 |
| Secret read-back of any kind | [SECURITY.md §9](SECURITY.md#9-secrets-management) |
| Jenkins job creation or free-form build parameters | PRD §4.10, §14 |
| Password set/reset/change | No password exists |
| Bulk operations across many servers in one request | Deferred: blast radius and partial-failure semantics need design first |
| File upload or download of arbitrary paths | PRD §4.7 "prevent unrestricted filesystem access" |

---

## 30. Open decisions

| ID | Question | Working default |
|---|---|---|
| ~~OPEN-02~~ | Are applications per-environment? Changes 18.x response shapes | **RESOLVED 2026-10-14: environment-agnostic** (AD-12 confirmed). The §18 shapes below stand as written |
| OPEN-09 | Windows service control endpoints | Not provided; `UNSUPPORTED` reported |
| OPEN-18 | Multiple deployment targets per application+environment | One per pair |
| OPEN-19 | Live progress via WebSocket/SSE instead of polling | Polling with `poll_after_seconds` |
| ~~OPEN-20~~ | Should `GET /logs/query` exist as a shareable-link variant? | **RESOLVED 2026-10-17 (before M3.1): no — `POST` only**, to keep filters out of access logs and history. A shareable link, if ever wanted, would carry a server-stored saved-query id, never the filter |
| OPEN-21 | Bulk service operations | Out of scope for v1 |
| **OPEN-27** | **How is a resource with no scope dimensions scoped?** Raised in M1.5. An application has no environment, no cloud account and no server (AD-12), so there is nothing for [SECURITY.md §4](SECURITY.md#4-permissions-scope-and-resourceenvironment-restrictions)'s dimension rules to evaluate against it | **Applications are not scope-filtered**: `server:read` is the whole check for §18.1 and §18.3. An application row is metadata (name, slug, owner, repository reference, health-check config) and every privileged action targets a *server* or a deployment target, each scope-filtered on its own dimensions; writes need `admin:integrations`, which is Administrator-only. *Alternatives, both needing a product decision:* filter by the `application` dimension alone, ignoring the other three; or treat an application as reachable when the grant reaches any server hosting it |
| ~~OPEN-28~~ | **Where do configurable warning/critical thresholds live?** Raised in M2.2. PRD §4.4 and §4.8 say "configurable"; the schema has no column or setting for it, and M2.1 left a comment promising "per server with M2.2" without defining it | **Catalogue defaults only** (`default_thresholds` on `cpu_utilisation`, `memory_utilisation`, `disk_utilisation`; `null` for the other seven). No migration. *Alternatives, needing a product decision:* per-environment settings (fits AD-14's per-environment staleness thresholds), or a per-server table. Decide before M3.3, whose alert rules need the same numbers and should not hold a second copy |
| **OPEN-28 — resolution** | Where do thresholds live? | **RESOLVED 2026-10-17 (before M3.1): per-environment thresholds, one source.** A single validated setting `DCP_UTILISATION_THRESHOLDS`, shaped and validated like `DCP_HEALTH_THRESHOLDS` (a `default` plus the four `servers.environment` values, `extra="forbid"`, `warning < critical`, checked at startup; the catalogue defaults — CPU 80/90, memory 85/95, disk 85/95 — are the shipped `default`). **The M3.3 Prometheus rule file for these conditions is generated from that setting by a committed script, and `check-all.sh` fails when the committed file is stale**, so alert rules stay file-managed in git (`OPEN-03`) and there is still exactly one copy of the numbers. No per-server table. **Not decided here, and left to M3.3's plan:** whether and how utilisation moves the `warning`/`critical` *health* state (today `warning` is scrape lateness and `critical` is never produced) — the two must not be given one meaning by accident. **Closed 2026-10-18 as `OPEN-39` ([ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions)): it does not; health stays "collected on time" and utilisation escalates through alerts.** Note also that the setting this row decided (`DCP_UTILISATION_THRESHOLDS`) was **not built by M3.1/M3.2**; M3.3 commit 1 builds it (`OPEN-46`) |

---

## 31. Assumptions

1. The SPA and API share an origin, so cookie auth needs no cross-origin exceptions.
2. Operation and deployment progress is polled; no socket infrastructure is required for v1.
3. Clients tolerate `data_state` on every data-bearing payload and render gaps, staleness and unavailability distinctly — a client that coerces `null` to `0` violates PRD §4.2 and is a defect.
4. All list endpoints are scope-filtered, so a client never needs to filter for authorisation. **Amended 2026-10-14 (M1.5):** `GET /servers` is; `GET /applications` is not, because an application carries no scope dimension — see `OPEN-27` in [§30](#30-open-decisions). The assumption's *purpose* holds either way: a client still never filters for authorisation.
5. The OpenAPI schema generated by the implementation is the authoritative machine-readable form of this document; where they diverge, the schema is fixed to match this contract, or this contract is amended by review.

**Implementation status (2026-10-14).** §11 (health), §12 (authentication), §25
(user and role administration), §14.1–§14.7 (server inventory) and §18.1–§18.5
(applications) are implemented. Everything else in this document is
unimplemented, and every example payload in those sections is placeholder
data.
