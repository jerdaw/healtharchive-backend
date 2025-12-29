#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: apply persistent WARC tiering bind mounts.

This script reads a bind-mount manifest and ensures canonical "hot" paths under:
  /srv/healtharchive/jobs/**
are bind-mounted to "cold" paths under:
  /srv/healtharchive/storagebox/jobs/**

This keeps DB snapshot WARC paths stable while storing bytes on the Storage Box.

Safe-by-default: dry-run unless you pass --apply.

Manifest format (default: /etc/healtharchive/warc-tiering.binds):
  <cold_path> <hot_path>

Lines beginning with # are ignored.

Usage (on the VPS):
  cd /opt/healtharchive-backend
  ./scripts/vps-warc-tiering-bind-mounts.sh

  # Apply (requires sudo):
  sudo ./scripts/vps-warc-tiering-bind-mounts.sh --apply

Options:
  --apply             Actually mount (otherwise print planned actions)
  --manifest FILE     Manifest path (default: /etc/healtharchive/warc-tiering.binds)
  --storagebox-mount DIR Storage Box mountpoint (default: /srv/healtharchive/storagebox)
  -h, --help          Show this help
EOF
}

APPLY="false"
MANIFEST="/etc/healtharchive/warc-tiering.binds"
STORAGEBOX_MOUNT="/srv/healtharchive/storagebox"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --manifest)
      MANIFEST="$2"
      shift 2
      ;;
    --storagebox-mount)
      STORAGEBOX_MOUNT="$2"
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

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

is_mounted() {
  local path="$1"
  if have_cmd mountpoint; then
    mountpoint -q "${path}"
    return $?
  fi
  mount | grep -q " on ${path} " 2>/dev/null
}

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

if [[ ! -f "${MANIFEST}" ]]; then
  echo "ERROR: manifest not found: ${MANIFEST}" >&2
  echo "Hint: create it with lines: <cold_path> <hot_path> (see playbook: docs/operations/playbooks/warc-storage-tiering.md)" >&2
  exit 1
fi

if ! is_mounted "${STORAGEBOX_MOUNT}"; then
  echo "ERROR: Storage Box mount is not active: ${STORAGEBOX_MOUNT}" >&2
  echo "Hint: start healtharchive-storagebox-sshfs.service (or mount manually) before applying bind mounts." >&2
  exit 1
fi

echo "HealthArchive WARC tiering bind mounts"
echo "-------------------------------------"
echo "manifest=${MANIFEST}"
echo "storagebox_mount=${STORAGEBOX_MOUNT}"
echo "apply=${APPLY}"
echo ""

bound=0
planned=0

while IFS= read -r line || [[ -n "${line}" ]]; do
  # Trim leading/trailing whitespace (best-effort).
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "${line}" ]] && continue
  [[ "${line}" == \#* ]] && continue

  cold="$(echo "${line}" | awk '{print $1}')"
  hot="$(echo "${line}" | awk '{print $2}')"
  if [[ -z "${cold}" || -z "${hot}" ]]; then
    echo "ERROR: malformed line in manifest (expected: <cold> <hot>): ${line}" >&2
    exit 1
  fi

  planned=$((planned + 1))

  if [[ ! -e "${cold}" ]]; then
    echo "ERROR: cold path does not exist: ${cold}" >&2
    exit 1
  fi

  if [[ -d "${hot}" ]] && is_mounted "${hot}"; then
    echo "OK   mounted: ${hot}"
    continue
  fi

  echo "MOUNT ${hot} <= ${cold}"
  run install -d -m 0755 "${hot}"
  run mount --bind "${cold}" "${hot}"
  bound=$((bound + 1))
done <"${MANIFEST}"

echo ""
echo "planned=${planned}"
echo "mounted_now=${bound}"
echo "OK: bind mounts validated."
