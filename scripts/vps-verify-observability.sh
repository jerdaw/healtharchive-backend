#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: verify observability stack is up (read-only).

This script checks:
- Key ports are listening on loopback only
- Key HTTP health endpoints respond on loopback
- Prometheus sees expected targets as UP

Usage (on the VPS):
  cd /opt/healtharchive-backend
  ./scripts/vps-verify-observability.sh

Notes:
- This script does NOT send any test alerts.
- It does NOT require sudo.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

failures=0

ok() {
  printf "OK   %s\n" "$1"
}

fail() {
  printf "FAIL %s\n" "$1" >&2
  failures=$((failures + 1))
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

expect_loopback_port() {
  local port="$1"
  local label="$2"

  if ! have_cmd ss; then
    fail "${label}: ss not found (cannot verify ports)"
    return 0
  fi

  local locals
  locals="$(ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -E "(:|\\])${port}$" || true)"
  if [[ -z "${locals}" ]]; then
    fail "${label}: port ${port} is not listening"
    return 0
  fi

  local bad_lines=()
  while IFS= read -r local_addr; do
    [[ -z "${local_addr}" ]] && continue

    if [[ "${local_addr}" == 127.*:* ]]; then
      continue
    fi
    if [[ "${local_addr}" == "[::1]:"* || "${local_addr}" == "::1:"* ]]; then
      continue
    fi
    if [[ "${local_addr}" == "[0:0:0:0:0:0:0:1]:"* || "${local_addr}" == "0:0:0:0:0:0:0:1:"* ]]; then
      continue
    fi

    bad_lines+=("${local_addr}")
  done <<<"${locals}"

  if [[ ${#bad_lines[@]} -gt 0 ]]; then
    fail "${label}: port ${port} is not loopback-only (saw: ${bad_lines[*]})"
    return 0
  fi

  ok "${label}: listening on loopback (:${port})"
}

check_http() {
  local url="$1"
  local label="$2"

  if curl -fsS --max-time 3 "${url}" >/dev/null 2>&1; then
    ok "${label}: ${url}"
    return 0
  fi

  fail "${label}: ${url} (not reachable)"
  return 0
}

PORT_GRAFANA=3000
PORT_ADMIN_PROXY=8002
PORT_PROM=9090
PORT_ALERTMANAGER=9093
PORT_NODE=9100
PORT_PG=9187
PORT_PUSHOVER=9911

expect_loopback_port "${PORT_GRAFANA}" "Grafana"
expect_loopback_port "${PORT_PROM}" "Prometheus"
expect_loopback_port "${PORT_ALERTMANAGER}" "Alertmanager"
expect_loopback_port "${PORT_NODE}" "Node exporter"
expect_loopback_port "${PORT_PG}" "Postgres exporter"
expect_loopback_port "${PORT_PUSHOVER}" "Pushover relay"
expect_loopback_port "${PORT_ADMIN_PROXY}" "Admin proxy"

check_http "http://127.0.0.1:${PORT_PROM}/-/ready" "Prometheus ready"
check_http "http://127.0.0.1:${PORT_ALERTMANAGER}/-/ready" "Alertmanager ready"
check_http "http://127.0.0.1:${PORT_GRAFANA}/api/health" "Grafana health"
check_http "http://127.0.0.1:${PORT_NODE}/metrics" "Node exporter metrics"
check_http "http://127.0.0.1:${PORT_PG}/metrics" "Postgres exporter metrics"
check_http "http://127.0.0.1:${PORT_PUSHOVER}/-/health" "Pushover relay health"
check_http "http://127.0.0.1:${PORT_ADMIN_PROXY}/-/health" "Admin proxy health"

if curl -fsS --max-time 5 "http://127.0.0.1:${PORT_PROM}/api/v1/targets" >/dev/null 2>&1; then
  if have_cmd jq; then
    targets_json="$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT_PROM}/api/v1/targets")"
    down="$(printf '%s' "${targets_json}" | jq -r '.data.activeTargets[] | select(.health != "up") | "\(.labels.job) \(.discoveredLabels.__address__)"' || true)"
    if [[ -n "${down}" ]]; then
      fail "Prometheus targets DOWN: ${down}"
    else
      ok "Prometheus targets: all UP"
    fi

    missing_jobs=()
    for job in prometheus healtharchive_backend node_exporter postgres_exporter; do
      if ! printf '%s' "${targets_json}" | jq -e --arg job "${job}" '.data.activeTargets[] | select(.labels.job == $job)' >/dev/null 2>&1; then
        missing_jobs+=("${job}")
      fi
    done
    if [[ ${#missing_jobs[@]} -gt 0 ]]; then
      fail "Prometheus targets missing expected jobs: ${missing_jobs[*]}"
    else
      ok "Prometheus targets: expected jobs present"
    fi
  else
    ok "Prometheus targets: endpoint reachable (install jq for deeper checks)"
  fi
else
  fail "Prometheus targets: endpoint not reachable"
fi

echo
if [[ "${failures}" -eq 0 ]]; then
  echo "OK: observability checks passed."
  exit 0
fi

echo "ERROR: ${failures} observability check(s) failed." >&2
exit 1
