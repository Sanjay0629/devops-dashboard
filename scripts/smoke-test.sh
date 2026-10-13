#!/usr/bin/env bash
# Compose smoke test (docs/TESTING.md §11).
#
# NOT YET VERIFIED: never executed. The Docker daemon was unavailable when M1.1
# was implemented, so this script is specification, not a passing check. The
# first person with a working daemon should run it and fix whatever it reveals.
#
# M1.1 covers steps 1-5 and 9. The remaining steps from docs/TESTING.md §11
# (seeded inventory, demo banner, mock IdP login, observability) arrive with the
# milestones that implement them.
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-infra/compose/docker-compose.yml}"
COMPOSE=(docker compose -f "$COMPOSE_FILE" --profile core)
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-180}"

log()  { printf '\n=== %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

cleanup() {
  if [[ "${KEEP_STACK:-0}" != "1" ]]; then
    log "Tearing down"
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

log "1. Validating Compose configuration"
"${COMPOSE[@]}" config --quiet || fail "compose config is invalid"

log "2. Starting the core stack"
"${COMPOSE[@]}" up -d --build --wait --wait-timeout "$TIMEOUT_SECONDS" \
  || { "${COMPOSE[@]}" ps; "${COMPOSE[@]}" logs --tail=80; fail "stack did not become healthy"; }

log "3. Liveness"
curl -fsS --max-time 10 http://localhost:8000/healthz | grep -q '"status":"ok"' \
  || fail "/healthz did not report ok"

log "4. Readiness"
readyz="$(curl -fsS --max-time 15 http://localhost:8000/readyz)" || fail "/readyz not reachable"
printf '%s\n' "$readyz"
grep -q '"database":true' <<<"$readyz" || fail "database check did not pass"
grep -q '"broker":true'   <<<"$readyz" || fail "broker check did not pass"

log "5. Readiness leaks nothing"
for forbidden in postgres 5432 password psycopg Traceback; do
  grep -qi "$forbidden" <<<"$readyz" && fail "/readyz leaked '$forbidden'"
done

log "6. Frontend is served"
curl -fsS --max-time 15 http://localhost:5173/ | grep -q '<div id="root">' \
  || fail "frontend did not serve the app shell"

log "7. Security headers are present"
headers="$(curl -fsS -D - -o /dev/null --max-time 10 http://localhost:8000/healthz)"
grep -qi 'x-content-type-options: nosniff' <<<"$headers" || fail "missing nosniff"
grep -qi 'content-security-policy'         <<<"$headers" || fail "missing CSP"
grep -qi 'x-correlation-id'                <<<"$headers" || fail "missing correlation id"

log "8. Containers do not run as root"
for service in backend worker beat; do
  uid="$("${COMPOSE[@]}" exec -T "$service" id -u | tr -d '\r')"
  [[ "$uid" != "0" ]] || fail "$service runs as root"
done

log "9. Broker round-trip through Celery"
"${COMPOSE[@]}" exec -T backend python -c "
from app.workers.tasks.ping import ping
assert ping.delay().get(timeout=30) == 'pong'
print('ping -> pong')
" || fail "celery round-trip failed"

log "10. Database outage degrades honestly"
"${COMPOSE[@]}" stop postgres >/dev/null
sleep 5
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://localhost:8000/readyz)"
[[ "$code" == "503" ]] || fail "expected 503 with postgres stopped, got $code"
curl -fsS --max-time 10 http://localhost:8000/healthz >/dev/null \
  || fail "liveness should survive a database outage"
"${COMPOSE[@]}" start postgres >/dev/null

printf '\nSMOKE TEST PASSED\n'
