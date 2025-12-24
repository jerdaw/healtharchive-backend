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

get_timeout_cmd() {
  if command -v timeout >/dev/null 2>&1; then
    echo "timeout"
    return 0
  fi
  if [[ -x "/usr/bin/timeout" ]]; then
    echo "/usr/bin/timeout"
    return 0
  fi
  if [[ -x "/bin/timeout" ]]; then
    echo "/bin/timeout"
    return 0
  fi
  echo ""
}

is_url_up() {
  local url="$1"
  local health_url="${url%/}/api/health"
  if ! command -v curl >/dev/null 2>&1; then
    return 0
  fi
  curl -fsS --max-time 2 "${health_url}" >/dev/null 2>&1
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
  if ! is_url_up "${GRAFANA_URL}"; then
    echo "ERROR: Grafana does not appear reachable at ${GRAFANA_URL} (try ${GRAFANA_URL%/}/api/health)." >&2
    echo "Start/fix Grafana first, then re-run this script." >&2
    exit 1
  fi

  echo "Configuring tailscale serve for Grafana upstream: ${GRAFANA_URL}"

  out=""
  rc=0
  timeout_cmd="$(get_timeout_cmd)"
  set +e
  if [[ -n "${timeout_cmd}" ]]; then
    out="$("${timeout_cmd}" 10s tailscale serve --bg "${GRAFANA_URL}" 2>&1)"
    rc=$?
  else
    out="$(tailscale serve --bg "${GRAFANA_URL}" 2>&1)"
    rc=$?
  fi
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    if [[ "${rc}" -eq 124 ]]; then
      if [[ "${out}" == *"Serve is not enabled on your tailnet."* ]]; then
        echo "ERROR: Tailscale Serve is disabled for this tailnet." >&2
        echo "${out}" >&2
        echo >&2
        echo "Enable it in the Tailscale admin console (the URL above), then re-run:" >&2
        echo "  sudo tailscale serve --bg ${GRAFANA_URL}" >&2
        echo "  sudo tailscale serve status" >&2
        echo >&2
        echo "Fallback (no Serve needed): use SSH port-forwarding over Tailscale:" >&2
        echo "  ssh -L 3000:127.0.0.1:3000 haadmin@<tailscale-host>" >&2
        echo "  # then open: http://127.0.0.1:3000" >&2
        exit 1
      fi

      echo "ERROR: tailscale serve --bg timed out after 10s." >&2
      echo "Output:" >&2
      echo "${out}" >&2
      exit 124
    fi

    if [[ "${out}" == *"unknown flag: --bg"* || "${out}" == *"flag provided but not defined"* || "${out}" == *"unknown shorthand flag"* ]]; then
      tailscale serve https / "${GRAFANA_URL}"
    else
      echo "ERROR: tailscale serve --bg failed." >&2
      echo "Output:" >&2
      echo "${out}" >&2
      exit "${rc}"
    fi
  fi

  status="$(tailscale serve status 2>/dev/null || true)"
  if [[ "${status}" == *"No serve config"* ]]; then
    echo "ERROR: tailscale serve did not create a serve config." >&2
    if [[ -n "${out}" ]]; then
      echo "Output from tailscale serve:" >&2
      echo "${out}" >&2
    fi
    echo "Try manually:" >&2
    echo "  sudo tailscale serve --bg ${GRAFANA_URL}" >&2
    echo "  sudo tailscale serve status" >&2
    exit 1
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
