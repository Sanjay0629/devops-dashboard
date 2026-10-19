# Technology Stack — DevOps Control Plane

**Status:** Draft for review. No packages have been installed and no versions have been verified against each other.
**Source of truth:** [`../PRD.md`](../PRD.md) §5. **Architecture context:** [ARCHITECTURE.md](ARCHITECTURE.md). **Security obligations:** [SECURITY.md](SECURITY.md).

> **Version policy for this document (AD-16).** Deliberately, no exact package versions are written here. Pinning a version in prose that was never resolved by a dependency solver creates a false record. Versions are recorded **only** in dependency files (`pyproject.toml`/`uv.lock`, `package.json`/`package-lock.json`, `Dockerfile` digests, Compose image tags) at implementation time, by a resolver, on the day the work is done. See [§9](#9-version-pinning-strategy).

---

## 1. Stack at a glance

| Layer | Technology | Responsibility |
|---|---|---|
| UI | React + TypeScript + Vite | Operator interface, routing, state |
| UI styling | Tailwind CSS + a headless component library | Consistent design system, dark/light themes, accessible primitives |
| UI charts | A React-friendly time-series charting library | Metric cards and historical charts |
| API | Python + FastAPI | HTTP surface, authn/authz enforcement, orchestration |
| Validation | Pydantic | Request/response schemas, config parsing, provider result envelopes |
| ORM | SQLAlchemy | Data access, parameterised SQL, transaction boundaries |
| Migrations | Alembic | Version-controlled schema evolution |
| Metadata store | PostgreSQL | Inventory, identity, operations, deployments, approvals, audit |
| Broker / cache | Redis | Celery broker + result backend, sessions, rate limits, short caches |
| Async execution | Celery (+ beat) | Operations, deployments, polling, notifications, scheduled sync |
| Metrics | Prometheus | Time-series store and alert evaluation |
| Host metrics | Node Exporter, windows_exporter | Linux and Windows host metrics |
| Unit metrics | systemd exporter / restricted agent | systemd unit state |
| Log shipping | Grafana Alloy | Collect, relabel, redact, push logs |
| Log store | Grafana Loki | Log storage and LogQL querying |
| Alert routing | Alertmanager (+ Grafana for deep dives) | Dedupe, group, route, notify |
| Packaging | Docker + Docker Compose | Local development and initial production |
| Cloud control | AWS Systems Manager, Azure Resource Manager / Run Command / Arc | Inventory, connectivity, allowlisted operations |
| Identity | OIDC (external provider) | Authentication only |
| CI/CD integration | Jenkins REST API | Deployment orchestration |
| Secrets | AWS Secrets Manager / Azure Key Vault / approved store | Runtime secret retrieval |

---

## 2. Frontend

### 2.1 React

**Responsibility:** component model and rendering for dashboards, inventory tables, charts, log views, and operation/approval dialogs.
**Why:** mandated by PRD §5.1; the largest ecosystem for the data-dense table/chart UI this product is, and the broadest pool of maintainers for an internal tool.
**Boundaries:** no credentials, no provider branching, no authorisation logic relied upon for security ([SECURITY.md §6](SECURITY.md#6-never-rely-on-frontend-authorization)).

### 2.2 TypeScript

**Responsibility:** compile-time types across components, API client and domain models.
**Why:** PRD §10 requires typed frontend code. The operation/deployment/service state enums are exactly the kind of thing that silently drifts between frontend and backend; types plus generated API models catch it at build time.
**Rule:** `strict: true`. API request/response types are **generated from the backend's OpenAPI schema**, never hand-written — this is the mechanism that keeps the contract synchronised ([§11](#11-contract-synchronisation)).

### 2.3 Vite

**Responsibility:** dev server with HMR, production bundling.
**Why:** PRD §5.1. Fast cold start matters because the Compose stack is restarted often.
**Boundary:** only `VITE_`-prefixed variables reach the bundle; anything in the bundle is public. API base URL is public; nothing else from config may be exposed.

### 2.4 Tailwind CSS + component library

**Responsibility:** Tailwind supplies the utility layer and the light/dark token set; a **headless, accessible** component library (Radix-UI-style primitives, or an equivalent) supplies dialogs, menus, tabs, tooltips, combo boxes and tables.
**Why:** PRD §8 demands information density, dark/light themes, and that colour is never the only status indicator. Headless primitives give correct keyboard and screen-reader behaviour for the confirmation dialogs that gate privileged operations — the place where an accessibility failure has operational consequences.
**Design-system rules:**
- Status is encoded as **icon + text + colour**, never colour alone (PRD §8). One `StatusBadge` component owns the mapping for `healthy | warning | critical | stale | unknown | unreachable` and is the only place those visuals are defined.
- Spacing, colour, radius and typography come from Tailwind theme tokens; no ad-hoc hex values in components.
- Both themes are verified for WCAG AA contrast on status colours.

### 2.5 Charting library

**Responsibility:** time-series line/area charts, sparklines, and comparison charts.
**Selection criteria** (decide during Phase 2, record the choice here):
1. Renders a 24 h range at a reasonable step without jank on a dashboard with several charts.
2. First-class gap handling — **a gap must render as a gap, not as zero or an interpolated line** (PRD §4.2, §4.4). This is a functional requirement, not a preference, and disqualifies libraries that coerce `null` to `0`.
3. Accessible tooltips and keyboard focus; a data-table fallback for screen readers.
4. React bindings, TypeScript types, dark/light theming via CSS variables.
5. Bundle size proportionate to use.

**Decision: `d3-shape` + `d3-scale`, used directly with no React wrapper. Selected 2026-10-16 (`TS-01` resolved).** Integrated as `frontend/src/components/chart/linePath.ts` — two functions, not a chart component. M2.2 built the components on top: `LineChart.tsx` (axes, tick selection in `ticks.ts`, a keyboard-readable crosshair, a lazily rendered table view, threshold lines with text labels) and the eight categorical `--series-*` tokens in `index.css`. No dependency was added by M2.2.

*Need (§10 rung 1).* Real now: M2.2 ships metric cards, range charts with selectable ranges, and server/environment comparison (IMPLEMENTATION_PLAN M2.2).

*The rung that failed: 3 — "is it a few lines?" — and only half of it.* Measured rather than argued. A zero-dependency hand-rolled line generator (a linear interpolation and a loop emitting `M`/`L`, breaking at each `null`) builds to **348 B gzipped** and is genuinely a few lines; that part was written and measured. What it does **not** do is choose tick positions over an arbitrary time domain, and "range charts with selectable ranges" is exactly the requirement that needs it — picking readable intervals across a 15-minute window and a 30-day one, which `Intl` cannot do (it *formats* a tick, it does not *choose* one). So the few lines were written for the geometry and the dependency was taken for the scales.

*Why nothing wraps it.* `@visx/shape`'s `LinePath` and `@visx/scale`'s `scaleLinear` are React/object-API wrappers over precisely these two packages. The component `LinePath` contributes is one `<path>` element, which is three lines to write, and the wrapper measured **18,480 B gzipped against 14,926 B** for the same capability — 3.5 kB and two more packages for an element we can emit ourselves. §10 rung 7 also prefers it: d3 micro-packages bring no component system at all.

**The gap criterion (criterion 2 above) was tested, not assumed**, which is what this decision was blocked on. `frontend/src/components/chart/linePath.test.ts` is that test and is kept as a standing regression test. It asserts on the *geometry of the rendered path* rather than on a snapshot, so it would judge a different library unchanged, and it catches **two** defects where a naive test catches one:

| Defect | Signature | Operator reads |
|---|---|---|
| Coercion (`null` → `0`) | line stays continuous, **gains** a vertex on the baseline | a silent host as an idle one |
| Bridging (`connectNulls`, `spanGaps`) | line stays continuous, **loses** a vertex | invented data as measured data |

So the gate checks subpath count *and* vertex count, and `describe('the gate itself')` proves it can fail — the same reason [`TESTING.md §8`](TESTING.md#8-security-tests)'s planted-bypass test exists. Stronger than that: removing `.defined()` from the product code was run as a mutation, and **6 of the 12 tests failed** with `the gap was coerced to a value (PRD §4.2)` and the real coerced path `M0,100L100,100L200,200L300,100L400,100`. The gap rule therefore lives in one function of ours rather than in a library default — which matters, because Recharts' correctness here rests on `connectNulls` continuing to default to `false`.

*Evaluation (§10 rung 4).* **ISC licensed**, all ten packages — verified by reading each `package.json`, not assumed from the top two. Transitive tree **measured** (`d3-shape`, `d3-scale` plus `d3-array`, `d3-color`, `d3-format`, `d3-interpolate`, `d3-path`, `d3-time`, `d3-time-format`, `internmap`): **ten runtime packages**, every one a single-purpose d3 micro-package from the same maintainer with a decade of release history and no CVE history, plus four dev-only `@types` packages (`@types/d3-shape`, `@types/d3-scale` and their transitive `@types/d3-path`, `@types/d3-time`). `npm audit --audit-level=high` reports 0 vulnerabilities. **Bundle impact today is zero** — the production build is byte-identical (`index-CeSHwmyG.js`, 313,223 B raw / 94,583 B gzipped, same content hash) because nothing imports `linePath` yet; the cost arrives with M2.2 and is **+14,926 B gzipped**, measured by building the import in isolation with React externalised. Against the precedent this document already set, `react-router-dom`'s **+18 kB gzipped** recorded as "modest", that is below the bar. Easy to remove: two functions in one file.

*Alternatives considered, each measured rather than ranked by popularity.*

- **Recharts 3.10.1 — rejected on size and on accessibility defaults.** It passes the gap gate out of the box (`connectNulls` defaults to false; the probe rendered `M5,36.67L102.5,36.67M297.5,36.67L395,36.67`, two runs and four vertices) and it would have supplied axes, tooltips and a responsive container. But a line chart with axes and a tooltip measured **129,306 B gzipped** — it would take the app's bundle from 94.6 kB to roughly 224 kB, **more than doubling it**, and more than seven times the increase this document calls modest. On criterion 3 it is actively unhelpful: the probe showed it sets `role="application"` on the `<svg>` and makes **13 elements tabbable** for a five-point chart. `role="application"` tells a screen reader to stop interpreting the content and forward keystrokes, which for a chart with no keyboard model of its own is worse than no role at all — so both defaults would have to be overridden, and the data-table fallback criterion 3 asks for would still have to be written by hand.
- **uPlot 1.6.32 — rejected because the criterion cannot be tested against it.** It is the smallest and fastest of the candidates and is the right tool for tens of thousands of points, which is not this dashboard (§15.2 caps a series at 11,000 and comparisons at 20 servers). Two findings ended it: it renders to **`<canvas>`**, so there is no path geometry to assert and criterion 2 could only be checked by eye; and it could not even be **imported** in this project's test environment — `TypeError: matchMedia is not a function`, thrown at module scope under jsdom. A charting choice whose one functional requirement is untestable fails the rule this section sets for itself.
- **visx 4.0.0 — rejected as a wrapper we do not need**, above. It passed the gate with `defined` (`M0,100L100,100M300,100L400,100`) and failed it without (`continuous line with 5 vertices for 4 real points`), which is the same result our own 40 lines get for 3.5 kB less.
- **Hand-rolled, zero dependencies — adopted for the geometry, rejected for the scales**, above. 348 B gzipped, and no tick selection.

*Criterion 4's "React bindings" is not met, and does not need to be.* `linePath` returns a string; the React binding is `<path d={linePath(samples, scales)} />`, which is the JSX element itself. The same criterion's **theming is better served this way**: a bare `<path>` takes `stroke="var(--chart-line)"` and inherits the page's dark/light tokens with nothing to configure, where a charting library's theming API generally has to be talked into CSS variables and often resolves colours in JavaScript at render time — which breaks under a `prefers-color-scheme` change without a re-render.

*The one place this choice is weaker than the alternatives: criterion 4's TypeScript types.* Recharts and visx ship their own; the d3 micro-packages do not, so the types come from DefinitelyTyped and can in principle lag a release. Accepted deliberately — the surface actually used is three functions (`line`, `scaleUtc`, `scaleLinear`) whose signatures have not changed in years, it is confined to one file, and `tsc --noEmit` runs under `strict` with `exactOptionalPropertyTypes` and `noUncheckedIndexedAccess` in `scripts/check-all.sh` and in CI, so a types drift fails the build rather than going unnoticed.

*Version.* Pinned exactly: `d3-shape@3.2.0`, `d3-scale@4.0.2`, and `@types/d3-shape@3.1.7` / `@types/d3-scale@4.0.9` as dev dependencies (neither package ships its own types). Exact pins for the same reason as `react-router-dom`: `npm audit --audit-level=high` is a gate in `scripts/check-all.sh` and in CI, and an upgrade should be checked against it rather than against a changelog.

*What is deliberately still unbuilt.* Axes, ticks, tooltips, responsive sizing, keyboard focus and criterion 3's data-table fallback. Those are M2.2's, and this decision deliberately does not pre-empt them — it settles the one question M2.2 could not safely answer under its own deadline. Criterion 1 (24 h range without jank on a dashboard of several charts) is **not yet verified**: there is no chart on a page to measure, and it will be measured in M2.2 against real series rather than asserted here.

### 2.6 Supporting frontend libraries (keep the list short)

| Need | Approach |
|---|---|
| Server-state fetching, caching, polling | A query library (TanStack Query or equivalent) — polling operations/deployments is pervasive; hand-rolled effects would duplicate it badly |
| Routing | **React Router** — selected in M1.5, see below |
| Forms + validation | A schema-validated form library whose schema can be derived from generated API types |
| Dates and ranges | The platform `Intl` API plus a small date utility; timestamps are rendered in the operator's locale with an explicit UTC tooltip |
| State beyond server state | React context/`useState` first. A global store only if a measured need appears |

**Routing: `react-router-dom`, selected 2026-10-14 (M1.5).** The first new
frontend dependency since M1.1.

*Need (§10 rung 1).* Real now, not speculative: M1.5 ships five pages
(`ServerList`, `ServerDetail`, `ServerForm`, `ApplicationList`,
`ApplicationDetail`) with list→detail drill-through, and each has to be
deep-linkable — an operator sharing a link to a server is the ordinary case,
not a nicety.

*Rung 2 (stdlib / already installed).* Nothing installed does this. React
itself has no router, and the project has no other navigation library.

*Rung 4 (native platform feature).* The History API genuinely covers one or
two pages, which is why M1.1–M1.4 needed nothing. It stops being enough at
five routes with URL parameters, a nested layout and a redirect: what starts
as `switch (pathname)` ends as a path matcher, a parameter parser and a link
component — a router, written here, with its own bugs.

*Rung 3 (is it a few lines?).* No. See above.

*Evaluation (§10 rung 4).* MIT licensed; the de facto React router with a long
release history and many maintainers. Transitive tree, measured rather than
assumed (`npm ls`): `react-router-dom` → `react-router` → `cookie` and
`set-cookie-parser` — **three packages behind the one we name**, both leaves
small and widely used, and both reachable only through the framework-router
paths this SPA does not use. Bundle impact is modest (the production build
grew from 234 kB to 294 kB raw, 73 kB to 91 kB gzipped). Easy to remove: route
definitions are confined to `App.tsx` and the pages use two hooks.

*Version.* Pinned exactly at `7.18.4`, and the exactness matters here. Every
release up to and including 7.17.0 carries open-redirect, XSS and SSR
advisories, and `npm audit --audit-level=high` — which is a gate in
`scripts/check-all.sh` and in CI — fails on them. It reports 0 vulnerabilities
at 7.18.4. Any upgrade or downgrade should be checked against that gate rather
than against the changelog alone.

*§10 rung 7 (frontend specifically).* It brings no component system, no CSS
paradigm and no state-management philosophy — the three things that rung
forbids.

*Rejected:* a hand-rolled router, which grows into a router anyway as M1.6–M3
add pages; and deferring the pages to M1.6, which leaves M1.5's definition of
done unmet and makes M1.6 large.

---

## 3. Backend

### 3.1 Python + FastAPI

**Responsibility:** HTTP routing, request validation, session and authorisation enforcement, orchestration of adapters and tasks, OpenAPI schema generation.
**Why:** PRD §5.2. Python is the lingua franca of the cloud SDKs this product depends on (`boto3`, Azure SDK, Jenkins clients) and shares one codebase with the Celery workers. FastAPI's dependency-injection model is what makes `require_permission(...)` a declarative, auditable, per-route construct rather than a convention people forget.
**Boundaries:** every route carries an explicit permission dependency; the OpenAPI schema is the contract source for the frontend; no business logic in route handlers (they call module service functions).

### 3.2 Pydantic

**Responsibility:** request/response models, settings parsing from environment, provider result envelopes, Alertmanager webhook payload validation.
**Why:** PRD §5.2 and §9 input validation. Validation at the trust boundary is non-negotiable; strict typed models reject malformed or over-broad input before any handler logic runs.
**Rules:** `extra="forbid"` on all request models — unknown fields are a client/contract bug and must not be silently dropped. Enums for every state field. Field-level constraints (lengths, patterns, ranges) on everything that reaches a query or a provider call. Secret-bearing fields use a secret type whose `repr` cannot leak into logs or tracebacks.

### 3.3 SQLAlchemy

**Responsibility:** mapped models, queries, transaction and session management.
**Why:** PRD §5.2; parameterised SQL by construction satisfies the SQL-injection requirement (PRD §9) without per-query vigilance.
**Rules:** no string-concatenated SQL anywhere; raw SQL only via bound parameters and only where justified (aggregates, partition maintenance). One session per request, committed or rolled back by middleware. `statement_timeout` set per session. Transaction boundaries are documented in [DATABASE.md §12](DATABASE.md#12-transaction-boundaries).

### 3.4 Alembic

**Responsibility:** forward-only, reviewable schema migrations.
**Why:** PRD §10 requires version-controlled migrations; PRD §12 requires they be tested.
**Rules:** every schema change is a migration — no manual DDL in any environment. Autogenerated revisions are always reviewed and edited by hand. Each revision declares a downgrade or an explicit comment saying why it is irreversible. Large-table changes avoid full rewrites and long locks. Migrations run as a dedicated step before the app starts, never from application startup code ([TESTING.md §4](TESTING.md#4-database-and-migration-tests)).

### 3.5 Server, HTTP client, and runtime

| Need | Technology | Notes |
|---|---|---|
| ASGI server | Uvicorn (Gunicorn supervisor in production) | `--reload` locally only |
| Outbound HTTP | `httpx` | Timeouts mandatory on every call; shared clients per integration with connection limits |
| AWS SDK | `boto3` | Role-based credentials only; adaptive retry mode |
| Azure SDK | `azure-identity` + `azure-mgmt-*` | Managed identity preferred |
| Jenkins | REST via `httpx` | Prefer plain REST over a wrapper library to keep the request surface explicit and auditable |
| Structured logging | `structlog` (or stdlib `logging` with a JSON formatter) | `correlation_id` bound per request/task; redaction processor in the pipeline |
| Rate limiting | Redis counters (library or ~30 lines of Lua) | A new dependency only if a library demonstrably beats the direct implementation |

---

## 4. Data and background processing

### 4.1 PostgreSQL

**Responsibility:** the durable record for everything DCP owns (see [DATABASE.md](DATABASE.md)).
**Why:** PRD §5.4. Relational integrity is the point — foreign keys, unique constraints and partial indexes are what make "no duplicate server registration" and "no duplicate operation" enforceable at the storage layer rather than hopefully in code. JSONB covers sanitised result blobs without abandoning normalisation.
**Boundaries:** no raw metric samples, no log lines (PRD §6). Audit tables are append-only via grants (AD-11).
**Version:** a currently supported major release; partitioning and JSONB features used here are long-established. Exact version recorded in the Compose file and the production runbook.

### 4.2 Redis

**Responsibility:** Celery broker and result backend, session store, rate-limit counters, short-TTL dashboard and provider-metadata caches.
**Why:** PRD §5.3.
**Boundary (PRD §5.4):** Redis must never be the only durable record of a privileged operation. Operation and deployment rows are committed to PostgreSQL before enqueue; losing Redis loses queued work and sessions (recoverable: re-login, reconcile rows) but never operational history.
**Config:** `appendonly` persistence, `maxmemory-policy noeviction` on the broker database so queued tasks are never evicted, separate logical databases (or instances) for broker vs cache so a cache flush cannot drop queued work, and a password plus private networking.

### 4.3 Celery + beat

**Responsibility:** asynchronous execution of operations, deployment polling, health checks, notifications and scheduled synchronisation.
**Why:** PRD §5.3. Mature, Python-native, with the `acks_late` + visibility-timeout semantics required for the idempotency model (AD-08).
**Rules:** `task_acks_late=True` with `visibility_timeout` greater than the longest operation deadline; `worker_prefetch_multiplier=1` for long tasks; queues separated by blast radius ([ARCHITECTURE.md §2.3](ARCHITECTURE.md#23-background-workers--celery)); every task has a soft and hard time limit; tasks take **ids, never payloads of domain objects**; results in Redis with a TTL, with PostgreSQL as the real record. Exactly one `beat` instance.
**Trade-off:** Celery's configuration surface is large and easy to get wrong. The alternatives (RQ, Arq, Dramatiq) are simpler but weaker on scheduling and routing; the PRD names Celery, so the mitigation is a single reviewed config module rather than a different library.

---

## 5. Monitoring and logging

### 5.1 Prometheus

**Responsibility:** scrape exporters, store time series, evaluate alert rules, serve queries to DCP.
**Why:** PRD §4.4. The exporter ecosystem already covers every metric the PRD lists.
**Boundary:** DCP is a **read-only client** and renders PromQL from allowlisted templates (AD-04). DCP also writes a target-discovery file from inventory (`OPEN-14`, resolved in M2.3): a `file_sd` JSON file on a volume Prometheus mounts read-only. Prometheus watches it, so nothing calls a reload and the lifecycle API is not enabled.

### 5.2 Node Exporter / windows_exporter

**Responsibility:** host-level CPU, memory, disk, disk I/O, network, load and uptime for Linux and Windows respectively.
**Why:** PRD §4.4.
**Boundary:** exporter ports are restricted to the Prometheus source only ([SECURITY.md §7](SECURITY.md#7-network-isolation)). Metric names differ between the two exporters, so the PromQL templates are **per-OS-family** — a point where assuming parity would produce wrong numbers rather than an error.

### 5.3 systemd metrics integration

**Responsibility:** per-unit `load_state`, `active_state`, `sub_state`, restart counts, state-change timestamps.
**Options:** the Prometheus `systemd_exporter`, `node_exporter`'s systemd collector with a unit allowlist, or the restricted DCP agent exposing the same series.
**Why:** PRD §4.5 requires unit state without SSH.
**Rules:** configure a **unit allowlist** on the exporter — scraping every unit on every host is a cardinality problem, and the portal only ever displays registered services. Linux only; Windows hosts report `UNSUPPORTED` for unit state (OPEN-09).
**Decision: `node_exporter`'s systemd collector with a unit allowlist. Selected 2026-10-17 (`TS-02` resolved).** The normalisation table in [ARCHITECTURE.md §6.1](ARCHITECTURE.md#61-monitoring-read) remains the contract, amended by `OPEN-29` (below).

*Need (§10 rung 1).* Real now: M2.4 is P0 (PRD §4.5) and a failed service must be identifiable without logging in.

*Rung 5 — an already-deployed component covers it.* `node-exporter` is already in Compose, already scraped, and already a registered target kind (M2.3). Enabling its systemd collector adds **no component, no port, no scrape job and no `file_sd` kind**, and `node_scrape_collector_success{collector="systemd"}` is a purpose-made signal for "collector failed" that a separate exporter would have to approximate.

*Why not `systemd_exporter`.* Tested, not assumed: its `systemd_unit_state` has the same `name`/`type`/`state` shape and exposes no more of what the PRD asks for. It adds `service_type`/`mount_type` (not wanted), a second container, a second job and a second discovery kind, and all 19 of its metrics are documented as UNSTABLE; the latest release is 0.7.0 (2025-03-14). *Why not the agent:* it is the only source that could supply the attributes below, but it is a new component with its own security surface (`OPEN-05`) and out of proportion to M2.4.

**What the collector was tested to do** (pinned `prom/node-exporter:v1.8.2` against a real systemd 255 on WSL2, non-root, system bus socket mounted read-only; a throwaway unit that exits non-zero, a `RemainAfterExit` unit, and `cron.service`):

| Observed | Consequence |
|---|---|
| `--collector.systemd.unit-include` limits the series to the allowlist; five `node_systemd_unit_state` series per unit, exactly one equal to `1` | Cardinality is `5 × registered services`. The exporter-side allowlist is **host configuration** (DCP has no write path to a host's `node_exporter` flags and must not gain one); DCP's own allowlist is applied **on read** — queries name only registered units, validated against the service-name pattern. Never a wildcard on either side |
| A failed unit reports `state="failed"` = 1 | `FAILED` is distinguishable |
| A stopped but still-loaded unit reports `state="inactive"` = 1 | `INACTIVE` is distinguishable from `FAILED` |
| An allowlisted unit that does not exist produces **no series at all**, while `node_scrape_collector_success{collector="systemd"}` stays `1` | "Absent from a *successful* collection" — the unit is not loaded on this host. It must not be rendered `INACTIVE` |
| With no system bus, the exporter stays up (`/metrics` = 200), `node_scrape_collector_success{collector="systemd"}` = `0`, and **no systemd series** are emitted | This is the `UNCOLLECTED` case: host up, collector failed |
| With the collector not enabled (the default), no `systemd` series and no `collector="systemd"` series exist | `UNCOLLECTED` as well, by absence — the same state, so the two causes must not be told apart in the UI |
| `node_systemd_unit_start_time_seconds` is `0` for a unit that is not running | **`0` means unknown, not 1970.** Rendering it as a date is the exact zero-for-unknown error CLAUDE.md §5 forbids |
| `active` is reported identically for `sub_state=running` and `sub_state=exited` | The §6.1 table's `ACTIVE` row cannot be tested for `running`. See `OPEN-29` |

**Not available from this source, or from `systemd_exporter`:** `sub_state`, `load_state` (only used as a "loaded" filter), the unit-file enabled state, and the main PID. They are `null` in M2.4 (`OPEN-29`). `node_systemd_service_restart_total` and `node_systemd_unit_start_time_seconds` need `--collector.systemd.enable-restarts-metrics` and `--collector.systemd.enable-start-time-metrics`. There is **no last-state-change timestamp**: the start time is the nearest series and describes activation only, so "last state change" is the collection timestamp until an agent supplies more.

**Operational constraint.** The collector needs the system bus. A host install of `node_exporter` has it; a *containerised* one must have `/run/dbus/system_bus_socket` mounted, which is a posture change on par with the host-root mount the development exporter deliberately avoids (PROGRESS.md §8). M2.4's integration fixture is therefore a container that runs systemd itself, not the existing `node-exporter` service.

### 5.4 Grafana Alloy

**Responsibility:** collect journald, Nginx, PHP-FPM, application and Docker logs; relabel to the bounded label set; apply best-effort redaction; push to Loki.
**Why:** PRD §4.7.
**Rules:** the label allowlist in [ARCHITECTURE.md §5](ARCHITECTURE.md#5-centralized-logging-architecture) is enforced in the Alloy config (`stage.label_keep`, last, after every guard); outbound push only, no inbound port; config is version-controlled and parameterised per host by environment variables (`ALLOY_*`, listed in the file's header) — rendering them from inventory is later work. `alloy fmt` is the formatter (tabs); `config.alloy` and `journal.alloy` are `fmt`-clean. **Alloy 1.5.1 facts learned building this:** `stage.replace` replaces only its capture group; a `loki.process` receives labels but not the extracted map, so normalisation lives in one shared `common` stage rather than per source; a source whose path does not exist is an unhealthy component and fails `/-/ready`, which is why the journal source is its own file; the image has bash but no `wget`/`curl`, so the healthcheck uses `/dev/tcp`.

### 5.5 Grafana Loki

**Responsibility:** log storage and LogQL query.
**Why:** PRD §4.7. Label-indexed storage is cheap at this scale, and it pairs with Alloy and Prometheus without a second query language family.
**Rules:** per-query limits, max range, and retention set in Loki config; DCP builds every query server-side (AD-05). Retention duration unresolved (OPEN-08).

### 5.6 Alerting integration

**Responsibility:** Alertmanager dedupes/groups/silences and posts to DCP's webhook, which materialises `alert_events`; DCP owns acknowledgement, assignment and history; DCP's `notifications` queue delivers to email/Teams/Slack/webhook.
**Why:** PRD §4.8.
**Resolved:** rules are file-managed (OPEN-03); channel configuration is DB tables (OPEN-13); delivery is DCP's own, driven by a transactional outbox (OPEN-50). Email is stdlib `smtplib`, HTTPS is the already-installed `httpx` -- no new dependency (§10 rung 2).
**Rule:** notification delivery failures must be observable and retryable (PRD §4.8) — delivery attempts are recorded with status, and retries are bounded with backoff.

### 5.7 Grafana

**Responsibility:** optional operator-facing deep-dive dashboards over Prometheus and Loki; included in the Compose stack.
**Boundary:** not a dependency of any DCP page. DCP must never embed a Grafana view in a way that bypasses DCP's authorisation scoping.

---

## 6. Infrastructure and integrations

### 6.1 Docker and Docker Compose

**Responsibility:** reproducible local stack (PRD §11) and the initial production topology.
**Rules:** multi-stage builds; non-root user; `HEALTHCHECK` on every service; base images pinned **by digest**; `.dockerignore` excludes secrets and local env files; resource limits in production; read-only root filesystem where feasible; Compose profiles (`core`, `observability`, `logs`, `mocks`) so the default `up` is light.

### 6.2 AWS Systems Manager

**Responsibility:** instance inventory and connectivity state; execution of a **registered SSM document** with a closed parameter set for allowlisted service actions.
**Why:** PRD §4.11 — no inbound SSH, provider-native auditability, IAM-scoped.
**Rules:** role-based credentials only, no static access keys; IAM conditions restrict the document name and target resource tags; throttling handled with `retry_after`-aware backoff. Any use of a general shell document (e.g. `AWS-RunShellScript`) is prohibited ([SECURITY.md §1](SECURITY.md#1-mandatory-rules)).

### 6.3 Azure management APIs

**Responsibility:** VM inventory, Run Command for registered scripts, Azure Arc for supported external servers, Azure Monitor where required.
**Why:** PRD §4.11.
**Rules:** managed identity preferred; role assignments scoped to a resource group; **Azure Run Command's per-VM serialisation and different async semantics are not equivalent to SSM** and must be modelled in the adapter, not papered over ([ARCHITECTURE.md §7.2](ARCHITECTURE.md#72-capability-matrix--providers-are-not-interchangeable)).

### 6.4 OIDC authentication

**Responsibility:** authentication only. Authorisation is always DCP's own (AD-03).
**Rules:** authorisation code + PKCE, confidential client, discovery document, JWKS verification with caching and rotation, `state` and `nonce` validated, exact redirect-URI registration, clock-skew tolerance. Roles are **not** taken from token claims initially (OPEN-01) because DCP must be able to revoke permissions without an IdP change.

**Implementation (M1.3).** `pyjwt[crypto]` for ID-token verification and `httpx` for discovery, JWKS and the token exchange. Against [§10](#10-rules-for-introducing-a-new-dependency): `httpx` was already an approved choice here ([§3](#3-backend)) and only moved from the dev group to the runtime dependencies. `pyjwt` failed rung 3 — "is it a few lines?" — because the few lines in question are RS256/ECDSA signature verification, JWKS key selection and the `alg`-confusion defence, which is the one place in this codebase not to save lines. Alternatives considered: `authlib` (a much larger surface, and it bundles an OAuth client whose redirect handling we deliberately own), `python-jose` (stale). Cost: `pyjwt` pulls `cryptography`, which is a large but unavoidable transitive dependency for any JWS verification.

### 6.5 Jenkins REST API

**Responsibility:** trigger registered jobs with parameters from registered configuration; poll queue item → build → result; fetch console output for authorised users.
**Rules:** API token from the secrets manager, never in the repository; the job path and parameter template come from `deployment_targets`, never from the request; host allowlist on the Jenkins base URL; CSRF crumb handling where required; console output passed through the redaction pass.

### 6.6 Secrets manager

**Responsibility:** runtime retrieval of OIDC client secret, DB credentials, Jenkins token, notification webhooks, and agent keys.
**As built (M3.4, OPEN-48):** `app/core/secrets.py` -- `resolve("env://NAME")` reads the process variable `DCPSECRET_NAME` (a development / small-deployment backend, **not** a production secret manager; the cloud stores arrive with M4.3 / M4.4 behind the same call). **Rules:** pluggable interface with one configured backend per deployment (OPEN-06); read at process start and on cache miss with a short TTL; never written to disk or logs; rotation supported by re-reading on authentication failure rather than requiring a restart.

---

## 7. Local development vs production configuration

| Concern | Local development | Production |
|---|---|---|
| Config source | `.env` from `.env.example` | Environment + secrets manager; no `.env` file |
| Secrets | Dummy placeholders | Secrets manager only |
| OIDC | Mock provider (`mocks` profile) | Real IdP, exact redirect URIs |
| Cloud adapters | `DemoAdapter`, `demo_mode: true` everywhere | Real adapters with scoped roles |
| Jenkins | Mock server | Real Jenkins, registered targets |
| TLS | Plain HTTP on localhost | HTTPS only, HSTS, secure cookies |
| Cookies | `Secure=false` on localhost (the sole documented difference) | `Secure`, `HttpOnly`, `SameSite=Strict` |
| Reload | `uvicorn --reload`, Vite dev server | Gunicorn + Uvicorn workers, static bundle via proxy |
| Debug/docs | `/docs` enabled, verbose errors (still redacted) | `/docs` disabled or admin-gated; generic client errors, detail in server logs |
| Migrations | Run on demand | Explicit deploy step, backup taken first |
| Log output | Pretty console | JSON to stdout → Alloy → Loki |
| Rate limits | Relaxed | Enforced |
| Data | Seeded synthetic fixtures | Real inventory only |

**Non-negotiable:** authorisation enforcement, input validation, audit writing, secret redaction and allowlist checks behave **identically** in both. No `if DEBUG: skip_auth` path may ever exist — tests assert its absence ([TESTING.md §8](TESTING.md#8-security-tests)).

---

## 8. Quality tooling

### 8.1 Backend

| Purpose | Tool | Gate |
|---|---|---|
| Lint + format | Ruff (lint + formatter) | CI fails on any finding |
| Type check | mypy (or Pyright), strict on `app/` | CI fails on error |
| Tests | pytest + pytest-asyncio + pytest-cov | CI fails below the agreed coverage floor on changed code |
| HTTP tests | `httpx` ASGI transport via FastAPI `TestClient` | — |
| DB tests | Postgres in a container (not SQLite — partial indexes, JSONB and partitioning must be exercised on the real engine) | — |
| Factories | `factory_boy` or plain fixtures | — |
| Security lint | Bandit (or Ruff's `S` rules) | CI fails on high severity |
| Dependency audit | `pip-audit` | CI fails on known high/critical CVEs |

### 8.2 Frontend

| Purpose | Tool | Gate |
|---|---|---|
| Lint | ESLint (TypeScript + React + a11y plugins) | CI fails on error |
| Format | Prettier | CI fails on diff |
| Type check | `tsc --noEmit`, `strict: true` | CI fails on error |
| Unit/component tests | Vitest + React Testing Library | CI fails on failure |
| E2E | Playwright | CI fails on failure |
| Accessibility | `axe` assertions in component and E2E tests | CI fails on violations in gated flows |
| Dependency audit | `npm audit` | CI fails on high/critical |

### 8.3 Repository-wide

Pre-commit hooks run the formatters, linters and a **secret scanner** (`gitleaks` or `detect-secrets`) so a credential cannot reach a commit. Container images are scanned (Trivy or Grype) in CI and before any production deploy, with a documented policy for accepted findings. Markdown links in `docs/` are checked so this documentation set does not rot.

Commands are listed in [TESTING.md §16](TESTING.md#16-how-to-run-everything) and are marked **to implement** — none of them run today, because no code exists.

---

## 9. Version-pinning strategy

1. **Python:** manifest (`pyproject.toml`) declares compatible ranges; a lockfile (`uv.lock` or `poetry.lock`) pins exact versions and hashes; CI installs **from the lockfile only**. Pin the Python minor version in the base image.
2. **Node:** `package.json` declares ranges; `package-lock.json` is committed; CI uses `npm ci`. Node major version pinned in `.nvmrc` and the Dockerfile.
3. **Container images:** `image: name:tag@sha256:...` everywhere, including Compose. A tag alone is mutable and makes builds unreproducible.
4. **Observability components** (Prometheus, Loki, Alloy, Grafana, Alertmanager, exporters): pinned by digest, upgraded deliberately — Loki and Alloy config schemas change across majors.
5. **Database:** PostgreSQL major version pinned and recorded in the runbook; a major upgrade is a planned migration with a tested restore.
6. **Upgrade cadence:** automated dependency PRs weekly (security advisories immediately), each one gated on the full test suite. Majors are deliberate, reviewed changes, never auto-merged.
7. **Compatibility is resolved, not asserted.** Pydantic/FastAPI/SQLAlchemy major-version interactions and Tailwind/React-tooling interactions are the usual breakages; the resolver and the test suite decide, not this document.

---

## 10. Rules for introducing a new dependency

A new dependency is a permanent maintenance and security commitment. Before adding one:

1. **Does it need to exist?** Is the need real now, or speculative?
2. **Does the standard library or an existing dependency cover it?** `itertools`, `functools`, `datetime`, `pathlib`, `secrets`, `hashlib`, `Intl`, `URL`, native `<input type="date">`, CSS over JS, a DB constraint over application code.
3. **Is it a few lines?** Write the few lines. A transitive-dependency tree for a two-line utility is a bad trade.
4. **If it is still needed, evaluate:** licence compatibility; maintenance signals (recent releases, open-issue handling, number of maintainers); transitive dependency count; known CVE history; bundle/image size impact; whether it would be hard to remove later.
5. **Record it:** the PR description states the need, the alternatives considered, and the rung of the above list that failed.
6. **Never add** a dependency that executes shell commands on untrusted input, bundles its own credential storage, or requires loosening the CSP or the container's security profile.
7. **Frontend specifically:** no library that pulls in a second component system, a second CSS paradigm, or a second state-management philosophy. One of each.

Removing a dependency never needs this justification. Prefer deletion.

---

## 11. Contract synchronisation

The frontend and backend contract is kept aligned mechanically, not by discipline:

1. FastAPI generates the OpenAPI schema from Pydantic models.
2. A generator (`openapi-typescript` or equivalent) produces TypeScript types into a generated file that is **committed** and never hand-edited.
3. CI regenerates and fails if the committed output differs — a backend schema change that was not propagated breaks the build instead of the dashboard.
4. State enums (`OperationStatus`, `DeploymentStatus`, `ServiceState`, `HealthState`, `AlertSeverity`, `AlertStatus`) are defined once in the backend and flow through this pipeline. The values are fixed in [API_CONTRACTS.md §9](API_CONTRACTS.md#9-enums).

---

## 12. Technology decisions still open

| ID | Decision | Default until resolved |
|---|---|---|
| OPEN-01 | OIDC provider; claim→role mapping | Generic OIDC; roles assigned in DCP |
| ~~OPEN-03~~ | Alert rules file-managed vs in-app | **RESOLVED 2026-10-17: file-managed in git**; utilisation numbers are generated from `DCP_UTILISATION_THRESHOLDS` (`OPEN-28`) |
| ~~OPEN-04~~ | Redis-only sessions vs a durable session table | **RESOLVED 2026-10-13 (M1.3): Redis-only.** No `user_sessions` table; auth events still audited in PostgreSQL |
| OPEN-05 | Restricted agent: adopt (SSM hybrid / Arc) vs build | Adopt |
| OPEN-06 | Canonical secret store | Pluggable interface, one backend per deployment |
| ~~OPEN-08~~ | Prometheus/Loki retention and storage budget | **RESOLVED 2026-10-17: Prometheus 30 d, Loki 14 d**, no disk quota in v1; limits in Loki config are the bound |
| OPEN-09 | Windows service management | Out of scope; metrics only |
| ~~OPEN-13~~ | Notification channel config storage | **RESOLVED 2026-10-17: DB tables (M3.4)** |
| ~~OPEN-14~~ | Prometheus target discovery | **Resolved 2026-10-16 (M2.3):** DCP-generated `file_sd`, no reload call |
| ~~TS-01~~ | Charting library | **RESOLVED 2026-10-16: `d3-shape` + `d3-scale`, used directly, no React wrapper** ([§2.5](#25-charting-library)). The gap criterion was tested rather than assumed, and the test is kept as a standing regression test. Recharts rejected on bundle size (+129 kB gzipped, more than doubling the app) and on its `role="application"` default; uPlot rejected because a canvas renderer makes the criterion untestable — and it cannot be imported under jsdom at all |
| ~~TS-02~~ | systemd metrics source (systemd_exporter vs node_exporter collector vs agent) | **RESOLVED 2026-10-17: `node_exporter`'s systemd collector with a unit allowlist** ([§5.3](#53-systemd-metrics-integration)). Tested against a real systemd. Gap: no `sub_state`/`load_state`/enabled/PID from any exporter — `OPEN-29`, agent is the later source |
| TS-03 | Python package manager (uv vs Poetry vs pip-tools) | Undecided; any one of them, with a committed lockfile |
| TS-04 | Component primitive library | Undecided; must be headless and accessible |

Full open-decision register: [ARCHITECTURE.md §19](ARCHITECTURE.md#19-open-decisions).

---

## 13. Assumptions

1. All listed technologies are self-hosted in the management environment except the IdP, the cloud control planes, Jenkins and the secrets manager.
2. Operators' browsers are current evergreen versions; no legacy browser support is required.
3. One Prometheus and one Loki instance suffice for the initial estate; federation and object-storage backends are deferred.
4. The team is comfortable with Python and TypeScript; no additional language is introduced anywhere in the stack.

**No version in this stack has been installed, resolved, or compatibility-tested.** Nothing here reports a working integration.
