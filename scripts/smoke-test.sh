#!/usr/bin/env bash
# Compose smoke test (docs/TESTING.md §11).
#
# Steps are numbered to match §11 so the mapping is auditable, and the ones
# that cannot be covered yet say which milestone brings them rather than being
# quietly dropped:
#
#   §11.8  observability profile. The **Prometheus half is covered** as of
#          M2.1, by step 18: the exporter target is asserted `up` and a
#          templated query is asserted to return a number — both through the
#          backend's own client, so what passes is the code the API uses and
#          not a hand-written curl that happens to work. The **Loki half**
#          ("accepts a push and returns it") is still uncovered: Loki ingests
#          nothing until M3.1, so asserting it would be asserting a
#          placeholder (CLAUDE.md §7).
#   step 18b (M2.4, TS-02) goes beyond §11: unit state against a REAL systemd
#          in a fixture container (profile `fixtures`) -- see its header.
#          SMOKE_SYSTEMD=auto|require|skip; a fixture that cannot start is
#          reported as NOT RUN, never as a pass.
#   §11.12 "resource limits present in the production overlay" — there is no
#          production overlay in infra/compose yet. What exists is checked:
#          non-root, no privileged flags, no added capabilities, and limits
#          declared for the core services in the file we do have.
#
# This script drives a real login through the mock IdP, which is a development
# aid: passing here is evidence the stack is self-consistent, NOT that a real
# identity provider works (CLAUDE.md §7, infra/compose/mock-idp/README.md).
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-infra/compose/docker-compose.yml}"
# The `mocks` profile is not optional any more: §11.7 is "mock IdP login
# completes end to end", and §11.5 reads inventory through an authenticated
# request, so there has to be an identity provider to authenticate against.
COMPOSE=(docker compose -f "$COMPOSE_FILE" --profile core --profile mocks)
# M2.1: §11.8's Prometheus half. A separate invocation rather than a third
# `--profile` on COMPOSE, because `grafana` lives in the same profile and is still
# a placeholder — starting it would add time to every run and assert nothing
# (CLAUDE.md §7). `loki` is started by name in step 18c (M3.1).
OBSERVABILITY=(docker compose -f "$COMPOSE_FILE" --profile observability)
# Everything, for teardown: `down` only reaches services in the profiles it is
# given, so the core-and-mocks invocation would leave Prometheus running.
ALL_PROFILES=(docker compose -f "$COMPOSE_FILE" --profile core --profile mocks --profile observability --profile logs --profile fixtures)
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-240}"
# M2.3, step 18: tight agent-health timing for this run only, so "a stopped
# exporter becomes UNREACHABLE within the configured window" is asserted in
# about a minute instead of about three. They also prove the compose
# passthrough works — with the defaults (30 s / 60 s) the assertions below
# about the worker's settings would fail.
export DCP_AGENT_PROBE_INTERVAL_SECONDS=10
export DCP_AGENT_UNREACHABLE_AFTER_SECONDS=30
# M2.5, step 18a: tight host-health lines, so "a silent host goes STALE, then
# UNREACHABLE" is asserted in minutes instead of a quarter of an hour. Not
# tighter than this: a healthy host is last seen up to one scrape interval
# (15 s) plus one probe interval (10 s) before the reference, and a line inside
# that would flap a perfectly good host. `stale` and `unreachable` are far enough
# apart (30 s) that a sweep landing in between is certain to observe STALE.
export DCP_HEALTH_THRESHOLDS='{"default":{"warning_after_seconds":30,"stale_after_seconds":45,"unreachable_after_seconds":75}}'
FRONTEND="http://localhost:${FRONTEND_HOST_PORT:-5173}"
BACKEND="http://localhost:${BACKEND_HOST_PORT:-8000}"
IDP="http://localhost:${MOCK_IDP_HOST_PORT:-9000}"
ADMIN_EMAIL="admin@example.invalid"
JAR="$(mktemp)"
# Steps that could not run on this machine, reported at the end so a partial pass
# never reads as a complete one (the same reason check-all.sh lists what it skipped).
NOT_RUN=()

