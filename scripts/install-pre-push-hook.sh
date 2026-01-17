#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install (or remove) a local .git/hooks/pre-push hook for this repo.

The hook runs `make check` before `git push` to help keep "green main" true
even when branch protections are relaxed.

Usage:
  ./scripts/install-pre-push-hook.sh [--force] [--uninstall]

Notes:
  - To run the full suite instead: set `HA_PRE_PUSH_FULL=1` (runs `make check-full`).
  - To bypass temporarily: `git push --no-verify` (or set HA_SKIP_PRE_PUSH=1).
EOF
}

FORCE="false"
UNINSTALL="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE="true"
      shift 1
      ;;
    --uninstall)
      UNINSTALL="true"
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

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required." >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${repo_root}" ]]; then
  echo "ERROR: Not inside a git repository." >&2
  exit 1
fi

hooks_dir="$(git rev-parse --git-path hooks)"
hook_path="${hooks_dir}/pre-push"
marker="# healtharchive:pre-push:v1"

if [[ "${UNINSTALL}" == "true" ]]; then
  if [[ -f "${hook_path}" ]] && grep -qF "${marker}" "${hook_path}" 2>/dev/null; then
    rm -f "${hook_path}"
    echo "Removed: ${hook_path}"
    exit 0
  fi
  echo "Nothing to remove: ${hook_path}"
  exit 0
fi

if [[ -f "${hook_path}" ]] && ! grep -qF "${marker}" "${hook_path}" 2>/dev/null; then
  if [[ "${FORCE}" != "true" ]]; then
    echo "ERROR: ${hook_path} already exists and doesn't look like ours." >&2
    echo "Hint: re-run with --force to overwrite, or --uninstall to remove ours." >&2
    exit 2
  fi
fi

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

cat >"${tmp}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
${marker}

if [[ "\${HA_SKIP_PRE_PUSH:-}" == "1" ]]; then
  echo "HA_SKIP_PRE_PUSH=1 set; skipping HealthArchive pre-push checks." >&2
  exit 0
fi

repo_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "\${repo_root}"

if [[ "\${HA_PRE_PUSH_FULL:-}" == "1" ]]; then
  echo "HealthArchive pre-push: running 'make check-full'..." >&2
  make check-full
else
  echo "HealthArchive pre-push: running 'make check'..." >&2
  make check
fi
EOF

chmod +x "${tmp}"
mkdir -p "${hooks_dir}"
install -m 0755 "${tmp}" "${hook_path}"

echo "Installed: ${hook_path}"
echo "Tip: bypass once with: git push --no-verify"
