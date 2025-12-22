# HealthArchive Ops Roadmap + TODO (internal)

This file tracks the current ops roadmap/todo items only. Keep it short and current.

For historical roadmaps and upgrade context, see:

- `docs/roadmaps/README.md` (backend repo)

## Recurring ops (non-IRL, ongoing)

- **Quarterly:** run a restore test and record a public-safe log entry in `/srv/healtharchive/ops/restore-tests/`.
- **Quarterly:** add an adoption signals entry in `/srv/healtharchive/ops/adoption/` (links + aggregates only).
- **Quarterly:** confirm dataset release exists and passes checksum verification (`sha256sum -c SHA256SUMS`).
- **Quarterly:** confirm core timers are still enabled and succeeding (`systemctl list-timers`, `journalctl -u <service>`).

## IRL / external validation (pending)

- Secure **1 distribution partner** willing to link to `/digest` or `/changes` (with permission to name them).
- Secure **1 verifier** willing to confirm your role and project utility (email confirmation is sufficient).
- Maintain the **mentions/citations log** discipline (public-safe; no private contact details).