log()  { printf '\n=== %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

cleanup() {
  rm -f "$JAR"
  if [[ "${KEEP_STACK:-0}" != "1" ]]; then
    log "Tearing down"
    "${ALL_PROFILES[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Brings the stack up and waits for every service that declares a healthcheck.
# `--wait` is the assertion for §11.1: it fails if anything does not reach
# healthy, which is why the worker's broken healthcheck (issue 49) had to be
# fixed before this script could pass honestly.
bring_up() {
  "${COMPOSE[@]}" up -d --build --wait --wait-timeout "$TIMEOUT_SECONDS" \
    || { "${COMPOSE[@]}" ps; "${COMPOSE[@]}" logs --tail=80; fail "stack did not become healthy"; }
}

# One hop of a redirect chain, returning the Location. Each hop is taken
# explicitly rather than with `-L` because the mock IdP's authorise endpoint
# needs `&user=` appended to it mid-chain, and because a flow that is followed
# blindly cannot assert which hop went where.
hop() {
  curl -sS -c "$JAR" -b "$JAR" -o /dev/null -w '%{redirect_url}' --max-time 15 "$1"
}

api() {
  curl -sS -c "$JAR" -b "$JAR" --max-time 20 "$FRONTEND/api/v1$1"
}

log "1. Validating Compose configuration"
"${COMPOSE[@]}" config --quiet || fail "compose config is invalid"

log "2. Starting the full core stack plus mocks (§11.1)"
bring_up
"${COMPOSE[@]}" ps

log "3. Migrations are applied and at head (§11.2)"
current="$("${COMPOSE[@]}" exec -T backend alembic current 2>&1 | tr -d '\r')"
printf '%s\n' "$current"
grep -q '(head)' <<<"$current" || fail "alembic current is not at head"

log "4. Liveness (§11.3)"
curl -fsS --max-time 10 "$BACKEND/healthz" | grep -q '"status":"ok"' \
  || fail "/healthz did not report ok"

log "5. Readiness, every dependency true (§11.3)"
readyz="$(curl -fsS --max-time 15 "$BACKEND/readyz")" || fail "/readyz not reachable"
printf '%s\n' "$readyz"
grep -q '"database":true' <<<"$readyz" || fail "database check did not pass"
grep -q '"broker":true'   <<<"$readyz" || fail "broker check did not pass"
# The third required check, and the one worth asserting loudest: it compares
# the applied revision against the head this build ships. An earlier draft of
# this script checked only database and broker, and would therefore have
# called a schemaless stack ready — which is the exact fault step 19 found.
grep -q '"migrations":true' <<<"$readyz" || fail "migrations check did not pass"

log "6. Readiness leaks nothing"
for forbidden in postgres 5432 password psycopg Traceback; do
  grep -qi "$forbidden" <<<"$readyz" && fail "/readyz leaked '$forbidden'"
done
printf 'no forbidden token in the readiness payload\n'

log "7. Frontend is served through the proxy (§11.4)"
curl -fsS --max-time 15 "$FRONTEND/" | grep -q '<div id="root">' \
  || fail "frontend did not serve the app shell"
# The SPA and the API must share one origin (AD-02); the dev server's proxy is
# what reproduces that locally, and it silently did not until M1.3.
curl -fsS --max-time 15 "$FRONTEND/healthz" | grep -q '"status":"ok"' \
  || fail "the frontend proxy did not reach the backend"

log "8. Security headers are present"
headers="$(curl -fsS -D - -o /dev/null --max-time 10 "$BACKEND/healthz")"
grep -qi 'x-content-type-options: nosniff' <<<"$headers" || fail "missing nosniff"
grep -qi 'content-security-policy'         <<<"$headers" || fail "missing CSP"
grep -qi 'x-correlation-id'                <<<"$headers" || fail "missing correlation id"

log "9. Containers do not run as root (§11.12)"
for service in backend worker beat frontend; do
  uid="$("${COMPOSE[@]}" exec -T "$service" id -u | tr -d '\r')"
  [[ "$uid" != "0" ]] || fail "$service runs as root"
  printf '%s uid=%s\n' "$service" "$uid"
done

log "10. No privileged flags and no added capabilities (§11.12)"
for service in postgres redis backend worker beat frontend; do
  container="$("${COMPOSE[@]}" ps -q "$service")"
  [[ -n "$container" ]] || fail "$service has no container"
  privileged="$(docker inspect --format '{{.HostConfig.Privileged}}' "$container")"
  cap_add="$(docker inspect --format '{{.HostConfig.CapAdd}}' "$container")"
  no_new_priv="$(docker inspect --format '{{.HostConfig.SecurityOpt}}' "$container")"
  [[ "$privileged" == "false" ]] || fail "$service is privileged"
  [[ "$cap_add" == "[]" || "$cap_add" == "<no value>" ]] || fail "$service adds capabilities: $cap_add"
  grep -q 'no-new-privileges:true' <<<"$no_new_priv" || fail "$service lacks no-new-privileges"
  printf '%s privileged=%s cap_add=%s\n' "$service" "$privileged" "$cap_add"
done

log "11. Resource limits are declared for the core services (§11.12, partial)"
# Partial on purpose: §11 asks for limits in the *production overlay*, and no
# such overlay exists yet. This asserts the limits the file we have declares,
# so the check is true rather than aspirational.
limits="$("${COMPOSE[@]}" config | grep -c 'cpus:')"
[[ "$limits" -ge 6 ]] || fail "expected cpu limits on at least 6 services, found $limits"
printf '%s services declare a cpu limit\n' "$limits"
printf 'NOTE: no production overlay exists; its limits cannot be checked (PROGRESS issue 21/52)\n'

log "12. Worker and beat are connected, and ping round-trips (§11.9)"
# The worker's own healthcheck already pinged it by node name (that is what
# issue 49 fixed), so this asserts the stronger property: a task crosses the
# broker and comes back.
"${COMPOSE[@]}" exec -T backend python -c "
from app.workers.tasks.ping import ping
assert ping.delay().get(timeout=30) == 'pong'
print('ping -> pong')
" || fail "celery round-trip failed"
# Beat declares no healthcheck (see the comment in docker-compose.yml), so its
# evidence is its container state and its own startup line.
beat_state="$(docker inspect --format '{{.State.Status}}' "$("${COMPOSE[@]}" ps -q beat)")"
[[ "$beat_state" == "running" ]] || fail "beat is not running (state: $beat_state)"
"${COMPOSE[@]}" logs beat 2>&1 | grep -q 'beat: Starting' \
  || fail "beat did not log that the scheduler started"
printf 'beat state=%s, scheduler started\n' "$beat_state"

log "13. Mock IdP login completes end to end (§11.7)"
authorize_url="$(hop "$FRONTEND/api/v1/auth/login?next=/dashboard")"
[[ "$authorize_url" == "$IDP/authorize"* ]] \
  || fail "login did not redirect to the identity provider (got: ${authorize_url:-<none>})"
# The mock renders an account picker without `user=`; naming the account is
# what makes this scriptable.
callback_url="$(hop "${authorize_url}&user=admin")"
[[ "$callback_url" == *"/api/v1/auth/callback?"* ]] \
  || fail "the identity provider did not redirect back to the callback (got: ${callback_url:-<none>})"
landing="$(hop "$callback_url")"
printf 'callback landed on: %s\n' "${landing:-<no redirect>}"

session="$(api /auth/session)" || fail "session endpoint not reachable after login"
grep -q "$ADMIN_EMAIL" <<<"$session" || fail "the session is not the account that logged in"
printf 'logged in as %s\n' "$ADMIN_EMAIL"

log "14. Demonstration mode is reported by the API (§11.6)"
# The *banner* is asserted by the Playwright suite, which is the only place
# that can see rendered DOM; this asserts the flag the banner is wired to.
grep -q '"demo_mode":true' <<<"$session" \
  || fail "demo_mode is not true in a stack started with DCP_DEMO_MODE=true"
printf 'demo_mode: true\n'

log "15. Granting the first administrator through the application"
# JIT provisioning deliberately creates users with no roles, so the login
# above produced an identity with no access at all. The bootstrap CLI is the
# only way to grant the first role; exit 2 means an administrator already
# exists, which is success for a re-run.
set +e
"${COMPOSE[@]}" exec -T backend python -m app.admin.bootstrap --email "$ADMIN_EMAIL"
bootstrap_status=$?
set -e
[[ "$bootstrap_status" == "0" || "$bootstrap_status" == "2" ]] \
  || fail "bootstrap failed with exit $bootstrap_status"
printf 'bootstrap exit %s (0 granted, 2 already present)\n' "$bootstrap_status"

log "16. Seeding synthetic inventory"
"${COMPOSE[@]}" exec -T backend python -m app.inventory.seed || fail "inventory seed failed"

log "17. Seeded inventory is visible through the API (§11.5)"
# A role change takes effect on the next request with no re-login, so the
# session from step 13 is now an administrator.
servers="$(api /servers)" || fail "server list not reachable"
grep -q '\[DEMO\]' <<<"$servers" \
  || fail "seeded inventory is not visible (is the seed marked [DEMO]?)"
count="$(grep -o '"hostname"' <<<"$servers" | wc -l | tr -d ' ')"
printf 'server list returned %s rows, visibly synthetic\n' "$count"

log "18. Prometheus scrapes the host exporter and the templates query it (§11.8)"
# M2.1. The assertion runs *inside the backend container*, through
# app/metrics/client.py and app/metrics/templates.py, so what passes here is
# the code path the API will use — not a curl that happens to produce the same
# JSON. It also proves DCP_PROMETHEUS_URL is reaching the backend, which is
# the half scripts/check_env_example.py structurally cannot check (issue 54).
"${OBSERVABILITY[@]}" up -d --wait --wait-timeout "$TIMEOUT_SECONDS" prometheus node-exporter \
  || { "${OBSERVABILITY[@]}" logs --tail=50 prometheus node-exporter
       fail "prometheus/node-exporter did not become healthy"; }

# M2.3 (OPEN-14). The exporter is NOT in prometheus.yml any more: a target gets
# there only because a server was registered, which is the acceptance criterion
# ("registering a server with monitoring enabled results in a scraped target
# without hand-editing Prometheus config") and is why this step registers one
# through the API rather than assuming it.
printf '  registering node-exporter:9100 through the API\n'
account_id="$("${COMPOSE[@]}" exec -T postgres psql -U dcp -d dcp -tAc \
  "SELECT id FROM cloud_accounts WHERE provider = 'ON_PREM' AND deleted_at IS NULL LIMIT 1" \
  | tr -d '\r[:space:]')"
[[ -n "$account_id" ]] || fail "no ON_PREM cloud account to register against"
csrf="$(grep dcp_csrf "$JAR" | awk '{print $NF}')"
[[ -n "$csrf" ]] || fail "no CSRF cookie in the session jar"
registered="$(curl -sS -c "$JAR" -b "$JAR" --max-time 20 -X POST \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $csrf" -H "Origin: $FRONTEND" \
  -H "Idempotency-Key: smoke-m23-$RANDOM$RANDOM" \
  --data "{\"cloud_account_id\":\"$account_id\",\"provider_resource_id\":\"smoke-node-exporter\",\"hostname\":\"smoke-exporter.internal.example\",\"display_name\":\"Smoke exporter\",\"operating_system\":\"LINUX\",\"environment\":\"staging\",\"management_method\":\"RESTRICTED_AGENT\",\"monitoring_enabled\":true,\"metrics_instance\":\"node-exporter:9100\"}" \
  "$FRONTEND/api/v1/servers")"
grep -q '"metrics_instance":"node-exporter:9100"' <<<"$registered" \
  || fail "registering the exporter failed: $registered"
smoke_server="$(grep -o '"id":"[^"]*"' <<<"$registered" | head -1 | cut -d'"' -f4)"
[[ -n "$smoke_server" ]] || fail "could not read the new server's id"

# The worker writes the file, Prometheus watches it. Polling, with a deadline.
deadline=$((SECONDS + 90))
# `sh -c '...'` because Git Bash on Windows rewrites a bare /var/... argument
# into a Windows path before docker sees it; a quoted command string is left alone.
# Captured, then grepped: under `set -o pipefail`, `grep -q` closing the pipe
# early makes `cat` exit non-zero, and a *successful* match reads as a failure.
until file="$("${COMPOSE[@]}" exec -T worker sh -c 'cat /var/lib/dcp/targets/node.json' 2>/dev/null)" \
        && grep -q 'node-exporter:9100' <<<"$file"; do
  (( SECONDS < deadline )) || { "${COMPOSE[@]}" logs --tail=30 worker; fail "the target file never listed the registered server"; }
  sleep 3
done
printf '  target file lists the registered server\n'
# What the file must not contain: the seeded demo servers (their .invalid
# instances can never resolve) and anything a person typed.
file="$("${COMPOSE[@]}" exec -T worker sh -c 'cat /var/lib/dcp/targets/node.json' | tr -d '\r')"
grep -q 'demo.invalid' <<<"$file" && fail "a demo.invalid server leaked into the target file"
grep -q 'Smoke exporter' <<<"$file" && fail "a display name leaked into the target file"
printf '  demo servers and free text are absent from it\n'

# The configured intervals must actually reach the worker (PROGRESS issue 54:
# a setting missing from the compose passthrough silently keeps its default).
"${COMPOSE[@]}" exec -T worker python -c "
from app.core.config import get_settings
s = get_settings()
assert s.agent_unreachable_after_seconds == 30, s.agent_unreachable_after_seconds
assert s.agent_probe_interval_seconds == 10, s.agent_probe_interval_seconds
print('  worker sees the configured probe interval and unreachable window')
" || fail "the agent timing settings did not reach the worker"

"${COMPOSE[@]}" exec -T backend python - <<'PYTHON' || fail "prometheus assertions failed"
import asyncio
import time

from app.metrics import templates
from app.metrics.client import PrometheusClient, PrometheusUnavailableError

INSTANCE = "node-exporter:9100"
# Generous: at a 15s scrape interval the target is DOWN for a moment after
# Prometheus starts, and rate() needs two samples in its window, so the first
# usable CPU reading is up to ~35s away on a cold stack and longer on a
# two-core CI runner. Polling, never sleeping blindly.
DEADLINE = 150


async def await_value(client, template_id, predicate, label):
    query = templates.render(template_id, "LINUX", [INSTANCE])
    end = time.monotonic() + DEADLINE
    last = "no sample yet"
    while time.monotonic() < end:
        try:
            samples = await client.query(query)
        except PrometheusUnavailableError as error:
            last = f"unavailable: {error.reason}"
        else:
            if samples and samples[0].value is not None and predicate(samples[0].value):
                print(f"  {label}: {samples[0].value} at {samples[0].timestamp.isoformat()}")
                return samples[0]
            last = f"got {samples[0].value if samples else None!r}"
        await asyncio.sleep(5)
    raise SystemExit(f"FAIL: {label} never arrived within {DEADLINE}s ({last})")


async def main():
    client = PrometheusClient()
    print(f"  querying {client.settings.prometheus_url}")

    # §11.8, first half: "Prometheus has the bundled exporter target up".
    # Read through the agent_up template, which is up{job="node",instance=...}
    # -- so this also proves the job label in prometheus.yml and the one in
    # templates.NODE_JOB still agree.
    await await_value(client, "agent_up", lambda value: value == 1.0, "agent_up")

    # §11.8, second half: "a metric query returns data". Four more templates, so
    # that one series existing does not pass for the catalogue working: a ratio,
    # a clock expression, a plain gauge and a counter-derived rate.
    await await_value(client, "memory_utilisation", lambda v: 0.0 < v <= 100.0, "memory_utilisation")
    await await_value(client, "uptime_seconds", lambda v: v > 0.0, "uptime_seconds")
    await await_value(client, "load_average", lambda v: v >= 0.0, "load_average")
    await await_value(client, "cpu_utilisation", lambda v: 0.0 <= v <= 100.0, "cpu_utilisation")

    # The remaining five are asserted to be VALID PromQL but not to return a
    # value, and the split is deliberate. node-exporter runs in a container with
    # no host mounts, so which filesystems, disks and interfaces it sees is an
    # environment detail: on the development machine all five answer (disk from
    # three ext4 bind mounts, network from eth0 once `lo` is filtered out), but
    # a runner with a different storage driver could legitimately expose none of
    # them, and a smoke test that failed there would be wrong about the code.
    #
    # Prometheus answers a syntactically invalid query with 400, which the client
    # maps to provider_error — so "no exception" IS the syntax assertion, and it
    # is the real risk for these five: they are the templates with label filters
    # and `without` clauses in them. The value is printed either way, so the run
    # records what this environment actually produced.
    for template_id in (
        "memory_available_bytes",
        "disk_utilisation",
        "disk_available_bytes",
        "disk_io",
        "network_throughput",
    ):
        query = templates.render(template_id, "LINUX", [INSTANCE])
        try:
            samples = await client.query(query)
        except PrometheusUnavailableError as error:
            raise SystemExit(f"FAIL: {template_id} is not a valid query ({error.reason})")
        if samples:
            print(f"  {template_id}: {samples[0].value}")
        else:
            print(f"  {template_id}: valid, no series in this environment")

    # M2.2. `collected_at` rests on `timestamp(up{...})` returning the SAMPLE's own
    # time rather than the moment of evaluation -- an instant query stamps every
    # result with its evaluation time, so if `timestamp()` did the same the age
    # would always be 0 and `stale` could never fire. The scripted tests cannot
    # settle that, only a real Prometheus can. A scrape time is never exactly the
    # evaluation time, so a strictly positive age that stays under a scrape
    # interval's worth of lookback is the signature of the real behaviour.
    import uuid
    from datetime import UTC, datetime, timedelta

    from app.inventory.models import Server
    from app.metrics import readings

    scrape = await client.query(templates.render_scrape_timestamp("LINUX", [INSTANCE]))
    if len(scrape) != 1 or scrape[0].value is None:
        raise SystemExit(f"FAIL: expected one scrape timestamp, got {scrape!r}")
    scraped_at = datetime.fromtimestamp(scrape[0].value, tz=UTC)
    age = (scrape[0].timestamp - scraped_at).total_seconds()
    if not 0.0 < age < 60.0:
        raise SystemExit(f"FAIL: scrape age {age}s is not a sample time (evaluation time would be 0)")
    print(f"  scrape timestamp: {scraped_at.isoformat()} ({age:.1f}s before evaluation)")

    # The same path the API uses, end to end, against the real exporter.
    server = Server(
        id=uuid.uuid4(), hostname="smoke", display_name="smoke", operating_system="LINUX",
        environment="staging", monitoring_enabled=True, metrics_instance=INSTANCE,
    )
    chosen = readings.template_ids_or_error(["cpu_utilisation", "load_average", "agent_up"])
    current = await readings.current(server, chosen, client=client)
    for reading in current.metrics:
        if reading.data_state != "ok" or reading.value is None or reading.collected_at is None:
            raise SystemExit(f"FAIL: {reading.template} is {reading.data_state} ({reading.unavailable_reason})")
    print("  current: " + ", ".join(f"{r.template}={r.value:.2f}" for r in current.metrics))

    # A real range query. The first minutes of a cold stack have no rate() window,
    # so a genuine series with LEADING HOLES is what comes back -- the case the
    # chart must draw as a gap, produced by a real Prometheus rather than by a
    # fixture. Holes must be null, and at least one point must be a number.
    # Polled, like every other reading here: since M2.3 the series begin when the
    # server is *registered* (the target is no longer static), and `rate()` needs
    # two samples in its window, so the first usable point is a little way off.
    chosen = readings.template_ids_or_error(["cpu_utilisation"])
    deadline = time.monotonic() + DEADLINE
    while True:
        end = datetime.now(UTC).replace(microsecond=0)
        end = end.replace(second=end.second - end.second % 15)
        history = await readings.history(
            server, chosen, start=end - timedelta(minutes=10), end=end, step_seconds=15, client=client
        )
        [series] = history.series
        if series.data_state == "ok" and series.points is not None:
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL: cpu history is {series.data_state} ({series.unavailable_reason})")
        await asyncio.sleep(5)
    values = [value for _, value in series.points]
    if len(values) != 41 or all(value is None for value in values):
        raise SystemExit(f"FAIL: expected 41 grid points with data, got {len(values)} / {values!r}")
    print(f"  history: {len(values)} points, {sum(v is None for v in values)} holes, none zero-filled")

    # An instance nobody scrapes returns NOTHING, which is what makes the
    # unavailable path honest: no series, so no number, so no zero.
    absent = await client.query(templates.render("cpu_utilisation", "LINUX", ["nowhere.invalid:9100"]))
    if absent:
        raise SystemExit(f"FAIL: an unscraped instance returned {absent!r}")
    print("  an unscraped instance returns no series, not a zero")

    # A WINDOWS server is unsupported, not empty (OPEN-09).
    try:
        templates.render("cpu_utilisation", "WINDOWS", [INSTANCE])
    except templates.UnsupportedOsFamilyError:
        print("  WINDOWS resolves to unsupported, not to a Linux query")
    else:
        raise SystemExit("FAIL: a WINDOWS render produced a query")


asyncio.run(main())
print("  prometheus: exporter up and templated queries return data")
PYTHON

# M2.3: agent health, observed rather than assumed. The registered server goes
# HEALTHY; stopping the exporter makes it UNREACHABLE within the configured
# window; starting it again recovers. The window is 30 s and the probe runs
# every 10 s for this run (exported at the top), so each transition is bounded
# by about a minute and the deadlines below are generous multiples of that.
agent_status() {
  api "/servers/$smoke_server" | grep -o '"agent_status":"[^"]*"' | cut -d'"' -f4
}
await_agent_status() {
  local want="$1" limit="$2" got=""
  local deadline=$((SECONDS + limit))
  while (( SECONDS < deadline )); do
    got="$(agent_status)"
    [[ "$got" == "$want" ]] && return 0
    sleep 3
  done
  fail "agent_status never became $want within ${limit}s (last: ${got:-<none>})"
}

printf '  waiting for the probe to mark the registered server HEALTHY\n'
await_agent_status HEALTHY 120
seen="$(api "/servers/$smoke_server" | grep -o '"last_seen_at":"[^"]*"' | cut -d'"' -f4)"
[[ -n "$seen" ]] || fail "a HEALTHY server has no last_seen_at"
printf '  HEALTHY, last seen %s\n' "$seen"

# Parsed rather than grepped: this needs "every [DEMO] row", which a pipeline of
# grep cannot say, and under `set -e` a grep with no match ends the script with
# no message at all.
api "/servers?limit=200" | "${COMPOSE[@]}" exec -T backend python -c "
import json, sys
rows = json.load(sys.stdin)['items']
demo = [r for r in rows if r['display_name'].startswith('[DEMO]')]
assert demo, 'no seeded demo servers were listed'
bad = {r['agent_status'] for r in demo} - {'UNKNOWN'}
assert not bad, f'seeded demo servers reported {bad}'
print(f'  {len(demo)} seeded demo servers stay UNKNOWN (never scraped, never asserted)')
" || fail "seeded demo servers should stay UNKNOWN"

printf '  stopping the exporter\n'
"${OBSERVABILITY[@]}" stop node-exporter >/dev/null
await_agent_status UNREACHABLE 150
printf '  UNREACHABLE after the configured window\n'

"${OBSERVABILITY[@]}" start node-exporter >/dev/null
await_agent_status HEALTHY 150
printf '  HEALTHY again once the exporter returned\n'

log "18a. The status sweep, host health, and a Prometheus outage that ages nothing (M2.5, AD-14)"
# The registered exporter is the host. Registration lands PENDING and the sweep
# never touches PENDING (D1), so an administrator accepts it first -- through the
# API, as a person would.
detail_field() {
  api "/servers/$smoke_server" \
    | "${COMPOSE[@]}" exec -T backend python -c 'import json,sys; d=json.load(sys.stdin); print('"$1"')'
}
summary_field() {
  api "/dashboard/summary" \
    | "${COMPOSE[@]}" exec -T backend python -c 'import json,sys; d=json.load(sys.stdin); print('"$1"')'
}
sweep_now() {
  "${COMPOSE[@]}" exec -T worker python -c \
    "from app.workers.tasks.monitoring import sweep_status; print(sweep_status())" | tail -1
}
status_changes() {
  "${COMPOSE[@]}" exec -T postgres psql -U dcp -d dcp -tAc \
    "SELECT count(*) FROM audit_events WHERE target_id = '$smoke_server' AND action_type = 'SERVER_STATUS_CHANGED' AND actor_label = 'system:health-sweep'" \
    | tr -d '\r[:space:]'
}
# Sweeps by hand while polling (the beat entry runs once a minute, and is proven
# separately below), so each transition is bounded by the thresholds and not by
# the schedule.
await_server_status() {
  local want="$1" limit="$2" got=""
  local deadline=$((SECONDS + limit))
  while (( SECONDS < deadline )); do
    sweep_now >/dev/null
    got="$(detail_field 'd["status"]')"
    [[ "$got" == "$want" ]] && return 0
    sleep 3
  done
  fail "status never became $want within ${limit}s (last: ${got:-<none>})"
}
await_health() {
  local want="$1" limit="$2" got=""
  local deadline=$((SECONDS + limit))
  while (( SECONDS < deadline )); do
    got="$(detail_field 'd["health"]["state"]')"
    [[ "$got" == "$want" ]] && return 0
    sleep 3
  done
  fail "health never became $want within ${limit}s (last: ${got:-<none>})"
}

csrf="$(grep dcp_csrf "$JAR" | awk '{print $NF}')"
accepted="$(curl -sS -c "$JAR" -b "$JAR" --max-time 20 -X PATCH \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $csrf" -H "Origin: $FRONTEND" \
  --data '{"status":"MANAGED"}' "$FRONTEND/api/v1/servers/$smoke_server")"
grep -q '"status":"MANAGED"' <<<"$accepted" || fail "accepting the registration failed: $accepted"
printf '  an administrator accepted the registration: MANAGED\n'
before_updated="$(detail_field 'd["updated_at"]')"

# The beat entry is real and the worker runs it: a line only the scheduled
# task writes (a by-hand call logs in its own process, not the container's).
printf '  waiting for the beat to run the sweep on its own\n'
deadline=$((SECONDS + 150))
until worker_log="$("${COMPOSE[@]}" logs worker 2>&1)" && grep -q 'status_swept' <<<"$worker_log"; do
  (( SECONDS < deadline )) || fail "the scheduled sweep never ran"
  sleep 5
done
printf '  the scheduled sweep ran\n'

await_health healthy 90
[[ "$(detail_field 'd["stale"]')" == "False" ]] || fail "a healthy host is flagged stale"
[[ "$(detail_field 'd["status"]')" == "MANAGED" ]] || fail "the sweep moved a healthy host"
printf '  healthy, not stale, still MANAGED -- the sweep leaves a healthy host alone\n'

# The dashboard rollup agrees with the host, and counts every readable server.
rollup=""
for _ in 1 2 3 4 5 6; do
  rollup="$(summary_field '(d["server_health"]["data_state"], d["server_health"]["healthy"] is not None and d["server_health"]["healthy"] >= 1, sum(d["server_health"][k] for k in ("healthy","warning","stale","unreachable","unknown")) == d["servers"]["total"])' 2>/dev/null || true)"
  [[ "$rollup" == "('ok', True, True)" ]] && break
  sleep 5
done
[[ "$rollup" == "('ok', True, True)" ]] || fail "the health rollup is wrong or does not sum to the server total: $rollup"
printf '  dashboard rollup is ok, counts the host healthy, and sums to the server total\n'

printf '  stopping the exporter: nothing is scraped, Prometheus keeps answering\n'
"${OBSERVABILITY[@]}" stop node-exporter >/dev/null
await_server_status STALE 150
[[ "$(detail_field 'd["health"]["state"]')" == "stale" ]] || fail "status is STALE but health is not"
[[ "$(detail_field 'd["stale"]')" == "True" ]] || fail "a STALE host is not flagged stale"
printf '  STALE: status and health agree\n'
await_server_status UNREACHABLE 180
[[ "$(detail_field 'd["health"]["state"]')" == "unreachable" ]] || fail "status is UNREACHABLE but health is not"
printf '  UNREACHABLE: status and health agree\n'

[[ "$(status_changes)" == "2" ]] || fail "expected exactly two audited moves, got $(status_changes)"
meta="$("${COMPOSE[@]}" exec -T postgres psql -U dcp -d dcp -tAc \
  "SELECT string_agg(sanitized_metadata->>'from' || '>' || (sanitized_metadata->>'to'), ',' ORDER BY occurred_at) FROM audit_events WHERE target_id = '$smoke_server' AND action_type = 'SERVER_STATUS_CHANGED'" \
  | tr -d '\r[:space:]')"
[[ "$meta" == "MANAGED>STALE,STALE>UNREACHABLE" ]] || fail "the audited moves are wrong: $meta"
printf '  two audited moves by system:health-sweep: %s\n' "$meta"
[[ "$(detail_field 'd["updated_at"]')" == "$before_updated" ]] \
  || fail "a sweep flip moved updated_at, so an open edit form would answer 409"
printf '  updated_at did not move (migration 0006)\n'
changes=""
for _ in 1 2 3 4 5 6; do
  changes="$(summary_field '[(c["display_name"], c["new_status"]) for c in d["recent_health_changes"]["items"]][0]' 2>/dev/null || true)"
  [[ "$changes" == "('Smoke exporter', 'UNREACHABLE')" ]] && break
  sleep 5
done
[[ "$changes" == "('Smoke exporter', 'UNREACHABLE')" ]] || fail "recent_health_changes does not list the move: $changes"
printf '  the dashboard lists the move, newest first\n'

"${OBSERVABILITY[@]}" start node-exporter >/dev/null
await_server_status MANAGED 150
await_health healthy 90
[[ "$(status_changes)" == "3" ]] || fail "expected three audited moves after recovery, got $(status_changes)"
printf '  back to MANAGED and healthy once the exporter returned\n'

# Issue 67, against a real Prometheus. Stopping Prometheus itself silences every
# host at once; aged against the wall clock, that is a fleet outage and the
# sweep would call this host UNREACHABLE after 75 s. Aged against the last time
# Prometheus answered, nothing happens.
printf '  stopping Prometheus for longer than the unreachable line (90 s)\n'
"${OBSERVABILITY[@]}" stop prometheus >/dev/null
for _ in 1 2 3 4 5 6 7 8 9; do
  sweep_now >/dev/null
  sleep 10
done
[[ "$(detail_field 'd["status"]')" == "MANAGED" ]] \
  || fail "a Prometheus outage aged the host into $(detail_field 'd["status"]')"
[[ "$(status_changes)" == "3" ]] || fail "the sweep moved a host during a Prometheus outage"
printf '  still MANAGED, no move audited: an outage ages nothing\n'
# And the read path does not judge hosts against an answer it can no longer
# confirm: not evaluated, dated by when Prometheus last answered -- never "healthy".
verdict="$(detail_field '(d["health"]["state"], d["health"]["data_state"], d["stale"])')"
[[ "$verdict" == "(None, 'stale', None)" ]] \
  || fail "health during an outage should be not evaluated and stale, got $verdict"
printf '  health is "not evaluated" and dated, not a frozen verdict: %s\n' "$verdict"

"${OBSERVABILITY[@]}" start prometheus >/dev/null
await_health healthy 150
[[ "$(detail_field 'd["status"]')" == "MANAGED" ]] || fail "the host did not come back MANAGED after the outage"
[[ "$(status_changes)" == "3" ]] || fail "recovery from the outage wrote a status move"
printf '  Prometheus returned: healthy again, still MANAGED, still three moves\n'

log "18b. systemd unit state against a real systemd, exporter and Prometheus (TS-02, M2.4)"
# The ordinary node-exporter container has no system bus, so nothing in the
# normal stack can produce node_systemd_* series. This step starts a host that
# runs systemd (infra/compose/systemd-host, profile `fixtures`) and drives
# *real* transitions on it, asserting what the portal says through the API:
#
#   kill a unit            -> FAILED          (a stop is INACTIVE: a different fact)
#   stop a unit cleanly    -> INACTIVE
#   a unit never loaded    -> UNCOLLECTED     (the exporter reports no series)
#   the system bus fails   -> UNCOLLECTED     (host up, collector failing)
#   the host stops         -> UNKNOWN, never INACTIVE, for EVERY unit
#
# "Stop the exporter" and "stop the host" are one observable here: with the
# systemd collector inside node_exporter there is no separate systemd exporter
# to stop, so a stopped exporter IS an unreachable host to Prometheus. What the
# plan called UNCOLLECTED (host answers, no unit data) is the collector failing,
# which is what stopping the bus does. Recorded in docs/TESTING.md.
#
# SMOKE_SYSTEMD=auto (default)  run it; if the fixture cannot start on this
#                               machine, say so loudly and list it as NOT RUN.
#                  require      a fixture that cannot start is a failure.
#                  skip         do not run it; listed as NOT RUN.
# 'auto' exists because the fixture needs SYS_ADMIN and a writable cgroup tree,
# which not every Docker host grants. It never turns a failed ASSERTION into a
# pass: once the fixture is up, every check below must hold.
SMOKE_SYSTEMD="${SMOKE_SYSTEMD:-auto}"
FIXTURES=(docker compose -f "$COMPOSE_FILE" --profile fixtures)
systemd_host="systemd-host:9100"
systemd_server=""

in_host()   { "${FIXTURES[@]}" exec -T systemd-host "$@"; }
unit_json() { api "/servers/$systemd_server/services/$1" || true; }
# `{ ...; } || true`: under `set -e -o pipefail` a grep -o with no match would
# otherwise end the script with no message at all.
unit_state() { { unit_json "$1" | grep -o '"state":"[A-Z]*"' | head -1 | cut -d'"' -f4; } || true; }
await_unit() {
  local unit="$1" want="$2" limit="$3" got=""
  local deadline=$((SECONDS + limit))
  while (( SECONDS < deadline )); do
    got="$(unit_state "$unit")"
    [[ "$got" == "$want" ]] && return 0
    sleep 4
  done
  fail "$unit never became $want within ${limit}s (last: ${got:-<none>})"
}
refresh_services_now() {
  "${COMPOSE[@]}" exec -T worker python -c \
    "from app.workers.tasks.monitoring import refresh_services; print('  refresh:', refresh_services())" \
    || fail "the service refresh task failed"
}
# state -> the service_names the list returns for the fixture host, sorted.
listed_names() {
  { api "/services?server_id=$systemd_server&state=$1&limit=200" \
      | grep -o '"service_name":"[^"]*"' | cut -d'"' -f4 | sort | tr '\n' ' '; } || true
}
post_json() {
  curl -sS -c "$JAR" -b "$JAR" --max-time 20 -X POST \
    -H 'Content-Type: application/json' -H "X-CSRF-Token: $csrf" -H "Origin: $FRONTEND" \
    --data "$2" "$FRONTEND/api/v1$1"
}

systemd_assertions() {
  printf '  registering %s through the API, then four units on it\n' "$systemd_host"
  local created
  created="$(curl -sS -c "$JAR" -b "$JAR" --max-time 20 -X POST \
    -H 'Content-Type: application/json' -H "X-CSRF-Token: $csrf" -H "Origin: $FRONTEND" \
    -H "Idempotency-Key: smoke-m24-$RANDOM$RANDOM" \
    --data "{\"cloud_account_id\":\"$account_id\",\"provider_resource_id\":\"smoke-systemd-host\",\"hostname\":\"smoke-systemd.internal.example\",\"display_name\":\"Smoke systemd host\",\"operating_system\":\"LINUX\",\"environment\":\"staging\",\"management_method\":\"RESTRICTED_AGENT\",\"monitoring_enabled\":true,\"metrics_instance\":\"$systemd_host\"}" \
    "$FRONTEND/api/v1/servers")"
  grep -q "\"metrics_instance\":\"$systemd_host\"" <<<"$created" \
    || fail "registering the systemd host failed: $created"
  systemd_server="$(grep -o '"id":"[^"]*"' <<<"$created" | head -1 | cut -d'"' -f4)"
  [[ -n "$systemd_server" ]] || fail "could not read the systemd host's id"
  local unit reg
  for unit in dcp-fixture-app dcp-fixture-stoppable dcp-fixture-broken dcp-fixture-missing; do
    reg="$(post_json "/servers/$systemd_server/services" "{\"service_name\":\"$unit.service\"}")"
    grep -q "\"service_name\":\"$unit.service\"" <<<"$reg" || fail "registering $unit failed: $reg"
  done

  # An unregistered unit is not queried and not shown, whatever the exporter
  # reports: it must be a 404 (docs/SECURITY.md §15).
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' -c "$JAR" -b "$JAR" --max-time 20 \
    "$FRONTEND/api/v1/servers/$systemd_server/services/systemd-journald.service")"
  [[ "$code" == "404" ]] || fail "an unregistered unit answered $code, expected 404"
  printf '  an unregistered unit is 404\n'

  printf '  waiting for the target to be scraped and the first unit to read ACTIVE\n'
  await_unit dcp-fixture-app.service ACTIVE 180
  local detail fragment
  detail="$(unit_json dcp-fixture-app.service)"
  for fragment in '"restart_count":0' '"sub_state":null' '"load_state":null' \
                  '"enabled":null' '"main_pid":null' '"server_reachable":true' '"data_state":"ok"'; do
    grep -q "$fragment" <<<"$detail" || fail "the app unit lacks $fragment: $detail"
  done
  grep -q '"started_at":"20' <<<"$detail" || fail "a running unit has no started_at: $detail"
  printf '  app: ACTIVE, restart_count 0, started_at set, the four unsupplied fields null\n'

  [[ "$(unit_state dcp-fixture-broken.service)" == "FAILED" ]] \
    || fail "a unit that failed at boot is not FAILED"
  [[ "$(unit_state dcp-fixture-stoppable.service)" == "ACTIVE" ]] || fail "stoppable is not ACTIVE"
  [[ "$(unit_state dcp-fixture-missing.service)" == "UNCOLLECTED" ]] \
    || fail "a unit the host does not have is not UNCOLLECTED (it must never read INACTIVE)"
  printf '  broken: FAILED   missing: UNCOLLECTED (not INACTIVE)\n'

  printf '  killing the app unit (SIGKILL) and stopping another cleanly\n'
  in_host systemctl kill -s KILL dcp-fixture-app.service
  in_host systemctl stop dcp-fixture-stoppable.service
  await_unit dcp-fixture-app.service FAILED 90
  await_unit dcp-fixture-stoppable.service INACTIVE 90
  printf '  killed -> FAILED, stopped -> INACTIVE: two different facts, two different states\n'

  # The list path: the cache the refresh job writes, aged and host-checked.
  refresh_services_now
  local failed inactive unknown dash
  failed="$(listed_names FAILED)"
  [[ "$failed" == "dcp-fixture-app.service dcp-fixture-broken.service " ]] \
    || fail "the failed-service view is wrong: '$failed'"
  inactive="$(listed_names INACTIVE)"
  [[ "$inactive" == "dcp-fixture-stoppable.service " ]] || fail "the inactive list is wrong: '$inactive'"
  printf '  the failed-service view lists exactly: %s\n' "$failed"
  # The summary is cached for 15 s (DCP_DASHBOARD_CACHE_TTL_SECONDS), so an
  # answer from before the kill may still be served: poll rather than assert once.
  local counted=0 try
  for try in 1 2 3 4 5 6; do
    dash="$(api /dashboard/summary || true)"
    if grep -Eq '"failed_services":\{[^}]*"count":[1-9]' <<<"$dash"; then counted=1; break; fi
    sleep 5
  done
  (( counted )) || fail "the dashboard does not count the failed services: $dash"
  printf '  the dashboard counts them\n'

  printf '  breaking the system bus: the host answers, the collector cannot\n'
  in_host systemctl stop dbus.socket dbus.service
  await_unit dcp-fixture-app.service UNCOLLECTED 90
  grep -q '"server_reachable":true' <<<"$(unit_json dcp-fixture-app.service)" \
    || fail "a failing collector made the host look unreachable"
  printf '  UNCOLLECTED, host still reachable\n'
  in_host systemctl start dbus.socket
  await_unit dcp-fixture-app.service FAILED 90
  printf '  and back to FAILED once the bus returned\n'

  printf '  stopping the host\n'
  "${FIXTURES[@]}" stop systemd-host >/dev/null
  for unit in dcp-fixture-app dcp-fixture-stoppable dcp-fixture-broken dcp-fixture-missing; do
    await_unit "$unit.service" UNKNOWN 120
  done
  grep -q '"server_reachable":false' <<<"$(unit_json dcp-fixture-stoppable.service)" \
    || fail "an unreachable host is not reported unreachable"
  refresh_services_now
  [[ -z "$(listed_names INACTIVE)" ]] \
    || fail "a dead host's units were listed INACTIVE: '$(listed_names INACTIVE)'"
  [[ -z "$(listed_names FAILED)" ]] || fail "a dead host's units were listed FAILED"
  unknown="$(listed_names UNKNOWN)"
  [[ "$unknown" == "dcp-fixture-app.service dcp-fixture-broken.service dcp-fixture-missing.service dcp-fixture-stoppable.service " ]] \
    || fail "the dead host's units are not all UNKNOWN: '$unknown'"
  printf '  all four units UNKNOWN on the detail page and in the list; none INACTIVE (PRD §4.5)\n'
  printf '  systemd: unit state observed end to end against a real systemd\n'
}

case "$SMOKE_SYSTEMD" in
  skip)
    NOT_RUN+=("18b systemd unit state (SMOKE_SYSTEMD=skip)")
    printf 'NOT RUN: SMOKE_SYSTEMD=skip\n' ;;
  auto|require)
    if "${FIXTURES[@]}" up -d --build --wait --wait-timeout 180 systemd-host; then
      systemd_assertions
    elif [[ "$SMOKE_SYSTEMD" == "require" ]]; then
      "${FIXTURES[@]}" logs --tail=40 systemd-host || true
      fail "the systemd fixture did not start and SMOKE_SYSTEMD=require"
    else
      "${FIXTURES[@]}" logs --tail=40 systemd-host || true
      NOT_RUN+=("18b systemd unit state (the fixture could not start on this Docker host)")
      printf '\n*** NOT RUN: the systemd fixture could not start here (it needs SYS_ADMIN and a\n'
      printf '*** writable cgroup tree). Unit-state behaviour is covered by the unit and integration\n'
      printf '*** suites against scripted Prometheus answers, NOT against a real systemd. This run\n'
      printf '*** is reported as incomplete below.\n\n'
    fi ;;
  *) fail "SMOKE_SYSTEMD must be auto, require or skip (got: $SMOKE_SYSTEMD)" ;;
