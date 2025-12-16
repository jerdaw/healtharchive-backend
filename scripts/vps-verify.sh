#!/usr/bin/env bash
# HealthArchive VPS verification (safe, read-only).
# - Does NOT read /etc/healtharchive/backend.env
# - Does NOT print environment variables or tokens
# - Does NOT modify services, files, or indexes

set -u
set -o pipefail

usage() {
  cat <<'EOF'
HealthArchive VPS verification (safe, read-only).

Run from the VPS in /opt/healtharchive-backend:
  bash ./scripts/vps-verify.sh

This script prints labeled sections so you can paste the output back into chat.
It avoids secrets by design (no env file reads; no /proc/*/environ; no tokens).

Options:
  --api-local URL        Default: http://127.0.0.1:8001
  --api-public URL       Default: https://api.healtharchive.ca
  --replay-local URL     Default: http://127.0.0.1:8090
  --replay-public URL    Default: https://replay.healtharchive.ca
  --frontend-public URL  Default: https://www.healtharchive.ca
  --max-sources N        Default: 8 (limit printed sources)
  -h, --help
EOF
}

API_LOCAL="http://127.0.0.1:8001"
API_PUBLIC="https://api.healtharchive.ca"
REPLAY_LOCAL="http://127.0.0.1:8090"
REPLAY_PUBLIC="https://replay.healtharchive.ca"
FRONTEND_PUBLIC="https://www.healtharchive.ca"
MAX_SOURCES="8"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-local)
      API_LOCAL="$2"
      shift 2
      ;;
    --api-public)
      API_PUBLIC="$2"
      shift 2
      ;;
    --replay-local)
      REPLAY_LOCAL="$2"
      shift 2
      ;;
    --replay-public)
      REPLAY_PUBLIC="$2"
      shift 2
      ;;
    --frontend-public)
      FRONTEND_PUBLIC="$2"
      shift 2
      ;;
    --max-sources)
      MAX_SOURCES="$2"
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
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

PYTHON_BIN="python3"
if [[ -x "${REPO_DIR}/.venv/bin/python3" ]]; then
  PYTHON_BIN="${REPO_DIR}/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

section() {
  echo
  echo "================================================================"
  echo "## $1"
  echo "================================================================"
}

run() {
  echo
  echo "$ $*"
  "$@" 2>&1
  local rc=$?
  echo "[exit=${rc}]"
  return 0
}

run_sh() {
  echo
  echo "$ $1"
  bash -lc "$1" 2>&1
  local rc=$?
  echo "[exit=${rc}]"
  return 0
}

curl_head() {
  local url="$1"
  curl -sS -I --max-time 15 "$url" 2>&1 | sed -n '1,30p'
}

curl_json_summary() {
  local url="$1"
  local label="$2"
  echo
  echo "HINT: ${label}"
  echo "$ curl -sS \"${url}\""
  curl -sS --max-time 20 "$url" \
    | "${PYTHON_BIN}" -c '
import json
import sys

try:
  data = json.load(sys.stdin)
except Exception as e:
  print(f"ERROR: failed to parse JSON: {e}")
  sys.exit(0)

print(f"type={type(data).__name__}")
if isinstance(data, dict):
  keys = sorted(list(data.keys()))
  print(f"keys={keys}")
  # Common health payload
  if "status" in data:
    print(f"status={data.get(\"status\")}")
  if "checks" in data and isinstance(data["checks"], dict):
    checks = data["checks"]
    jobs = checks.get("jobs", {})
    snaps = checks.get("snapshots", {})
    if isinstance(jobs, dict):
      print(f"checks.jobs.indexed={jobs.get(\"indexed\")}")
    if isinstance(snaps, dict):
      print(f"checks.snapshots.total={snaps.get(\"total\")}")
elif isinstance(data, list):
  print(f"len={len(data)}")
'
}

echo "HealthArchive VPS verification (safe, read-only)"
echo "Run UTC: $(date -u '+%Y-%m-%d %H:%M:%S')"
echo "Host:    $(hostname 2>/dev/null || true)"
echo "User:    $(id -un 2>/dev/null || true)"
echo "Repo:    ${REPO_DIR}"
echo "Python:  ${PYTHON_BIN}"

section "1) Repo Version (no secrets)"
run git rev-parse --short HEAD
run git status --porcelain=v1

section "2) Service Status (no logs)"
for svc in healtharchive-api healtharchive-worker healtharchive-replay caddy; do
  status="$(systemctl is-active "${svc}" 2>/dev/null || true)"
  echo "HINT: systemd service '${svc}' status"
  echo "  ${svc}: ${status:-unknown}"
done

section "3) Listening Ports (best-effort)"
run_sh "ss -ltn | grep -E ':(8001|8090)\\b' || true"

section "4) Local API health"
curl_json_summary "${API_LOCAL}/api/health" "Local API health (should be status=ok)"

section "5) Public API health (via Caddy/TLS)"
curl_json_summary "${API_PUBLIC}/api/health" "Public API health (should match local)"

section "6) Sources summary (browseUrl/preview/entry fields)"
echo
echo "HINT: This prints a compact table of sources (first ${MAX_SOURCES})."
echo "$ curl -sS \"${API_LOCAL}/api/sources\" | ${PYTHON_BIN} (summary)"
curl -sS --max-time 25 "${API_LOCAL}/api/sources" \
  | MAX_SOURCES="${MAX_SOURCES}" "${PYTHON_BIN}" -c '
import json
import sys
import os

try:
  data = json.load(sys.stdin)
except Exception as e:
  print(f"ERROR: failed to parse JSON: {e}")
  sys.exit(0)

if not isinstance(data, list):
  print("ERROR: expected a JSON list")
  sys.exit(0)

max_sources = int(os.environ.get("MAX_SOURCES") or "8")

def short_date(s: str | None) -> str:
  if not s:
    return "-"
  return s

print("sourceCode  snapshots  firstCapture  lastCapture  entryBrowseUrl?  entryPreviewUrl?")
print("---------   ---------  -----------   ----------   -------------   --------------")
for i, src in enumerate(data[:max_sources]):
  if not isinstance(src, dict):
    continue
  code = (src.get("sourceCode") or "-")
  count = (src.get("recordCount") or 0)
  first = short_date(src.get("firstCapture"))
  last = short_date(src.get("lastCapture"))
  has_entry = "yes" if (src.get("entryBrowseUrl") or "").strip() else "no"
  has_prev = "yes" if (src.get("entryPreviewUrl") or "").strip() else "no"
  print(f"{code:9}  {count:9}  {first:11}  {last:10}  {has_entry:13}  {has_prev:14}")

print()
with_entry = [s for s in data if isinstance(s, dict) and (s.get("entryBrowseUrl") or "").strip()]
print(f"HINT: sources_with_entryBrowseUrl={len(with_entry)}")
'

section "7) Editions endpoint (first source with entryBrowseUrl)"
SOURCE_WITH_ENTRY_BROWSE_URL="$(
  "${PYTHON_BIN}" - <<PY
import json, sys
import urllib.request

api = "${API_LOCAL}/api/sources"

try:
  with urllib.request.urlopen(api, timeout=20) as r:
    data = json.load(r)
except Exception:
  print("")
  sys.exit(0)

if not isinstance(data, list):
  print("")
  sys.exit(0)

for src in data:
  if not isinstance(src, dict):
    continue
  if (src.get("entryBrowseUrl") or "").strip():
    print(src.get("sourceCode") or "")
    sys.exit(0)
print("")
PY
)"

if [[ -n "${SOURCE_WITH_ENTRY_BROWSE_URL}" ]]; then
  echo "HINT: Testing editions for sourceCode='${SOURCE_WITH_ENTRY_BROWSE_URL}'"
  echo "$ curl -sS \"${API_LOCAL}/api/sources/${SOURCE_WITH_ENTRY_BROWSE_URL}/editions\""
  curl -sS --max-time 25 "${API_LOCAL}/api/sources/${SOURCE_WITH_ENTRY_BROWSE_URL}/editions" \
    | "${PYTHON_BIN}" -c '
import json
import sys

try:
  data = json.load(sys.stdin)
except Exception as e:
  print(f"ERROR: failed to parse JSON: {e}")
  sys.exit(0)

if not isinstance(data, list):
  print("ERROR: expected a JSON list")
  sys.exit(0)

print(f"editions_count={len(data)}")
for ed in data[:5]:
  if not isinstance(ed, dict):
    continue
  job_id = ed.get("jobId")
  name = ed.get("jobName")
  first = ed.get("firstCaptureTimestamp")
  last = ed.get("lastCaptureTimestamp")
  entry = ed.get("entryBrowseUrl")
  print(f"- jobId={job_id} name={name!r}")
  print(f"  first={first} last={last}")
  print(f"  entryBrowseUrl_present={'yes' if isinstance(entry, str) and entry else 'no'}")
'
else
  echo "WARN: No sources with entryBrowseUrl found; editions check skipped."
fi

section "8) Search payload includes browseUrl/jobId (first source with entryBrowseUrl)"
if [[ -n "${SOURCE_WITH_ENTRY_BROWSE_URL}" ]]; then
  echo "HINT: Fetching a single result to confirm browseUrl/jobId fields are populated."
  echo "$ curl -sS \"${API_LOCAL}/api/search?pageSize=1&source=${SOURCE_WITH_ENTRY_BROWSE_URL}\""
  curl -sS --max-time 25 "${API_LOCAL}/api/search?pageSize=1&source=${SOURCE_WITH_ENTRY_BROWSE_URL}" \
    | "${PYTHON_BIN}" -c '
import json
import sys

try:
  payload = json.load(sys.stdin)
except Exception as e:
  print(f"ERROR: failed to parse JSON: {e}")
  sys.exit(0)

results = payload.get("results") if isinstance(payload, dict) else None
if not isinstance(results, list) or not results:
  print("WARN: no search results returned")
  sys.exit(0)

r0 = results[0] if isinstance(results[0], dict) else {}
print(f"first_result.captureTimestamp={r0.get(\"captureTimestamp\")}")
print(f"first_result.jobId={r0.get(\"jobId\")}")
print(f"first_result.browseUrl={r0.get(\"browseUrl\")}")
print(f"first_result.url={r0.get(\"url\")}")
'
else
  echo "WARN: No source available for search test."
fi

section "9) Replay service health + embed headers"
echo "HINT: Local replay root (should be HTTP 200)"
echo "$ curl -I \"${REPLAY_LOCAL}/\" | head"
curl_head "${REPLAY_LOCAL}/"

echo
echo "HINT: Public replay root should allow embedding by HealthArchive frontend."
echo "      Expect: Content-Security-Policy with frame-ancestors healtharchive domains."
echo "      Expect: no X-Frame-Options header."
echo "$ curl -I \"${REPLAY_PUBLIC}/\" | egrep -i 'content-security-policy|x-frame-options' || true"
run_sh "curl -sS -I --max-time 15 \"${REPLAY_PUBLIC}/\" | egrep -i 'content-security-policy|x-frame-options' || true"

section "10) Replay banner presence (custom_banner.html)"
echo "HINT: This checks for the deployed banner file and the banner marker in a live replay page."
echo "NOTE: Banner is expected to be visible when visiting replay directly, hidden in iframes."

if [[ -r "/srv/healtharchive/replay/templates/custom_banner.html" ]]; then
  echo "OK: Found /srv/healtharchive/replay/templates/custom_banner.html (readable)."
else
  echo "WARN: Missing or unreadable /srv/healtharchive/replay/templates/custom_banner.html"
  echo "      (If you deployed it with 0640, run this script as a user in group 'healtharchive'.)"
fi

ENTRY_BROWSE_URL="$("${PYTHON_BIN}" - <<PY
import json, sys, urllib.request
api = "${API_LOCAL}/api/sources"
try:
  with urllib.request.urlopen(api, timeout=20) as r:
    data = json.load(r)
except Exception:
  print("")
  sys.exit(0)
if not isinstance(data, list):
  print("")
  sys.exit(0)
for src in data:
  if not isinstance(src, dict):
    continue
  u = (src.get("entryBrowseUrl") or "").strip()
  if u:
    print(u)
    sys.exit(0)
print("")
PY
)"

if [[ -n "${ENTRY_BROWSE_URL}" ]]; then
  echo "HINT: Testing banner marker on: ${ENTRY_BROWSE_URL}"
  echo "$ curl -sS \"${ENTRY_BROWSE_URL}\" | grep -n 'ha-replay-banner' || true"
  run_sh "curl -sS --max-time 25 \"${ENTRY_BROWSE_URL}\" | grep -n 'ha-replay-banner' || true"
else
  echo "WARN: No entryBrowseUrl available to test banner HTML."
fi

section "11) Preview endpoint (if available)"
PREVIEW_URL="$("${PYTHON_BIN}" - <<PY
import json, sys, urllib.request
api = "${API_LOCAL}/api/sources"
try:
  with urllib.request.urlopen(api, timeout=20) as r:
    data = json.load(r)
except Exception:
  print("")
  sys.exit(0)
if not isinstance(data, list):
  print("")
  sys.exit(0)
for src in data:
  if not isinstance(src, dict):
    continue
  u = (src.get("entryPreviewUrl") or "").strip()
  if u:
    print(u)
    sys.exit(0)
print("")
PY
)"

if [[ -n "${PREVIEW_URL}" ]]; then
  if [[ "${PREVIEW_URL}" == http* ]]; then
    FULL_PREVIEW_URL="${PREVIEW_URL}"
  else
    FULL_PREVIEW_URL="${API_PUBLIC}${PREVIEW_URL}"
  fi

  echo "HINT: Checking first available preview URL headers:"
  echo "      ${FULL_PREVIEW_URL}"
  echo "$ curl -I \"${FULL_PREVIEW_URL}\" | egrep -i 'HTTP/|content-type|cache-control|etag|last-modified' || true"
  run_sh "curl -sS -I --max-time 20 \"${FULL_PREVIEW_URL}\" | egrep -i 'HTTP/|content-type|cache-control|etag|last-modified' || true"
else
  echo "INFO: No entryPreviewUrl found in /api/sources; preview check skipped."
fi

section "12) Frontend spot-checks (public)"
echo "HINT: These are best-effort HTML checks (no auth, no secrets)."
echo "$ curl -sS \"${FRONTEND_PUBLIC}/archive\" | grep -n \"Browse and search archived snapshots\" || true"
run_sh "curl -sS --max-time 20 \"${FRONTEND_PUBLIC}/archive\" | grep -n \"Browse and search archived snapshots\" || true"

echo
echo "DONE. If something looks off, paste this entire output into chat."
