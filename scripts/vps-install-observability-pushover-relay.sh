#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install a loopback-only Pushover relay for Alertmanager.

Purpose:
- Alertmanager can only send generic webhooks.
- Pushover expects a specific POST payload (token/user/message).
- This relay accepts Alertmanager webhooks on 127.0.0.1 and forwards a concise alert
  message to Pushover using secrets stored under /etc/healtharchive/observability/.

Safe-by-default: dry-run unless you pass --apply.

Prereqs:
- You already created:
    /etc/healtharchive/observability/pushover_app_token
    /etc/healtharchive/observability/pushover_user_key
  with mode 0640 and group 'healtharchive' (or your ops group).

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-install-observability-pushover-relay.sh

  # Apply:
  sudo ./scripts/vps-install-observability-pushover-relay.sh --apply

Then set Alertmanager’s receiver URL to the local relay:
  sudoedit /etc/healtharchive/observability/alertmanager_webhook_url
  # Put this single line:
  http://127.0.0.1:9911/alertmanager

Options:
  --apply                 Actually perform changes (default: dry-run)
  --etc-dir DIR           Base /etc dir for healtharchive (default: /etc/healtharchive)
  --ops-group NAME        Shared ops group (default: healtharchive)
  --listen HOST:PORT      Relay bind address (default: 127.0.0.1:9911)
  --no-enable             Do not enable/start the service (still writes files)
EOF
}

APPLY="false"
ETC_DIR="/etc/healtharchive"
OPS_GROUP="healtharchive"
LISTEN="127.0.0.1:9911"
ENABLE_SERVICE="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --etc-dir)
      ETC_DIR="$2"
      shift 2
      ;;
    --ops-group|--group)
      OPS_GROUP="$2"
      shift 2
      ;;
    --listen)
      LISTEN="$2"
      shift 2
      ;;
    --no-enable)
      ENABLE_SERVICE="false"
      shift 1
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

if ! getent group "${OPS_GROUP}" >/dev/null 2>&1; then
  echo "ERROR: Group does not exist: ${OPS_GROUP}" >&2
  exit 1
fi

obs_secrets_dir="${ETC_DIR%/}/observability"
app_token_file="${obs_secrets_dir}/pushover_app_token"
user_key_file="${obs_secrets_dir}/pushover_user_key"

for f in "${app_token_file}" "${user_key_file}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: Missing secret file: ${f}" >&2
    exit 1
  fi
done

if [[ "${APPLY}" == "true" ]]; then
  run apt-get update
  run apt-get install -y python3 ca-certificates curl
fi

svc_user="healtharchive-alert"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ (ensure system user exists: ${svc_user} in group ${OPS_GROUP})"
else
  if ! id "${svc_user}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin -g "${OPS_GROUP}" "${svc_user}"
  fi
fi

bin_path="/usr/local/bin/healtharchive-pushover-relay"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ install -m 0755 -o root -g root ${bin_path}"
else
  install -m 0755 -o root -g root /dev/null "${bin_path}"
  cat >"${bin_path}" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer


def _read_secret(path: str) -> str:
    raw = ""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    value = raw.strip().replace("\r", "").replace("\n", "")
    if not value:
        raise RuntimeError(f"Secret is empty: {path}")
    return value


def _fmt_ts(value: str | None) -> str:
    if not value:
        return ""
    try:
        # Alertmanager uses RFC3339.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except Exception:
        return value


def _build_message(payload: dict) -> tuple[str, str]:
    alerts = payload.get("alerts") or []
    if not alerts:
        return ("HealthArchive alert", "Received webhook with no alerts.")

    a0 = alerts[0] or {}
    labels = a0.get("labels") or {}
    ann = a0.get("annotations") or {}

    alertname = labels.get("alertname", "Alert")
    severity = labels.get("severity", "unknown")
    status = a0.get("status", "unknown")
    instance = labels.get("instance") or labels.get("job") or ""
    starts_at = _fmt_ts(a0.get("startsAt"))

    title = f"HA {severity.upper()}: {alertname}"

    summary = ann.get("summary") or ""
    description = ann.get("description") or ""
    runbook = ann.get("runbook_url") or ""

    lines: list[str] = []
    if summary:
        lines.append(summary)
    if description and description != summary:
        lines.append(description)
    meta = " | ".join([p for p in [status, instance, starts_at] if p])
    if meta:
        lines.append(meta)
    if runbook:
        lines.append(runbook)

    if len(alerts) > 1:
        lines.append(f"(+{len(alerts) - 1} more alert(s) in this batch)")

    body = "\n".join(lines).strip() or "Alert received."
    return (title, body)


def _send_pushover(*, token: str, user: str, title: str, message: str) -> None:
    url = "https://api.pushover.net/1/messages.json"
    data = urllib.parse.urlencode(
        {
            "token": token,
            "user": user,
            "title": title,
            "message": message,
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        # Drain response (avoid leaving sockets open); ignore body content.
        resp.read()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/-/health", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/alertmanager":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        try:
            token = _read_secret(os.environ["PUSHOVER_APP_TOKEN_FILE"])
            user = _read_secret(os.environ["PUSHOVER_USER_KEY_FILE"])
            title, message = _build_message(payload)
            _send_pushover(token=token, user=user, title=title, message=message)
        except Exception:
            self.send_response(500)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, fmt: str, *args) -> None:
        # Keep logs minimal and avoid accidental secret leakage.
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))


def main() -> int:
    listen = os.environ.get("LISTEN_ADDR", "127.0.0.1:9911")
    host, port_s = listen.rsplit(":", 1)
    port = int(port_s)
    srv = HTTPServer((host, port), Handler)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
fi

unit="healtharchive-pushover-relay.service"
unit_path="/etc/systemd/system/${unit}"
listen_host="${LISTEN%:*}"
listen_port="${LISTEN##*:}"

unit_body="[Unit]
Description=HealthArchive Alertmanager -> Pushover relay (loopback-only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${svc_user}
Group=${OPS_GROUP}
Environment=LISTEN_ADDR=${LISTEN}
Environment=PUSHOVER_APP_TOKEN_FILE=${app_token_file}
Environment=PUSHOVER_USER_KEY_FILE=${user_key_file}
ExecStart=${bin_path}
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictSUIDSGID=true
RestrictNamespaces=true
RestrictRealtime=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallFilter=@system-service

[Install]
WantedBy=multi-user.target
"

if [[ "${APPLY}" != "true" ]]; then
  echo "+ cat > ${unit_path} <<'EOF'"
  echo "${unit_body}"
  echo "+ EOF"
else
  cat >"${unit_path}" <<EOF
${unit_body}
EOF
  chown root:root "${unit_path}"
  chmod 0644 "${unit_path}"
fi

run systemctl daemon-reload
if [[ "${ENABLE_SERVICE}" == "true" ]]; then
  run systemctl enable "${unit}"
  run systemctl restart "${unit}"
fi

echo "OK: pushover relay installed."
echo
echo "Verify locally on the VPS:"
echo "  curl -s http://${listen_host}:${listen_port}/-/health"
echo "  ss -lntp | grep -E ':${listen_port}\\\\b'"
echo
echo "Next: point Alertmanager at the relay:"
echo "  sudoedit ${obs_secrets_dir}/alertmanager_webhook_url"
echo "  # set to: http://${listen_host}:${listen_port}/alertmanager"

