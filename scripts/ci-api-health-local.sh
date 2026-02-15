#!/usr/bin/env bash
set -euo pipefail

HEALTHARCHIVE_DATABASE_URL="${HEALTHARCHIVE_DATABASE_URL:-sqlite:///./ci-api-health.db}"
HEALTHARCHIVE_ARCHIVE_ROOT="${HEALTHARCHIVE_ARCHIVE_ROOT:-/tmp/healtharchive-api-health}"
export HEALTHARCHIVE_DATABASE_URL
export HEALTHARCHIVE_ARCHIVE_ROOT

mkdir -p "${HEALTHARCHIVE_ARCHIVE_ROOT}"

alembic upgrade head
ha-backend seed-sources

uvicorn ha_backend.api:app --host 127.0.0.1 --port 8765 --log-level warning &
UVICORN_PID=$!
trap 'kill "${UVICORN_PID}" >/dev/null 2>&1 || true' EXIT

for i in {1..30}; do
  if curl -s http://127.0.0.1:8765/api/health > /dev/null 2>&1; then
    break
  fi
  if [ "${i}" -eq 30 ]; then
    echo "Backend server failed to start"
    exit 1
  fi
  sleep 1
done

python scripts/verify_public_surface.py \
  --api-base http://127.0.0.1:8765 \
  --timeout 10 \
  --skip-frontend \
  --allow-empty-index \
  --allow-usage-disabled \
  --allow-exports-disabled \
  --allow-change-tracking-disabled
