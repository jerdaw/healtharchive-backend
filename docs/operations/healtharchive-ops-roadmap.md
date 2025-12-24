# HealthArchive ops roadmap (internal)

This file tracks the current ops roadmap/todo items only. Keep it short and current.

For historical roadmaps and upgrade context, see:

- `docs/roadmaps/README.md` (backend repo)

Keep the two synced copies of this file aligned:

- Backend repo: `healtharchive-backend/docs/operations/healtharchive-ops-roadmap.md`
- Root non-git copy: `/home/jer/LocalSync/healtharchive/docs/operations/healtharchive-ops-roadmap.md`

## Recurring ops (non-IRL, ongoing)

- **Quarterly:** run a restore test and record a public-safe log entry in `/srv/healtharchive/ops/restore-tests/`.
- **Quarterly:** add an adoption signals entry in `/srv/healtharchive/ops/adoption/` (links + aggregates only).
- **Quarterly:** confirm dataset release exists and passes checksum verification (`sha256sum -c SHA256SUMS`).
- **Quarterly:** confirm core timers are enabled and succeeding (recommended: on the VPS run `cd /opt/healtharchive-backend && ./scripts/verify_ops_automation.sh`; then spot-check `journalctl -u <service>`).

## IRL / external validation (pending)

Track external validation/outreach work (partner, verifier, mentions/citations log) in:

- `../roadmaps/future-roadmap.md`
