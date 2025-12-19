#!/usr/bin/env bash
set -u -o pipefail

usage() {
  cat <<'EOF'
Run a command and (optionally) ping Healthchecks-style URLs.

This is intended for systemd oneshot services where you want a "did it run?"
signal without embedding secrets in unit files.

Usage:
  ./scripts/systemd-healthchecks-wrapper.sh [--ping-var ENV_VAR_NAME] [--timeout-seconds N] -- <command...>

Examples:
  # Pings are optional; if the env var isn't set, the command still runs.
  HEALTHARCHIVE_HC_PING_REPLAY_RECONCILE=https://hc-ping.com/<uuid> \
    ./scripts/systemd-healthchecks-wrapper.sh --ping-var HEALTHARCHIVE_HC_PING_REPLAY_RECONCILE -- echo ok

Notes:
  - If pinging is enabled, this wrapper will:
    - best-effort ping: <url>/start
    - on success:        <url>
    - on failure:        <url>/fail
  - Ping failures do NOT make the wrapped command fail.
  - The wrapper does not print ping URLs (avoid leaking secrets to logs).
EOF
}

PING_VAR=""
TIMEOUT_SECONDS="10"

if [[ $# -eq 0 ]]; then
  usage >&2
  exit 2
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ping-var)
      PING_VAR="$2"
      shift 2
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --)
      shift 1
      break
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

if [[ $# -lt 1 ]]; then
  echo "ERROR: Missing command after --" >&2
  usage >&2
  exit 2
fi

ping_base=""
if [[ -n "${PING_VAR}" ]]; then
  # Indirect expansion: resolve the env var name to its value.
  # Example: PING_VAR=FOO, then ${!PING_VAR} reads $FOO.
  ping_base="${!PING_VAR:-}"
  ping_base="${ping_base%/}"
fi

ping() {
  local url="$1"
  if [[ -z "${url}" ]]; then
    return 0
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m "${TIMEOUT_SECONDS}" "${url}" >/dev/null 2>&1 || true
  fi
}

if [[ -n "${ping_base}" ]]; then
  ping "${ping_base}/start"
fi

"$@"
rc=$?

if [[ -n "${ping_base}" ]]; then
  if [[ ${rc} -eq 0 ]]; then
    ping "${ping_base}"
  else
    ping "${ping_base}/fail"
  fi
fi

exit "${rc}"
