# Data Handling & Retention (internal)

Prevent accidental collection/retention creep and PHI risk. Keep notes public-safe.

## Issue reports (`POST /api/reports`)

Stored in DB table `issue_reports` with fields:

- `category`, `description` (free text)
- optional `reporter_email`, `snapshot_id`, `original_url`, `page_url`
- `status`, `internal_notes`

Policy:

- Public UI must warn users **not** to submit personal health information.
- Admin views are operator-only; never expose reports in public UI.
- If a report includes PHI, do not copy it into other systems/logs; redact/delete and record a public-safe note.
- Retention: keep minimal; retain only what’s needed to resolve the issue.

## Usage metrics (`GET /api/usage`)

- Stored in DB table `usage_metrics`: `metric_date`, `event`, `count`.
- Aggregated daily counts only (no IPs, no user IDs).
- Public API returns a rolling window (`HEALTHARCHIVE_USAGE_METRICS_WINDOW_DAYS`).

## Backups

- Postgres dumps (custom-format) are stored on the VPS (see `docs/deployment/production-single-vps.md`).
- Treat dumps as sensitive; they may contain report text/emails and should not be shared publicly.

## Server/application logs

- journald and web server logs may include IPs and request paths.
- Treat logs as sensitive; do not paste raw logs into public issues or git.

## Ops logs (public-safe)

- Restore tests: `/srv/healtharchive/ops/restore-tests/` (public-safe Markdown entries only).
- Adoption signals: `/srv/healtharchive/ops/adoption/` (public-safe; quarterly; links + aggregates only).
- Mentions log: keep public-safe; do not store private contact details.
