#!/usr/bin/env bash
# Resolve the image tags in Compose and the Dockerfiles to immutable digests.
#
# AD-16 requires digest pinning; M1.1 shipped exact tags only, because no Docker
# daemon was available to resolve digests and inventing one would be fabricating
# a fact. Run this once a daemon is available, then commit the result and close
# the corresponding entry in PROGRESS.md "Known issues".
set -euo pipefail

images=(
  postgres:17.2-alpine
  redis:7.4.1-alpine
  python:3.12.8-slim-bookworm
  node:22.12.0-alpine
  nginx:1.27.3-alpine
  prom/prometheus:v3.0.1
  prom/node-exporter:v1.8.2
  grafana/loki:3.3.2
  grafana/grafana:11.4.0
  grafana/alloy:v1.5.1
)

printf 'Resolving digests. Replace each tag with name:tag@sha256:... by hand,\n'
printf 'so the change is reviewable.\n\n'

for image in "${images[@]}"; do
  digest="$(docker buildx imagetools inspect "$image" --format '{{.Manifest.Digest}}' 2>/dev/null || true)"
  if [[ -n "$digest" ]]; then
    printf '%-40s %s\n' "$image" "$digest"
  else
    printf '%-40s UNRESOLVED (is the daemon running?)\n' "$image"
  fi
done
