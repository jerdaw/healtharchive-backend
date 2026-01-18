# Restore Test Log (template)

Use this template to record quarterly restore tests. Keep it public-safe: no secrets, credentials, or internal IPs.

## Restore test record

- **Date (UTC):**
- **Operator:**
- **Backup source used:** (e.g., latest nightly dump, date, location)
- **Restore target:** (local temp DB / staging host)
- **Restore method:** (command summary)
- **Schema check:** (`alembic current` output)
- **API checks:** (`/api/health`, `/api/stats`, `/api/sources`)
- **Result:** Pass / Fail
- **Notes / anomalies:**
- **Follow-up actions:**