esac

log "18c. Loki accepts a push, returns it, and enforces its limits (§11.8 Loki half, M3.1)"
# Loki is started by name for the reason `OBSERVABILITY` says: `grafana` shares
# its profile and is still a placeholder.
"${OBSERVABILITY[@]}" up -d --wait --wait-timeout "$TIMEOUT_SECONDS" loki \
  || { "${OBSERVABILITY[@]}" logs --tail=50 loki; fail "loki did not become healthy"; }
loki_uid="$("${OBSERVABILITY[@]}" exec -T loki id -u | tr -d '\r')"
[[ "$loki_uid" != "0" ]] || fail "loki runs as root"
printf '  loki uid=%s\n' "$loki_uid"

# From the backend container, over the project network: the same route the
# M3.2 client will take, and nothing here depends on the published port.
"${COMPOSE[@]}" exec -T backend python - <<'PYTHON' || fail "loki assertions failed"
import json
import time
import urllib.error
import urllib.parse
import urllib.request

LOKI = "http://loki:3100"
# The seven labels of docs/ARCHITECTURE.md §5 point 1 (AD-21), and nothing else.
ALLOWED = {"environment", "server_id", "hostname", "application_id",
           "service", "source_type", "level"}
MARKER = f"smoke-m31-{int(time.time())}"


