#!/usr/bin/env bash
# Everything a contributor should run before opening a PR (docs/TESTING.md §16).
# Runs every check, then reports which failed — rather than stopping at the first.
set -uo pipefail

cd "$(dirname "$0")/.."
failed=()

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
run "backend: pytest"         bash -c 'cd backend && uv run pytest'
# Not --strict: see the comment in .github/workflows/ci.yml.
run "backend: pip-audit"      bash -c 'cd backend && uv run pip-audit'
run "frontend: eslint"        bash -c 'cd frontend && npx eslint .'
run "frontend: prettier"      bash -c 'cd frontend && npx prettier --check .'
run "frontend: tsc"           bash -c 'cd frontend && npx tsc --noEmit'
run "frontend: vitest"        bash -c 'cd frontend && npx vitest run'
run "frontend: build"         bash -c 'cd frontend && npm run build'
run "frontend: npm audit"     bash -c 'cd frontend && npm audit --audit-level=high'
run "docs: link check"        python scripts/check_doc_links.py .

printf '\n========================================\n'
if (( ${#failed[@]} == 0 )); then
  printf 'ALL CHECKS PASSED\n'
  exit 0
fi
printf '%d CHECK(S) FAILED:\n' "${#failed[@]}"
printf '  - %s\n' "${failed[@]}"
exit 1
