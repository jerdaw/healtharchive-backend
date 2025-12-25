#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS helper: install a loopback-only "admin proxy" for browser-friendly ops triage.

Phase: "9 — Admin/ops UI decision gate (build only if needed)"

This is intentionally not a bespoke UI app. It is a tiny reverse proxy that:
- Listens on 127.0.0.1 (default: :8002)
- Proxies read-only GET requests to:
    /api/admin/** and /metrics
  on the local backend (default upstream: http://127.0.0.1:8001)
- Adds the backend admin token server-side, so your browser does not need headers.

Access model:
- Keep the proxy loopback-only on the VPS.
- Access it from your laptop via SSH port-forwarding (tailnet-only SSH).

Safe-by-default: dry-run unless you pass --apply.

Prereqs:
- The backend is running locally (systemd) on 127.0.0.1:8001.
- The backend admin token is available in a single-line file. Default:
    /etc/healtharchive/observability/prometheus_backend_admin_token

Usage (on the VPS):
  cd /opt/healtharchive-backend

  # Dry-run:
  ./scripts/vps-install-ops-admin-proxy.sh

  # Apply:
  sudo ./scripts/vps-install-ops-admin-proxy.sh --apply

Then (from your laptop):
  ssh -N -L 8002:127.0.0.1:8002 haadmin@<vps-tailscale-ip>
  # Open:
  http://127.0.0.1:8002/

Options:
  --apply                 Actually perform changes (default: dry-run)
  --listen HOST:PORT      Bind address (default: 127.0.0.1:8002)
  --upstream URL          Backend upstream base (default: http://127.0.0.1:8001)
  --token-file PATH       Admin token file (default: /etc/healtharchive/observability/prometheus_backend_admin_token)
  --ops-group NAME        Shared ops group (default: healtharchive)
  --no-enable             Do not enable/start the service (still writes files)

Verify on the VPS:
  curl -s http://127.0.0.1:8002/-/health
  curl -s http://127.0.0.1:8002/api/admin/jobs?limit=1 | head
  curl -s http://127.0.0.1:8002/metrics | head
EOF
}

APPLY="false"
LISTEN="127.0.0.1:8002"
UPSTREAM="http://127.0.0.1:8001"
TOKEN_FILE="/etc/healtharchive/observability/prometheus_backend_admin_token"
OPS_GROUP="healtharchive"
ENABLE_SERVICE="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY="true"
      shift 1
      ;;
    --listen)
      LISTEN="$2"
      shift 2
      ;;
    --upstream)
      UPSTREAM="$2"
      shift 2
      ;;
    --token-file)
      TOKEN_FILE="$2"
      shift 2
      ;;
    --ops-group|--group)
      OPS_GROUP="$2"
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

if [[ ! -f "${TOKEN_FILE}" ]]; then
  echo "ERROR: Missing token file: ${TOKEN_FILE}" >&2
  exit 1
fi

if [[ "${APPLY}" == "true" ]]; then
  run apt-get update
  run apt-get install -y python3 ca-certificates
fi

svc_user="healtharchive-admin-proxy"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ (ensure system user exists: ${svc_user} in group ${OPS_GROUP})"
else
  if ! id "${svc_user}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin -g "${OPS_GROUP}" "${svc_user}"
  fi
fi

bin_path="/usr/local/bin/healtharchive-admin-proxy"
if [[ "${APPLY}" != "true" ]]; then
  echo "+ install -m 0755 -o root -g root ${bin_path}"
else
  install -m 0755 -o root -g root /dev/null "${bin_path}"
  cat >"${bin_path}" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


def _read_token(path: str) -> str:
    raw = ""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    token = raw.strip().replace("\r", "").replace("\n", "")
    if not token:
        raise RuntimeError(f"Admin token file is empty: {path}")
    return token


def _is_allowed_path(path: str) -> bool:
    if path == "/metrics":
        return True
    if path.startswith("/api/admin"):
        return True
    if path in ("/", "/-/health"):
        return True
    return False


def _index_html() -> bytes:
    body = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>HealthArchive Ops Proxy</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; max-width: 900px; }
      code { background: #f2f2f2; padding: 0.15rem 0.25rem; border-radius: 0.25rem; }
      a { color: #0b5fff; text-decoration: none; }
      a:hover { text-decoration: underline; }
      ul { line-height: 1.7; }
    </style>
  </head>
  <body>
    <h1>HealthArchive Ops Proxy</h1>
    <p>This is a loopback-only reverse proxy that adds the admin token server-side.</p>
    <h2>Useful links</h2>
    <ul>
      <li><a href="/metrics">/metrics</a></li>
      <li><a href="/api/admin/jobs">/api/admin/jobs</a></li>
      <li><a href="/api/admin/jobs/status-counts">/api/admin/jobs/status-counts</a></li>
      <li><a href="/api/admin/reports">/api/admin/reports</a></li>
      <li><a href="/api/admin/search-debug?q=covid">/api/admin/search-debug?q=covid</a></li>
    </ul>
    <p>If you see a JSON response, it’s working.</p>
  </body>
</html>
"""
    return body.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if not _is_allowed_path(self.path):
            self.send_response(404)
            self.end_headers()
            return

        if self.path in ("/-/health", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return

        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_index_html())
            return

        upstream = os.environ.get("UPSTREAM_BASE", "http://127.0.0.1:8001").rstrip("/")
        token_file = os.environ.get("ADMIN_TOKEN_FILE", "/etc/healtharchive/observability/prometheus_backend_admin_token")
        try:
            token = _read_token(token_file)
        except Exception:
            self.send_response(500)
            self.end_headers()
            return

        # Preserve query string.
        url = upstream + self.path
        req = urllib.request.Request(url, method="GET")
        req.add_header("X-Admin-Token", token)
        req.add_header("Accept", "*/*")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                self.send_response(resp.status)
                ctype = resp.headers.get("Content-Type")
                if ctype:
                    self.send_header("Content-Type", ctype)
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            self.send_response(getattr(e, "code", 502))
            ctype = e.headers.get("Content-Type") if e.headers else None
            if ctype:
                self.send_header("Content-Type", ctype)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except Exception:
            self.send_response(502)
            self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Avoid noisy logs and never include secrets.
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))


def main() -> int:
    listen = os.environ.get("LISTEN_ADDR", "127.0.0.1:8002")
    host, port_s = listen.rsplit(":", 1)
    port = int(port_s)
    srv = HTTPServer((host, port), Handler)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
fi

unit="healtharchive-admin-proxy.service"
unit_path="/etc/systemd/system/${unit}"

unit_body="[Unit]
Description=HealthArchive admin/metrics proxy (loopback-only; adds admin token server-side)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${svc_user}
Group=${OPS_GROUP}
Environment=LISTEN_ADDR=${LISTEN}
Environment=UPSTREAM_BASE=${UPSTREAM}
Environment=ADMIN_TOKEN_FILE=${TOKEN_FILE}
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

listen_host="${LISTEN%:*}"
listen_port="${LISTEN##*:}"

echo "OK: admin proxy installed."
echo
echo "Verify locally on the VPS:"
echo "  curl -s http://${listen_host}:${listen_port}/-/health"
echo "  curl -s http://${listen_host}:${listen_port}/api/admin/jobs?limit=1 | head"
echo "  curl -s http://${listen_host}:${listen_port}/metrics | head"
echo
echo "Access from your laptop via SSH port-forward:"
echo "  ssh -N -L ${listen_port}:127.0.0.1:${listen_port} haadmin@<vps-tailscale-ip>"
echo "  # then open: http://127.0.0.1:${listen_port}/"

