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
# `--profile` on COMPOSE, because `loki` and `grafana` live in the same profile
# and are still placeholders — starting them would add two minutes to every
# run and assert nothing (CLAUDE.md §7).
OBSERVABILITY=(docker compose -f "$COMPOSE_FILE" --profile observability)
# Everything, for teardown: `down` only reaches services in the profiles it is
# given, so the core-and-mocks invocation would leave Prometheus running.
ALL_PROFILES=(docker compose -f "$COMPOSE_FILE" --profile core --profile mocks --profile observability)
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-240}"
FRONTEND="http://localhost:${FRONTEND_HOST_PORT:-5173}"
BACKEND="http://localhost:${BACKEND_HOST_PORT:-8000}"
IDP="http://localhost:${MOCK_IDP_HOST_PORT:-9000}"
ADMIN_EMAIL="admin@example.invalid"
JAR="$(mktemp)"

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
    end = datetime.now(UTC).replace(microsecond=0)
    end = end.replace(second=end.second - end.second % 15)
    start = end - timedelta(minutes=10)
    history = await readings.history(
        server, readings.template_ids_or_error(["cpu_utilisation"]),
        start=start, end=end, step_seconds=15, client=client,
    )
    [series] = history.series
    if series.data_state != "ok" or series.points is None:
        raise SystemExit(f"FAIL: cpu history is {series.data_state} ({series.unavailable_reason})")
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

printf '\nSMOKE TEST PASSED\n'
printf 'Covered docs/TESTING.md §11 steps 1-12, with step 8 partial:\n'
printf '  its Prometheus half is asserted (step 18); its Loki half needs M3.1.\n'
