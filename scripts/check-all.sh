#!/usr/bin/env bash
# Everything a contributor should run before opening a PR (docs/TESTING.md §16).
# Runs every check, then reports which failed — rather than stopping at the first.
set -uo pipefail

cd "$(dirname "$0")/.."
failed=()
skipped=()

run() {
  local label="$1"; shift
  printf '\n=== %s\n' "$label"
  if "$@"; then
    printf '    ok\n'
  else
    printf '    FAILED\n'
    failed+=("$label")
  fi
}

run "backend: ruff lint"      bash -c 'cd backend && uv run ruff check .'
run "backend: ruff format"    bash -c 'cd backend && uv run ruff format --check .'
run "backend: mypy"           bash -c 'cd backend && uv run mypy'
run "backend: bandit"         bash -c 'cd backend && uv run bandit -q -r app'
run "backend: unit tests"     bash -c 'cd backend && uv run pytest -m "not integration"'

# Integration tests need a real PostgreSQL. Without this branch the script
# printed "ALL CHECKS PASSED" while 61 tests silently skipped — a pass it had
# not earned, and the suite that covers the constraints, partitioning and audit
# immutability the schema depends on.
#
# M1.3 added authentication tests that need a real Redis as well (the session
# store, the single-use `state`, the rate limiter). Those skip individually
# without TEST_REDIS_URL, so it is reported for the same reason: a partial
# suite must not read as a complete one.
if [[ -n "${TEST_DATABASE_URL:-}" ]]; then
  run "backend: integration"  bash -c 'cd backend && uv run pytest -m integration'
  if [[ -z "${TEST_REDIS_URL:-}" ]]; then
    printf '    NOTE: TEST_REDIS_URL unset, so the authentication suite skipped\n'
    skipped+=("backend: integration (auth — needs TEST_REDIS_URL)")
  fi
else
  printf '\n=== backend: integration\n    SKIPPED — TEST_DATABASE_URL is not set (see docs/TESTING.md §16)\n'
  skipped+=("backend: integration")
fi
# Not --strict: see the comment in .github/workflows/ci.yml.
run "backend: pip-audit"      bash -c 'cd backend && uv run pip-audit'
run "frontend: eslint"        bash -c 'cd frontend && npx eslint .'
run "frontend: prettier"      bash -c 'cd frontend && npx prettier --check .'
run "frontend: tsc"           bash -c 'cd frontend && npx tsc --noEmit'
run "frontend: vitest"        bash -c 'cd frontend && npx vitest run'
run "frontend: build"         bash -c 'cd frontend && npm run build'
run "frontend: npm audit"     bash -c 'cd frontend && npm audit --audit-level=high'
# Regenerates in place, so a drift leaves the corrected file in the working
# tree ready to commit — which is the fix, not a side effect.
run "contracts: api types"    bash -c './scripts/generate-api-types.sh >/dev/null && git diff --exit-code -- frontend/src/api/schema.ts'
run "docs: link check"        python scripts/check_doc_links.py .

printf '\n========================================\n'
if (( ${#failed[@]} == 0 )); then
  if (( ${#skipped[@]} > 0 )); then
    printf 'CHECKS PASSED, but %d SKIPPED — a skipped suite is not a passing suite:\n' \
      "${#skipped[@]}"
    printf '  - %s\n' "${skipped[@]}"
    exit 0
  fi
  printf 'ALL CHECKS PASSED\n'
  exit 0
fi
printf '%d CHECK(S) FAILED:\n' "${#failed[@]}"
printf '  - %s\n' "${failed[@]}"
exit 1
