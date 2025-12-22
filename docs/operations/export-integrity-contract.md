# Export Integrity Contract (internal)

Exports and dataset releases must be defensible and reproducible over time.

## Export endpoints (ordering + pagination)

- `GET /api/exports/snapshots` is ordered by `snapshot_id` ascending and paginates via `afterId`.
- `GET /api/exports/changes` is ordered by `change_id` ascending and paginates via `afterId`.

## Dataset release manifest (`manifest.json`)

Required fields:

- `version` (schema version for the manifest itself)
- `tag` (release tag)
- `releasedAtUtc` (ISO-8601 UTC timestamp)
- `apiBase` and `exportsManifest` (from `GET /api/exports`)
- `artifacts.snapshots` and `artifacts.changes` including:
  - `rows`, `minId`, `maxId`, `requestsMade`, `limitPerRequest`, `truncated`
  - `sha256` for each artifact

Rules:

- `SHA256SUMS` must match all listed files.
- `truncated` should be `false` for both exports; if `true`, treat the release as incomplete and re-run.

## Immutability / corrections

- Treat published dataset release tags as immutable research objects.
- If a correction is required, document it in release notes and prefer a new tag over silently rewriting history.

## Diff recomputation policy

- Change export rows include `diff_version` and `normalization_version`.
- If change tracking methodology changes, bump versions and document it (methods note/changelog) rather than silently rewriting history.