def push(stream: dict, line: str, ts_ns: int | None = None) -> tuple[int, str]:
    body = json.dumps({"streams": [{"stream": stream,
        "values": [[str(ts_ns or time.time_ns()), line]]}]}).encode()
    request = urllib.request.Request(f"{LOKI}/loki/api/v1/push", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def get(path: str, **params: str) -> str:
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    with urllib.request.urlopen(f"{LOKI}{path}{query}", timeout=10) as response:
        return response.read().decode()


stream = {"environment": "staging", "server_id": "smoke", "hostname": "smoke.invalid",
          "application_id": "smoke", "service": "smoke", "source_type": "application",
          "level": "info"}

status, _ = push(stream, MARKER)
assert status == 204, f"a push with the seven allowed labels was refused: {status}"
print("  a push with the seven allowed labels: 204")

for _ in range(15):
    result = json.loads(get("/loki/api/v1/query_range", query='{server_id="smoke"}'))["data"]["result"]
    lines = [v[1] for s in result for v in s["values"]]
    if MARKER in lines:
        break
    time.sleep(1)
else:
    raise AssertionError("the pushed line never came back from a query")
assert result[0]["stream"] == stream, result[0]["stream"]
print("  it comes back from a query, carrying exactly those seven labels")

# An eighth label is how a request id or a path would arrive. Rejected here is
# the backstop; the shipper drops such labels before they get this far.
status, text = push({**stream, "request_id": "r-1"}, MARKER + "-eight")
assert status == 400 and "label names" in text, (status, text)
print("  an eighth label is rejected (400): the cardinality backstop holds")

# Older than reject_old_samples_max_age (7 d): a flapping shipper cannot back-fill.
status, text = push(stream, MARKER + "-old", time.time_ns() - 8 * 86400 * 10**9)
assert status == 400 and "too old" in text, (status, text)
print("  an 8-day-old sample is rejected (400)")

labels = set(json.loads(get("/loki/api/v1/labels"))["data"])
assert labels <= ALLOWED, f"labels outside the allowlist: {sorted(labels - ALLOWED)}"
assert "service_name" not in labels
print(f"  every label Loki holds is on the allowlist: {sorted(labels)}")

config = get("/config")
assert "retention_enabled: true" in config, "compactor retention is off"
assert "retention_period: 2w" in config, "retention is not 14 days (OPEN-08)"
assert "reporting_enabled: false" in config, "Loki would send usage statistics"
# M3.2: the log API reads up to 5000 lines for a continuation page and a tie-group
# completion (backend/app/logs/service.py LOKI_MAX_ENTRIES); a lower ceiling makes
# every 1000-line page a 400 from Loki.
assert "max_entries_limit_per_query: 5000" in config, "Loki's read ceiling is below what the API reads"
print("  retention is 14 d with the compactor on; usage reporting is off")
PYTHON

log "18d. Alloy ships the sample logs: closed labels, redaction, fail-closed identity (M3.1)"
# Started by name, like step 18c, for the same reason (grafana shares the profile).
LOGS=(docker compose -f "$COMPOSE_FILE" --profile observability --profile logs)
"${LOGS[@]}" up -d --build --wait --wait-timeout "$TIMEOUT_SECONDS" loki sample-logs alloy \
  || { "${LOGS[@]}" logs --tail=60 alloy sample-logs; fail "alloy/sample-logs did not become healthy"; }

# What SECURITY.md §7 and §23 say about the shipper, asserted on the running
# container rather than on the file that was supposed to configure it.
alloy_id="$("${LOGS[@]}" ps -q alloy)"
[[ -n "$alloy_id" ]] || fail "alloy has no container"
alloy_uid="$(docker exec "$alloy_id" id -u | tr -d '\r')"
[[ "$alloy_uid" != "0" ]] || fail "alloy runs as root"
[[ -z "$(docker port "$alloy_id")" ]] || fail "alloy publishes a port: $(docker port "$alloy_id")"
[[ "$(docker inspect --format '{{.HostConfig.Privileged}}' "$alloy_id")" == "false" ]] || fail "alloy is privileged"
if docker inspect --format '{{range .Mounts}}{{.Source}} {{end}}' "$alloy_id" | grep -q 'docker.sock'; then
  fail "the Docker socket is mounted into alloy (AD-22)"
fi
printf '  alloy uid=%s, no published port, not privileged, no Docker socket (AD-22)\n' "$alloy_uid"

"${LOGS[@]}" exec -T sample-logs python - ingestion < scripts/smoke_logs.py \
  || { "${LOGS[@]}" logs --tail=40 alloy; fail "the Alloy ingestion assertions failed"; }

# Fail closed: a shipper started with a bad identity must STORE NOTHING for it.
# Each case is a throwaway Alloy with its own state and port. The assertion is
# the drop COUNTER first (the lines were seen and refused) and Loki's silence
# second -- silence alone is also what a container that never started looks like
# (it did, once: Git Bash rewrote --storage.path, M3.1 commit 3).
bad_alloy() {  # name reason hostname VAR=value
  local name="$1" reason="$2" host="$3" override="$4"
  MSYS_NO_PATHCONV=1 "${LOGS[@]}" run -d --no-deps --name "$name" \
    -e "ALLOY_HOSTNAME=$host" -e "$override" alloy run \
    --server.http.listen-addr=127.0.0.1:12346 --disable-reporting \
    --storage.path=/tmp/alloy-bad /etc/alloy/config.alloy >/dev/null \
    || fail "could not start the $reason case"
  local deadline=$((SECONDS + 90)) metrics=""
  until metrics="$(docker exec "$name" bash -c 'exec 3<>/dev/tcp/127.0.0.1/12346; printf "GET /metrics HTTP/1.0\r\n\r\n" >&3; cat <&3' 2>/dev/null)" \
          && grep -Eq "loki_process_dropped_lines_total\{[^}]*reason=\"$reason\"\} [1-9]" <<<"$metrics"; do
    (( SECONDS < deadline )) || { docker logs --tail=20 "$name" || true; docker rm -f "$name" >/dev/null; fail "no '$reason' drops counted"; }
    sleep 3
  done
  printf '  %s: lines dropped and counted\n' "$reason"
}
bad_alloy smoke-alloy-bad-1 bad_server_id      bad-server.internal.example "ALLOY_SERVER_ID=not-a-uuid"
bad_alloy smoke-alloy-bad-2 bad_environment    bad-env.internal.example    "ALLOY_ENVIRONMENT=prod"
bad_alloy smoke-alloy-bad-3 bad_hostname       "bad host!"                 "ALLOY_SERVER_ID=00000000-0000-4000-8000-00000000a1b3"
bad_alloy smoke-alloy-bad-4 bad_application_id bad-appid.internal.example  "ALLOY_APPLICATION_ID=not-a-uuid"
for h in bad-server.internal.example bad-env.internal.example "bad host!"; do
  "${LOGS[@]}" exec -T sample-logs python - absent "$h" < scripts/smoke_logs.py || fail "'$h' reached Loki"
done
"${LOGS[@]}" exec -T sample-logs python - appid-dropped bad-appid.internal.example < scripts/smoke_logs.py \
  || fail "a malformed application_id was not dropped alone"
docker rm -f smoke-alloy-bad-1 smoke-alloy-bad-2 smoke-alloy-bad-3 smoke-alloy-bad-4 >/dev/null

log "18e. Alloy reads a REAL systemd journal (M3.1)"
# The fixture from step 18b writes a persistent journal to a volume; alloy-journal
# reads it through the same pipeline. Skipped, and listed as NOT RUN, wherever
# 18b is: it needs the same SYS_ADMIN fixture.
if [[ "$SMOKE_SYSTEMD" == "skip" ]]; then
  NOT_RUN+=("18e journald (SMOKE_SYSTEMD=skip)")
  printf 'NOT RUN: SMOKE_SYSTEMD=skip\n'
elif "${FIXTURES[@]}" up -d --wait --wait-timeout 180 systemd-host alloy-journal; then
  in_host sh -c 'logger -t dcp-fixture-app -p user.err "m31 journal test line err password=canary-journal-0009"; logger -t dcp-fixture-app -p user.info "m31 journal test line info"; journalctl --flush'
  "${LOGS[@]}" exec -T sample-logs python - journal < scripts/smoke_logs.py \
    || { "${FIXTURES[@]}" logs --tail=40 alloy-journal; fail "the journald assertions failed"; }
elif [[ "$SMOKE_SYSTEMD" == "require" ]]; then
  "${FIXTURES[@]}" logs --tail=40 systemd-host alloy-journal || true
  fail "the journald fixture did not start and SMOKE_SYSTEMD=require"
else
  "${FIXTURES[@]}" logs --tail=40 systemd-host alloy-journal || true
  NOT_RUN+=("18e journald (the systemd fixture, or alloy-journal, could not start here)")
  printf '\n*** NOT RUN: journald ingestion needs the systemd fixture. The file sources are\n'
  printf '*** covered by 18d; the journal source is configured but NOT observed this run.\n\n'
fi

log "18f. The log search API against a REAL Loki and the real pipeline (M3.2)"
# 18d left loki, sample-logs and alloy running. The API is exercised as the SPA
# calls it (session cookie + CSRF), from inside the project network, by
# scripts/smoke_logs_api.py -- see its docstring for what each part proves and why
# every "absent" there is paired with a "present".
"${COMPOSE[@]}" exec -T backend python -m app.inventory.seed --sample-log-host \
  || fail "could not register the sample log host (issue 94)"
# 18b-18e together outlast the 30-minute idle timeout, so the session from step 13
# may be gone. A 401 here would be about the harness and not the API: log in again.
authorize_url="$(hop "$FRONTEND/api/v1/auth/login?next=/dashboard")"
callback_url="$(hop "${authorize_url}&user=admin")"
hop "$callback_url" >/dev/null
[[ "$(api /auth/session)" == *"$ADMIN_EMAIL"* ]] || fail "could not log in again for step 18f"
session_cookie="$(grep -E '[[:space:]]dcp_session[[:space:]]' "$JAR" | awk '{print $NF}')"
csrf="$(grep dcp_csrf "$JAR" | awk '{print $NF}')"
[[ -n "$session_cookie" && -n "$csrf" ]] || fail "no session or CSRF cookie in the jar"
smoke_api() {
  "${LOGS[@]}" exec -T -e "SMOKE_COOKIE=dcp_session=$session_cookie; dcp_csrf=$csrf" \
    -e "SMOKE_CSRF=$csrf" -e "SMOKE_ORIGIN=$FRONTEND" \
    sample-logs python - "$1" < scripts/smoke_logs_api.py
}
smoke_api search || { "${LOGS[@]}" logs --tail=40 alloy; "${COMPOSE[@]}" logs --tail=40 backend; fail "the log search assertions failed"; }

# An outage must be SAID. Stopping Loki is the only way to see the API's answer to
# a real one; 503 with no entries is the assertion, an empty 200 is the failure.
"${LOGS[@]}" stop loki >/dev/null
smoke_api outage || { "${LOGS[@]}" start loki >/dev/null; fail "an unavailable Loki was not reported as unavailable"; }
"${LOGS[@]}" start loki >/dev/null
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$("${LOGS[@]}" ps -q loki)")" = "healthy" ]; do
  sleep 3
