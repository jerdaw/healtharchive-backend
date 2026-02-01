# Search quality & relevance evaluation

This project intentionally runs on a single VPS with Postgres, and does **not**
introduce a separate search service (Elasticsearch/Meilisearch/etc.) unless and
until we outgrow Postgres FTS + light heuristics.

This document is a lightweight, repeatable way to evaluate whether search
results “feel better” after changes.

## 1) Goals (what “better” means)

For broad, common queries (e.g. `covid`):

- No obvious error/garbage captures in the top results (404/5xx “Not Found” pages,
  missing assets, etc.).
- Hub/overview pages rise near the top (titles that clearly match the query).
- Snippets look human-readable (not “Skip to main content … Search … Menu …”).
- API response time stays reasonable as the dataset grows.

## 2) Golden queries (starter list)

Start with ~10–30 queries and expand over time:

- `covid`
- `covid vaccine`
- `long covid`
- `mask guidance`
- `rapid testing`
- `influenza`
- `mpox`
- `food recall`
- `travel advisory`
- `mental health`

For each query, define:

- 2–5 “expected” page titles/URLs that should appear in the top 10.
- 2–5 “anti-results” you never want in the top 20 (assets, obvious error pages).

Keep the list as a simple note, or add a small markdown checklist in this repo
if you want it versioned.

## 3) How to run a quick evaluation (local or prod)

### 3.1 Use the API directly (fastest)

Local dev (backend running on `http://127.0.0.1:8001`):

```bash
curl -s "http://127.0.0.1:8001/api/search?q=covid&page=1&pageSize=10&sort=relevance" | python3 -m json.tool
```

Production:

```bash
curl -s "https://api.healtharchive.ca/api/search?q=covid&page=1&pageSize=10&sort=relevance" | python3 -m json.tool
```

To de-duplicate repeated captures of the same URL (show latest snapshot per page):

```bash
curl -s "https://api.healtharchive.ca/api/search?q=covid&page=1&pageSize=10&sort=relevance&view=pages" | python3 -m json.tool
```

To include non‑2xx captures for research/debugging:

```bash
curl -s "https://api.healtharchive.ca/api/search?q=covid&page=1&pageSize=10&sort=relevance&includeNon2xx=true" | python3 -m json.tool
```

To inspect *why* a result ranks where it does (admin-only score breakdown):

```bash
curl -s "https://api.healtharchive.ca/api/admin/search-debug?q=covid&view=pages&sort=relevance&ranking=v2&pageSize=10" \
  -H "X-Admin-Token: ${HEALTHARCHIVE_ADMIN_TOKEN}" \
  | python3 -m json.tool
```

### 3.2 Capture “before/after” snapshots (recommended)

For a small set of key queries (e.g. `covid`, `mpox`, `food recall`), capture
page 1 JSON to files so you can compare later:

```bash
mkdir -p /tmp/ha-search-eval
curl -s "https://api.healtharchive.ca/api/search?q=covid&page=1&pageSize=10&sort=relevance" \
  > /tmp/ha-search-eval/covid.after.json
```

Keep these captures out of git unless you explicitly want them committed.

For repeatable, multi-query captures, use the helper scripts in `scripts/`:

```bash
./scripts/search-eval-capture.sh --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval --page-size 20 --ranking v1
./scripts/search-eval-capture.sh --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval --page-size 20 --ranking v2
python ./scripts/search-eval-diff.py --a /tmp/ha-search-eval/<TS_A> --b /tmp/ha-search-eval/<TS_B> --top 20
```

To capture v1 + v2 and generate a diff report in one command:

```bash
./scripts/search-eval-run.sh --base-url https://api.healtharchive.ca --out-dir /tmp/ha-search-eval
```

On the production VPS, prefer a persistent output directory:

```bash
./scripts/search-eval-run.sh --base-url https://api.healtharchive.ca --out-dir /srv/healtharchive/ops/search-eval
```

## 4) Minimal pass/fail checklist for releases

For each key query:

- [ ] Top 10 contains at least a few clear title matches (“hub/overview” pages).
- [ ] No obvious 404/asset/error pages in top 20 (unless `includeNon2xx=true`).
- [ ] Snippets look readable and are not mostly boilerplate.
- [ ] Pagination behaves correctly (`total` stable; out-of-range pages return empty).

## 5) Operational tools

These commands are intended for production maintenance on the VPS (Postgres),
but can also be used in local dev environments.

### 5.1 Backfill Postgres FTS vectors

After deploying a schema update that adds `snapshots.search_vector`, populate it
for existing rows:

```bash
ha-backend backfill-search-vector
```

### 5.1.1 Enable fuzzy search (Postgres only)

Fuzzy matching for misspellings relies on the `pg_trgm` extension and trigram
GIN indexes (see Alembic migration `0007_pg_trgm_fuzzy_search`).

Notes:

- `CREATE EXTENSION` may require elevated DB privileges on some hosts.
- If `pg_trgm` is unavailable, search still works (FTS + substring fallback), but
  the fuzzy similarity fallback is disabled.
- The fuzzy fallback is intentionally conservative for performance: it uses
  pg_trgm *word* similarity (not whole-field similarity) and tuned thresholds to
  avoid huge candidate sets on broad queries.

### 5.2 Refresh snippets/titles from WARCs (in place)

After improving HTML extraction logic, update snapshot metadata without
re-indexing (IDs remain stable):

```bash
ha-backend refresh-snapshot-metadata --job-id <JOB_ID>
```

### 5.2.1 Backfill normalized URL groups (page de-duplication)

If older snapshots are missing `Snapshot.normalized_url_group`, `view=pages`
may show duplicate “pages” for the same URL (especially when query parameters
vary). Backfill the column:

```bash
ha-backend backfill-normalized-url-groups
```

### 5.2.2 Snapshot view: hide same-day content duplicates (UI)

In `view=snapshots`, the API can hide *same-day duplicates* of the exact same
URL when the content is identical (same `content_hash`), which helps reduce
noise from repeated tracker / redirect captures while keeping the underlying
data intact.

- Default: duplicates are hidden.
- To include them: `GET /api/search?...&view=snapshots&includeDuplicates=true`

Future (storage-only, must preserve trustworthiness): if we can *prove* the HTML
payload is identical (and preserve provenance), consider pruning same-day
duplicates from storage to save disk space. Track this work in:

- `../planning/roadmap.md`

### 5.3 Backfill outlinks + authority signals

If you have applied the authority schema (tables `snapshot_outlinks` and
`page_signals`), you can populate link edges for a job by re-reading its WARCs:

```bash
ha-backend backfill-outlinks --job-id <JOB_ID> --update-signals
```

To rebuild all link signals from the full outlink graph (includes `inlink_count`,
`outlink_count`, and `pagerank` when present):

```bash
ha-backend recompute-page-signals
```
