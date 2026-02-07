#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: diff two hotpath-staleness evidence bundles

This compares the output directories produced by:
  ./scripts/vps-capture-hotpath-staleness-evidence.sh

Usage:
  ./scripts/vps-diff-hotpath-staleness-evidence.sh --before DIR --after DIR

Example (VPS):
  root=/srv/healtharchive/ops/observability/hotpath-staleness
  before=$(ls -1dt "${root}"/*pre-repair 2>/dev/null | head -n 1)
  after=$(ls -1dt "${root}"/*post-repair 2>/dev/null | head -n 1)
  ./scripts/vps-diff-hotpath-staleness-evidence.sh --before "${before}" --after "${after}"

Notes:
  - Read-only: this script only reads files and prints diffs.
  - If a file is missing in either bundle, it is skipped.
EOF
}

BEFORE=""
AFTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --before)
      BEFORE="${2:-}"
      shift 2
      ;;
    --after)
      AFTER="${2:-}"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${BEFORE}" || -z "${AFTER}" ]]; then
  echo "ERROR: --before and --after are required." >&2
  usage >&2
  exit 2
fi

if [[ ! -d "${BEFORE}" ]]; then
  echo "ERROR: before dir not found: ${BEFORE}" >&2
  exit 2
fi
if [[ ! -d "${AFTER}" ]]; then
  echo "ERROR: after dir not found: ${AFTER}" >&2
  exit 2
fi

echo "HealthArchive hotpath-staleness bundle diff"
echo "-----------------------------------------"
echo "before=${BEFORE}"
echo "after=${AFTER}"
echo ""

count_hits() {
  local dir="$1"
  local pat="$2"
  if command -v rg >/dev/null 2>&1; then
    rg -n "${pat}" "${dir}"/*.txt 2>/dev/null | wc -l | tr -d ' '
  else
    grep -RInE -- "${pat}" "${dir}"/*.txt 2>/dev/null | wc -l | tr -d ' '
  fi
}

echo "Quick signals (counts):"
echo "  before errno107_hits=$(count_hits "${BEFORE}" 'Errno 107|Transport endpoint is not connected')"
echo "  after  errno107_hits=$(count_hits "${AFTER}" 'Errno 107|Transport endpoint is not connected')"
echo "  before fuse_mounts=$(count_hits "${BEFORE}" 'fuse\.sshfs|fuse\.s')"
echo "  after  fuse_mounts=$(count_hits "${AFTER}" 'fuse\.sshfs|fuse\.s')"
echo ""

diff_one() {
  local rel="$1"
  local a="${BEFORE%/}/${rel}"
  local b="${AFTER%/}/${rel}"
  if [[ ! -f "${a}" || ! -f "${b}" ]]; then
    return 0
  fi
  echo "=== diff: ${rel} ==="
  # Use +e so we still print a header when files differ.
  set +e
  diff -u "${a}" "${b}"
  local rc=$?
  set -e
  echo ""
  return "${rc}"
}

# Keep this small and high-signal: focus on mounts, systemd state, and probes.
diff_one meta.txt || true
diff_one repo.txt || true
diff_one vps-crawl-status.txt || true
diff_one systemctl-status.txt || true
diff_one systemctl-show-storagebox.txt || true
diff_one systemctl-cat-storagebox.txt || true
diff_one systemctl-cat-tiering.txt || true
diff_one mount.txt || true
diff_one findmnt-storagebox.txt || true
diff_one tiering-binds.txt || true
diff_one tiering-hotpath-probes.txt || true
diff_one path-probes.txt || true
diff_one journal-storagebox.txt || true
diff_one journal-tiering.txt || true
diff_one journal-hotpath-watchdog.txt || true
diff_one journal-worker.txt || true
diff_one dmesg-tail.txt || true
diff_one watchdog-metrics.prom || true
diff_one watchdog-state.json || true
diff_one ip-addr.txt || true
diff_one ip-route.txt || true
diff_one ss-summary.txt || true
diff_one ps-sshfs.txt || true

echo "Done."