done
smoke_api recovered || fail "the log API did not recover after Loki came back"

log "19. A database outage degrades honestly, with no zeros (§11.10)"
"${COMPOSE[@]}" stop postgres >/dev/null
# Poll rather than sleep: the readiness probe has its own per-check deadline,
# so the transition is quick but not instant.
for _ in $(seq 1 10); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BACKEND/readyz")"
  [[ "$code" == "503" ]] && break
  sleep 2
done
[[ "$code" == "503" ]] || fail "expected 503 with postgres stopped, got $code"
curl -fsS --max-time 10 "$BACKEND/healthz" >/dev/null \
  || fail "liveness should survive a database outage"
# The point of §11.10: the dashboard must not answer a count of 0 when it
# cannot reach the database. It must fail or say unavailable — never invent a
# number (PRD §4.2, CLAUDE.md §5).
degraded="$(api /dashboard/summary || true)"
if grep -q '"count":0' <<<"$degraded"; then
  fail "the dashboard reported a zero count while the database was down"
fi
printf 'readyz=503, healthz=200, and no zero was substituted for an unknown\n'
"${COMPOSE[@]}" start postgres >/dev/null
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$("${COMPOSE[@]}" ps -q postgres)")" = "healthy" ]; do
  sleep 2
done
printf 'postgres is healthy again\n'

