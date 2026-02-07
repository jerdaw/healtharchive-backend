#!/usr/bin/env bash
set -euo pipefail

# Opinionated wrapper around scripts/vps-deploy.sh for the single Hetzner VPS.
#
# Goals:
# - One command that is safe in interactive shells (no "set -e" persistence).
# - Refuse dirty working trees by default (avoids ad-hoc scp changes breaking deploys).
# - Provide a clear "backend-only" mode for when the public frontend is externally down (e.g., Vercel 402).

REPO_DIR="/opt/healtharchive-backend"
MODE="full" # full | backend-only

if [[ "${EUID}" -eq 0 ]]; then
  echo "ERROR: Do not run hetzdeploy as root." >&2
  echo "Run as the operator user (e.g., haadmin); the underlying deploy will sudo when needed." >&2
  echo "This avoids /opt repo/.venv permission drift and /tmp lockfile ownership mismatches." >&2
  exit 2
fi

usage() {
  cat <<'EOF'
HealthArchive VPS helper: hetzdeploy wrapper (safe defaults)

Usage:
  ./scripts/vps-hetzdeploy.sh [--mode MODE] [--repo-dir DIR] [--] [extra vps-deploy.sh args...]

Modes:
  full         Runs the normal deploy gate (includes public-surface verify).
  backend-only Skips public-surface verify (use only when frontend is externally broken, e.g. Vercel 402).

Notes:
  - This wrapper always does: git fetch --prune, checkout main, pull --ff-only.
  - It refuses dirty git state (same as vps-deploy.sh).
  - Extra args after -- are passed through to vps-deploy.sh.

Examples:
  cd /opt/healtharchive-backend
  ./scripts/vps-hetzdeploy.sh
  ./scripts/vps-hetzdeploy.sh --mode backend-only
  ./scripts/vps-hetzdeploy.sh -- --restart-replay
EOF
}

PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="${2:-}"
      shift 2
      ;;
    --)
      shift
      PASSTHROUGH+=("$@")
      break
      ;;
    *)
      PASSTHROUGH+=("$1")
      shift
      ;;
  esac
done

case "${MODE}" in
  full|backend-only) ;;
  *)
    echo "ERROR: --mode must be 'full' or 'backend-only' (got: ${MODE})" >&2
    exit 2
    ;;
esac

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "ERROR: Not a git repo: ${REPO_DIR}" >&2
  exit 1
fi

cd "${REPO_DIR}"

echo "HealthArchive VPS hetzdeploy"
echo "----------------------------"
echo "Repo: ${REPO_DIR}"
echo "Mode: ${MODE}"
echo ""

git fetch --prune
git checkout main >/dev/null

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Working tree is dirty; refusing to deploy." >&2
  echo "Hint: run: git status --porcelain" >&2
  git status --porcelain >&2
  exit 2
fi

git pull --ff-only

args=(
  "--apply"
  "--install-systemd-units"
  "--apply-alerting"
  "--baseline-mode" "live"
)

if [[ "${MODE}" == "backend-only" ]]; then
  args+=("--skip-public-surface-verify")
fi

exec ./scripts/vps-deploy.sh "${args[@]}" "${PASSTHROUGH[@]}"
