#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install/update the `hetzdeploy` wrapper command.

Safe-by-default: dry-run unless you pass --apply.

This installs the repo wrapper:
  scripts/vps-hetzdeploy.sh
as:
  /usr/local/bin/hetzdeploy

Why:
  - Avoid fragile shell aliases (especially ones that set `-euo pipefail`).
  - Ensure `hetzdeploy --mode backend-only` works as expected.

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run (prints what would happen):
  ./scripts/vps-install-hetzdeploy.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-install-hetzdeploy.sh --apply

Options:
  --apply       Actually install/update (otherwise dry-run)
  --copy        Copy the script (default)
  --symlink     Install as a symlink to the repo (updates automatically on git pull)
  --dst PATH    Destination path (default: /usr/local/bin/hetzdeploy)

Notes:
  - This does not modify your shell config. If you previously defined an alias named
    "hetzdeploy", remove it from ~/.bashrc and run: unalias hetzdeploy
EOF
}

APPLY="false"
INSTALL_MODE="copy" # copy | symlink
DST="/usr/local/bin/hetzdeploy"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --copy)
      INSTALL_MODE="copy"
      shift 1
      ;;
    --symlink)
      INSTALL_MODE="symlink"
      shift 1
      ;;
    --dst)
      DST="${2:-}"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC="${REPO_ROOT}/scripts/vps-hetzdeploy.sh"

if [[ ! -f "${SRC}" ]]; then
  echo "ERROR: Missing wrapper script: ${SRC}" >&2
  exit 1
fi

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

case "${INSTALL_MODE}" in
  copy|symlink) ;;
  *)
    echo "ERROR: invalid install mode: ${INSTALL_MODE}" >&2
    exit 2
    ;;
esac

dst_dir="$(dirname "${DST}")"
run install -d -m 0755 -o root -g root "${dst_dir}"

if [[ "${INSTALL_MODE}" == "copy" ]]; then
  run install -m 0755 -o root -g root "${SRC}" "${DST}"
else
  # Prefer a symlink so the command tracks the repo wrapper on git pull.
  run ln -sfn "${SRC}" "${DST}"
fi

echo "OK: hetzdeploy installed: ${DST}"
echo "Verify on the VPS:"
echo "  type hetzdeploy"
echo "  hetzdeploy --help"
echo ""
echo "If you previously set an alias named hetzdeploy, remove it:"
echo "  unalias hetzdeploy 2>/dev/null || true"
echo "  rg -n 'alias hetzdeploy=' ~/.bashrc ~/.profile ~/.bash_profile 2>/dev/null || true"