log "20. down -v then up reaches the same healthy state (§11.11)"
# No hidden local state: the volumes are destroyed, so migrations have to run
# from nothing and every service has to come back healthy on its own. Every
# profile, so step 18's Prometheus and its TSDB volume go too — a `down` scoped
# to core-and-mocks would leave them behind and `--remove-orphans` would then
# remove the containers while keeping the volume.
"${ALL_PROFILES[@]}" down -v --remove-orphans >/dev/null 2>&1
bring_up
current="$("${COMPOSE[@]}" exec -T backend alembic current 2>&1 | tr -d '\r')"
grep -q '(head)' <<<"$current" || fail "migrations are not at head after a volume wipe"
readyz="$(curl -fsS --max-time 15 "$BACKEND/readyz")" || fail "/readyz not reachable after recreate"
grep -q '"database":true'   <<<"$readyz" || fail "database not ready after recreate"
grep -q '"broker":true'     <<<"$readyz" || fail "broker not ready after recreate"
grep -q '"migrations":true' <<<"$readyz" || fail "migrations not applied after recreate"
printf 'stack returned to the same healthy state from empty volumes\n'

if (( ${#NOT_RUN[@]} > 0 )); then
  printf '\nSMOKE TEST PASSED, BUT INCOMPLETE: %s step(s) NOT RUN\n' "${#NOT_RUN[@]}"
  printf '  NOT RUN: %s\n' "${NOT_RUN[@]}"
else
  printf '\nSMOKE TEST PASSED\n'
fi
printf 'Covered docs/TESTING.md §11 steps 1-12. Step 8: the Prometheus half is\n'
printf '  asserted (step 18), and so is Loki accepting a push and returning it\n'
printf '  (step 18c). Alloy shipping the sample logs through to Loki is step 18d,\n'
printf '  a real systemd journal is step 18e, and the log search API against that real\n'
printf '  pipeline and Loki (including an outage) is step 18f.\n'
