# HealthArchive exports — data dictionary (Phase 5)

This document describes the **public research exports** exposed via `/api/exports`.
Exports are **metadata-only** and do not include raw HTML or full diff bodies.

All timestamps are in **UTC** and use ISO-8601 format.

---

## Snapshots export (`/api/exports/snapshots`)

One row per snapshot (captured page).

Fields:

- `snapshot_id` — numeric snapshot ID.
- `source_code` — source code (e.g., `hc`, `phac`).
- `source_name` — human-readable source name.
- `captured_url` — the URL captured at crawl time.
- `normalized_url_group` — canonical grouping key for the page across captures.
- `capture_timestamp_utc` — capture timestamp (UTC, ISO-8601).
- `language` — detected language (if available).
- `status_code` — HTTP status recorded during capture (if available).
- `mime_type` — content type recorded during capture (if available).
- `title` — extracted page title (if available).
- `job_id` — archive job ID (edition anchor, if available).
- `job_name` — archive job name (edition label, if available).
- `snapshot_url` — stable public URL to the snapshot detail page.

Notes:

- `normalized_url_group` is the canonical identifier for a page across multiple
  captures.
- Some fields can be null if a capture did not record them or was non-HTML.

---

## Changes export (`/api/exports/changes`)

One row per **precomputed change event** between two captures of the same page group.

Fields:

- `change_id` — numeric change event ID.
- `source_code` — source code (e.g., `hc`, `phac`).
- `source_name` — human-readable source name.
- `normalized_url_group` — canonical grouping key for the page.
- `from_snapshot_id` — earlier snapshot ID (may be null for first capture).
- `to_snapshot_id` — later snapshot ID.
- `from_capture_timestamp_utc` — earlier capture timestamp (UTC).
- `to_capture_timestamp_utc` — later capture timestamp (UTC).
- `from_job_id` — earlier job/edition ID (if available).
- `to_job_id` — later job/edition ID (if available).
- `change_type` — change classification (`new_page`, `updated`, `removed`, etc.).
- `summary` — descriptive change summary (no interpretation).
- `added_sections` — count of sections added (if available).
- `removed_sections` — count of sections removed (if available).
- `changed_sections` — count of sections changed (if available).
- `added_lines` — count of lines added (if available).
- `removed_lines` — count of lines removed (if available).
- `change_ratio` — proportional change score (if available).
- `high_noise` — true when changes are likely dominated by layout/boilerplate.
- `diff_truncated` — true when the stored diff was truncated for readability.
- `diff_version` — diff algorithm version identifier (if available).
- `normalization_version` — normalization version identifier (if available).
- `computed_at_utc` — when this change event was computed (UTC).
- `compare_url` — stable public URL to the compare view.

Notes:

- Change events are **edition-aware** by default, reflecting captured snapshots,
  not real-time source updates.
- `compare_url` is safe for citation and shows the descriptive diff.

---

## Limitations

- Exports reflect **captured content**, not necessarily what changed on the
  original source site in real time.
- Coverage is limited to in-scope sources and successful captures.
- Replay fidelity varies by site and asset type; exports do not include replay
  assets or raw HTML.

