#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: expose Grafana via tailnet-only HTTPS using Tailscale Serve.

Phase: "5 — Grafana (private stats page) with tailnet-only access"

Safe-by-default: dry-run unless you pass --apply.

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-enable-tailscale-serve-grafana.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-enable-tailscale-serve-grafana.sh --apply

Options:
  --apply             Actually apply tailscale serve config
  --grafana-url URL   Upstream Grafana URL (default: http://127.0.0.1:3000)
  --force             If tailscale serve is already configured, replace it

Rollback:
  sudo tailscale serve reset
EOF
}

APPLY="false"
FORCE="false"
GRAFANA_URL="http://127.0.0.1:3000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --force)
      FORCE="true"
      shift 1
      ;;
    --grafana-url)
      GRAFANA_URL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run() {
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ $*"
    return 0
  fi
  "$@"
}

if [[ "${APPLY}" == "true" && "${EUID}" -ne 0 ]]; then
  echo "ERROR: --apply requires root (use sudo)." >&2
  exit 1
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "ERROR: tailscale is not installed." >&2
  exit 1
fi

if [[ "${APPLY}" == "true" ]]; then
  if ! tailscale status >/dev/null 2>&1; then
    echo "ERROR: tailscale status failed (is tailscaled running and authenticated?)" >&2
    exit 1
  fi
fi

if [[ "${APPLY}" == "true" && "${FORCE}" != "true" ]]; then
  existing="$(tailscale serve status 2>/dev/null || true)"
  if [[ -n "${existing}" && "${existing}" != *"No serve config"* ]]; then
    echo "ERROR: tailscale serve already configured; refusing to overwrite." >&2
    echo "Run with --force to replace it, or rollback with: sudo tailscale serve reset" >&2
    exit 1
  fi
fi

if [[ "${FORCE}" == "true" ]]; then
  run tailscale serve reset
fi

if [[ "${APPLY}" != "true" ]]; then
  echo "+ (try: tailscale serve --bg ${GRAFANA_URL}; fallback to legacy syntax if unsupported)"
else
  set +e
  tailscale serve --bg "${GRAFANA_URL}" >/dev/null 2>&1
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    tailscale serve https / "${GRAFANA_URL}"
  fi
fi

if [[ "${APPLY}" == "true" ]]; then
  echo "OK: tailscale serve configured for Grafana."
else
  echo "DRY-RUN: no changes applied."
fi
echo
echo "Serve status:"
if [[ "${APPLY}" == "true" ]]; then
  tailscale serve status || true
else
  echo "+ tailscale serve status"
fi
