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
  --repair-stale-mounts If a mountpoint is stale (Errno 107), attempt a targeted unmount and retry
  --manifest FILE     Manifest path (default: /etc/healtharchive/warc-tiering.binds)
  --storagebox-mount DIR Storage Box mountpoint (default: /srv/healtharchive/storagebox)
  -h, --help          Show this help
EOF
}

APPLY="false"
REPAIR_STALE_MOUNTS="false"
MANIFEST="/etc/healtharchive/warc-tiering.binds"
STORAGEBOX_MOUNT="/srv/healtharchive/storagebox"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --repair-stale-mounts)
      REPAIR_STALE_MOUNTS="true"
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

is_transport_endpoint_error() {
  echo "$1" | grep -qi "Transport endpoint is not connected"
}

is_permission_denied_error() {
  echo "$1" | grep -qi "permission denied"
}

probe_readable_dir() {
  local path="$1"
  local err=""
  if ls -la "${path}" >/dev/null 2>&1; then
    return 0
  fi
  err="$(ls -la "${path}" 2>&1 || true)"
  if is_transport_endpoint_error "${err}"; then
    echo "ERROR: path is mounted but unreadable (stale mountpoint; Errno 107): ${path}" >&2
    echo "Hint: follow docs/operations/playbooks/storagebox-sshfs-stale-mount-recovery.md" >&2
    echo "      or run: sudo umount -l ${path}" >&2
    return 107
  fi
  if is_permission_denied_error "${err}"; then
    echo "ERROR: cannot read path (permission denied): ${path}" >&2
    echo "Hint: run with sudo for accurate checks and apply mode." >&2
    echo "  ${err}" >&2
    return 13
  fi
  echo "ERROR: path is not readable: ${path}" >&2
  echo "  ${err}" >&2
  return 1
}

try_unmount_stale_mountpoint() {
  local path="$1"
  if [[ "${REPAIR_STALE_MOUNTS}" != "true" ]]; then
    echo "ERROR: stale mountpoint detected (Errno 107): ${path}" >&2
    echo "Hint: run: sudo umount -l ${path}" >&2
    echo "      then re-run this script." >&2
    exit 1
  fi

  echo "REPAIR stale mountpoint: ${path}"
  if [[ "${APPLY}" != "true" ]]; then
    echo "+ umount ${path}"
    echo "+ umount -l ${path}"
    return 0
  fi

  if umount "${path}" 2>/dev/null; then
    return 0
  fi
  if umount -l "${path}" 2>/dev/null; then
    return 0
  fi
  echo "ERROR: failed to unmount stale mountpoint: ${path}" >&2
  echo "Hint: check if it is still mounted: mount | grep \" on ${path} \"" >&2
  exit 1
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
if ! probe_readable_dir "${STORAGEBOX_MOUNT}"; then
  echo "ERROR: Storage Box mount is not readable: ${STORAGEBOX_MOUNT}" >&2
  echo "Hint: try: sudo systemctl restart healtharchive-storagebox-sshfs.service" >&2
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
    err="$(ls -ld "${cold}" 2>&1 || true)"
    if is_transport_endpoint_error "${err}"; then
      echo "ERROR: cold path is unreadable (stale mountpoint; Errno 107): ${cold}" >&2
      echo "Hint: Storage Box mount or the cold path is stale; restart the mount service:" >&2
      echo "      sudo systemctl restart healtharchive-storagebox-sshfs.service" >&2
      echo "Then re-run this script." >&2
    elif is_permission_denied_error "${err}"; then
      echo "ERROR: cannot access cold path (permission denied): ${cold}" >&2
      echo "  ${err}" >&2
    else
      echo "ERROR: cold path does not exist: ${cold}" >&2
      echo "  ${err}" >&2
    fi
    exit 1
  fi

  if is_mounted "${hot}"; then
    if probe_readable_dir "${hot}"; then
      echo "OK   mounted: ${hot}"
      continue
    fi
    probe_rc=$?
    if [[ "${probe_rc}" -eq 107 ]]; then
      # Stale mountpoint: repair (if requested) then proceed to re-mount.
      try_unmount_stale_mountpoint "${hot}"
    else
      exit 1
    fi
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
