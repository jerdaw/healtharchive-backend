# Compare-live (snapshot vs live) - implementation plan

Status: implemented (2025-12-25).

## 1) Goal

Provide a public compare-to-live workflow that diffs an archived snapshot against the current live page, without caching or persisting live content.

## 2) Decisions (locked)

- Public endpoint (no admin token gating).
- Always fresh fetch (no caching of live results).
- Descriptive-only copy; no interpretation or medical advice.
- Safety controls: timeouts, byte limits, redirect limits, SSRF blocking, and per-process concurrency caps.

## 3) Implementation summary

### 3.1 Backend

- New endpoint: `GET /api/snapshots/{snapshot_id}/compare-live`.
- Uses existing diffing pipeline (`normalize_html_for_diff` + `compute_diff`).
- Computes section/line stats and high-noise flag using the same heuristic as change tracking.
- Response headers set `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow`.
- Usage event: `compare_live_view` recorded into aggregate metrics.

### 3.2 Frontend

- New route: `/compare-live?to=<snapshotId>`.
- Two-step UX: initial landing + explicit "Fetch live diff" button (`run=1`) to prevent prefetch-triggered fetches.
- Entry points added on snapshot page and archived compare page (prefetch disabled).
- Compare-live copy warns that the live page is not archived and should not be cited.

### 3.3 Documentation

- Citation guidance updated to clarify that compare-live is not an archival record.
- Deployment config docs include compare-live env toggles.

## 4) Config (env)

- `HEALTHARCHIVE_COMPARE_LIVE_ENABLED` (default `1`)
- `HEALTHARCHIVE_COMPARE_LIVE_TIMEOUT_SECONDS` (default `8`)
- `HEALTHARCHIVE_COMPARE_LIVE_MAX_REDIRECTS` (default `4`)
- `HEALTHARCHIVE_COMPARE_LIVE_MAX_BYTES` (default `2000000`)
- `HEALTHARCHIVE_COMPARE_LIVE_MAX_ARCHIVE_BYTES` (default `2000000`)
- `HEALTHARCHIVE_COMPARE_LIVE_MAX_CONCURRENCY` (default `4`)
- `HEALTHARCHIVE_COMPARE_LIVE_USER_AGENT` (default identifies HealthArchive)

## 5) Safety guardrails

- Only `http`/`https` URLs allowed.
- Blocks private, loopback, link-local, and reserved IP ranges.
- Disallows non-80/443 ports and embedded credentials.
- Redirects are capped and re-validated on every hop.
- Response body size is capped to avoid memory pressure.

## 6) Testing

- Manual test from a known HTML snapshot:
  - `/compare-live?to=<id>` then "Fetch live diff".
  - Verify headers (`Cache-Control: no-store`), diff output, and live metadata.
- Non-HTML snapshot should return 422 with a clear error message.
