#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Capture /api/search JSON responses for a standard set of "golden queries".

Usage:
  ./scripts/search-eval-capture.sh [--base-url URL] [--out-dir DIR] [--page-size N] [--queries-file FILE] [--ranking (v1|v2)] [--generate-from-db ...]

Examples:
  ./scripts/search-eval-capture.sh
  ./scripts/search-eval-capture.sh --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval
  ./scripts/search-eval-capture.sh --queries-file ./scripts/search-eval-queries.txt
  ./scripts/search-eval-capture.sh --ranking v2
  ./scripts/search-eval-capture.sh --generate-from-db
  ./scripts/search-eval-capture.sh --generate-from-db --corpus-max-titles 100000 --corpus-top-unigrams 60 --corpus-top-bigrams 40

Notes:
  - Writes files to a timestamped subdirectory under --out-dir.
  - Captures both view=pages and view=snapshots for each query.
  - --ranking overrides the backend default (useful when comparing ranking versions).
  - --generate-from-db generates a corpus-derived query list from the configured DB
    (uses HEALTHARCHIVE_DATABASE_URL) and merges it with the curated query list.
EOF
}

BASE_URL="http://127.0.0.1:8001"
OUT_DIR="/tmp/ha-search-eval"
PAGE_SIZE="20"
QUERIES_FILE=""
RANKING=""
GENERATE_CORPUS_QUERIES="false"
CORPUS_MAX_TITLES="50000"
CORPUS_TOP_UNIGRAMS="40"
CORPUS_TOP_BIGRAMS="30"
CORPUS_MIN_COUNT="8"

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
    --page-size)
      PAGE_SIZE="$2"
      shift 2
      ;;
    --queries-file)
      QUERIES_FILE="$2"
      shift 2
      ;;
    --ranking)
      RANKING="$2"
      shift 2
      ;;
    --generate-from-db)
      GENERATE_CORPUS_QUERIES="true"
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

if [[ -n "${RANKING}" && "${RANKING}" != "v1" && "${RANKING}" != "v2" ]]; then
  echo "ERROR: --ranking must be 'v1' or 'v2' (got: ${RANKING})" >&2
  exit 2
fi

urlencode() {
  "${PYTHON_BIN}" - <<'PY' "$1"
import sys
from urllib.parse import quote_plus
print(quote_plus(sys.argv[1]))
PY
}

sanitize_filename() {
  # Keep filenames stable and readable across shells/filesystems.
  local s="$1"
  s="${s// /-}"
  s="$(echo "$s" | tr '[:upper:]' '[:lower:]')"
  s="$(echo "$s" | tr -cd 'a-z0-9._-')"
  if [[ -z "$s" ]]; then
    s="query"
  fi
  echo "$s"
}

read_queries() {
  local default_queries_file
  default_queries_file="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/search-eval-queries.txt"

  if [[ -z "${QUERIES_FILE}" && -f "${default_queries_file}" ]]; then
    QUERIES_FILE="${default_queries_file}"
  fi

  if [[ -n "${QUERIES_FILE}" ]]; then
    if [[ ! -f "${QUERIES_FILE}" ]]; then
      echo "ERROR: queries file not found: ${QUERIES_FILE}" >&2
      exit 2
    fi
    # Ignore blank lines and comments.
    awk 'NF && $1 !~ /^#/' "${QUERIES_FILE}"
    return 0
  fi

  cat <<'EOF'
covid
covid vaccine
long covid
mask guidance
rapid testing
influenza
mpox
food recall
travel advisory
mental health
EOF
}

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
capture_dir="${OUT_DIR%/}/${timestamp}"
mkdir -p "${capture_dir}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="python"
if [[ -z "${VIRTUAL_ENV:-}" && -x "${SCRIPT_DIR}/../.venv/bin/python" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/../.venv/bin/python"
fi

meta_file="${capture_dir}/meta.txt"
{
  echo "timestamp_utc=${timestamp}"
  echo "base_url=${BASE_URL}"
  echo "page_size=${PAGE_SIZE}"
  echo "ranking=${RANKING:-default}"
  echo "generate_from_db=${GENERATE_CORPUS_QUERIES}"
  echo "python_bin=${PYTHON_BIN}"
} > "${meta_file}"

echo "Capturing search eval JSON to: ${capture_dir}"
echo "Base URL: ${BASE_URL}"

if [[ "${GENERATE_CORPUS_QUERIES}" == "true" ]]; then
  generator="${SCRIPT_DIR}/search-eval-generate-queries.py"
  corpus_out="${capture_dir}/queries.corpus.txt"
  merged_out="${capture_dir}/queries.merged.txt"

  if [[ ! -f "${generator}" ]]; then
    echo "ERROR: corpus generator not found: ${generator}" >&2
    exit 2
  fi

  echo "Generating corpus-derived queries from DB → ${corpus_out}"
  "${PYTHON_BIN}" "${generator}" \
    --out "${corpus_out}" \
    --max-titles "${CORPUS_MAX_TITLES}" \
    --top-unigrams "${CORPUS_TOP_UNIGRAMS}" \
    --top-bigrams "${CORPUS_TOP_BIGRAMS}" \
    --min-count "${CORPUS_MIN_COUNT}"

  curated_file="${SCRIPT_DIR}/search-eval-queries.txt"
  if [[ ! -f "${curated_file}" ]]; then
    echo "ERROR: curated queries file not found: ${curated_file}" >&2
    exit 2
  fi

  # Merge curated + corpus-derived, preserving order and de-duplicating.
  awk 'NF && $1 !~ /^#/ { print }' "${curated_file}" "${corpus_out}" \
    | awk '!seen[tolower($0)]++' > "${merged_out}"

  QUERIES_FILE="${merged_out}"
  echo "Merged queries → ${merged_out}"
fi

while IFS= read -r query; do
  q_enc="$(urlencode "${query}")"
  q_name="$(sanitize_filename "${query}")"

  for view in pages snapshots; do
    out="${capture_dir}/${q_name}.${view}.json"
    url="${BASE_URL%/}/api/search?q=${q_enc}&page=1&pageSize=${PAGE_SIZE}&sort=relevance&view=${view}"
    if [[ -n "${RANKING}" ]]; then
      url="${url}&ranking=${RANKING}"
    fi
    echo "GET ${url}"
    curl -fsS "${url}" > "${out}"
  done
done < <(read_queries)

echo "Done."
