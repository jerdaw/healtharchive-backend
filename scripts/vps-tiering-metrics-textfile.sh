#!/usr/bin/env bash
set -euo pipefail

# Emit a small set of HealthArchive tiering health metrics via the node_exporter
# textfile collector. Intended to be run via systemd timer as root.

OUT_DIR="/var/lib/node_exporter/textfile_collector"
OUT_FILE="${OUT_DIR}/healtharchive_tiering.prom"

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

unit_ok() {
  local unit="$1"
  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    echo 0
    return 0
  fi
  local active failed
  active="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  failed="$(systemctl is-failed "${unit}" 2>/dev/null || true)"
  if [[ "${active}" == "active" && "${failed}" != "failed" ]]; then
    echo 1
    return 0
  fi
  echo 0
}

unit_failed() {
  local unit="$1"
  if ! systemctl cat "${unit}" >/dev/null 2>&1; then
    echo 0
    return 0
  fi
  local failed
  failed="$(systemctl is-failed "${unit}" 2>/dev/null || true)"
  [[ "${failed}" == "failed" ]] && echo 1 || echo 0
}

storagebox_ok=0
if is_mounted "/srv/healtharchive/storagebox"; then
  # Ensure the mount is readable (catches some auth/remote-path issues).
  if ls -la "/srv/healtharchive/storagebox" >/dev/null 2>&1; then
    storagebox_ok=1
  fi
fi

storagebox_service_ok="$(unit_ok healtharchive-storagebox-sshfs.service)"
tiering_service_ok="$(unit_ok healtharchive-warc-tiering.service)"
tiering_service_failed="$(unit_failed healtharchive-warc-tiering.service)"

mkdir -p "${OUT_DIR}"
tmp="$(mktemp "${OUT_FILE}.XXXXXX")"
cat >"${tmp}" <<EOF
# HELP healtharchive_storagebox_mount_ok 1 if Storage Box mount is present and readable.
# TYPE healtharchive_storagebox_mount_ok gauge
healtharchive_storagebox_mount_ok ${storagebox_ok}

# HELP healtharchive_systemd_unit_ok 1 if the unit exists and is not failed (and active when applicable).
# TYPE healtharchive_systemd_unit_ok gauge
healtharchive_systemd_unit_ok{unit="healtharchive-storagebox-sshfs.service"} ${storagebox_service_ok}
healtharchive_systemd_unit_ok{unit="healtharchive-warc-tiering.service"} ${tiering_service_ok}

# HELP healtharchive_systemd_unit_failed 1 if systemd reports the unit is failed.
# TYPE healtharchive_systemd_unit_failed gauge
healtharchive_systemd_unit_failed{unit="healtharchive-warc-tiering.service"} ${tiering_service_failed}

# HELP healtharchive_tiering_metrics_timestamp_seconds UNIX timestamp when these metrics were generated.
# TYPE healtharchive_tiering_metrics_timestamp_seconds gauge
healtharchive_tiering_metrics_timestamp_seconds $(date +%s)
EOF

chmod 0644 "${tmp}"
mv "${tmp}" "${OUT_FILE}"
