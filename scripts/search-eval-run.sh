#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run a repeatable search quality evaluation: capture v1 + v2, then diff.

This wraps:
  - ./scripts/search-eval-capture.sh
  - ./scripts/search-eval-diff.py

Usage:
  ./scripts/search-eval-run.sh [--base-url URL] [--out-dir DIR] [--run-id ID] [--page-size N]
                              [--top N] [--show N] [--generate-from-db ...]

Examples:
  # Local API (default base URL):
  ./scripts/search-eval-run.sh

  # Production API (store artifacts under ops dir on the VPS):
  ./scripts/search-eval-run.sh --base-url https://api.healtharchive.ca --out-dir /srv/healtharchive/ops/search-eval

Notes:
  - Writes two capture directories: <run-id>-v1 and <run-id>-v2
  - Writes a diff report: <run-id>.diff.txt
EOF
}

BASE_URL="http://127.0.0.1:8001"
OUT_DIR=""
RUN_ID=""
PAGE_SIZE="20"
TOP="20"
SHOW="10"

GENERATE_FROM_DB="false"
CORPUS_MAX_TITLES=""
CORPUS_TOP_UNIGRAMS=""
CORPUS_TOP_BIGRAMS=""
CORPUS_MIN_COUNT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    --page-size)
      PAGE_SIZE="$2"
      shift 2
      ;;
    --top)
      TOP="$2"
      shift 2
      ;;
    --show)
      SHOW="$2"
      shift 2
      ;;
    --generate-from-db)
      GENERATE_FROM_DB="true"
      shift 1
      ;;
    --corpus-max-titles)
      CORPUS_MAX_TITLES="$2"
      shift 2
      ;;
    --corpus-top-unigrams)
      CORPUS_TOP_UNIGRAMS="$2"
      shift 2
      ;;
    --corpus-top-bigrams)
      CORPUS_TOP_BIGRAMS="$2"
      shift 2
      ;;
    --corpus-min-count)
      CORPUS_MIN_COUNT="$2"
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

if [[ -z "${OUT_DIR}" ]]; then
  if [[ -d "/srv/healtharchive/ops/search-eval" && -w "/srv/healtharchive/ops/search-eval" ]]; then
    OUT_DIR="/srv/healtharchive/ops/search-eval"
  else
    OUT_DIR="/tmp/ha-search-eval"
  fi
fi

if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi

capture_args=(
  --base-url "${BASE_URL}"
  --out-dir "${OUT_DIR}"
  --page-size "${PAGE_SIZE}"
)
if [[ "${GENERATE_FROM_DB}" == "true" ]]; then
  capture_args+=(--generate-from-db)
fi
if [[ -n "${CORPUS_MAX_TITLES}" ]]; then
  capture_args+=(--corpus-max-titles "${CORPUS_MAX_TITLES}")
fi
if [[ -n "${CORPUS_TOP_UNIGRAMS}" ]]; then
  capture_args+=(--corpus-top-unigrams "${CORPUS_TOP_UNIGRAMS}")
fi
if [[ -n "${CORPUS_TOP_BIGRAMS}" ]]; then
  capture_args+=(--corpus-top-bigrams "${CORPUS_TOP_BIGRAMS}")
fi
if [[ -n "${CORPUS_MIN_COUNT}" ]]; then
  capture_args+=(--corpus-min-count "${CORPUS_MIN_COUNT}")
fi

run_id_v1="${RUN_ID}-v1"
run_id_v2="${RUN_ID}-v2"

echo "HealthArchive search eval run"
echo "-----------------------------"
echo "Base URL:   ${BASE_URL}"
echo "Out dir:    ${OUT_DIR}"
echo "Run ID:     ${RUN_ID}"
echo "Page size:  ${PAGE_SIZE}"
echo "Top:        ${TOP}"
echo "Show:       ${SHOW}"
echo ""

"${SCRIPT_DIR}/search-eval-capture.sh" "${capture_args[@]}" --run-id "${run_id_v1}" --ranking v1
"${SCRIPT_DIR}/search-eval-capture.sh" "${capture_args[@]}" --run-id "${run_id_v2}" --ranking v2

dir_a="${OUT_DIR%/}/${run_id_v1}"
dir_b="${OUT_DIR%/}/${run_id_v2}"
report="${OUT_DIR%/}/${RUN_ID}.diff.txt"

python3 "${SCRIPT_DIR}/search-eval-diff.py" --a "${dir_a}" --b "${dir_b}" --top "${TOP}" --show "${SHOW}" | tee "${report}"

echo ""
echo "Wrote: ${report}"
