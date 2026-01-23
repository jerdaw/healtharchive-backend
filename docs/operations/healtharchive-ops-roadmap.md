# HealthArchive ops roadmap (internal)

This file tracks the current ops roadmap/todo items only. Keep it short and current.

For historical roadmaps and upgrade context, see:

- `docs/roadmaps/README.md` (backend repo)

Keep the two synced copies of this file aligned:

- Backend repo: `docs/operations/healtharchive-ops-roadmap.md`
- Optional local working copy (non-git): if you keep a separate ops checklist outside the repo, keep it in sync with this canonical file.

## Recurring ops (non-IRL, ongoing)

- **Quarterly:** run a restore test and record a public-safe log entry in `/srv/healtharchive/ops/restore-tests/`.
- **Quarterly:** add an adoption signals entry in `/srv/healtharchive/ops/adoption/` (links + aggregates only).
- **Quarterly:** confirm dataset release exists and passes checksum verification (`sha256sum -c SHA256SUMS`).
- **Quarterly:** confirm core timers are enabled and succeeding (recommended: on the VPS run `cd /opt/healtharchive-backend && ./scripts/verify_ops_automation.sh`; then spot-check `journalctl -u <service>`).
- **Quarterly:** docs drift skim: re-read the production runbook + incident response and fix any drift you notice (keep docs matching reality).

## Current ops tasks (implementation already exists; enable/verify)

- Deploy the backend changes that improve annual crawl throughput (WARC-first, workers=2, docker shm sizing) and restart the worker.
- For already-created annual jobs, decide whether to patch `ArchiveJob.config.tool_options` to adopt:
  - `skip_final_build=true`
  - `docker_shm_size="1g"`
  - `stall_timeout_minutes=60` (canada.ca sources)
  - `initial_workers=2` (if you want it to take effect on retry/recovery)

## IRL / external validation (pending)

Track external validation/outreach work (partner, verifier, mentions/citations log) in:

- `../roadmaps/roadmap.md`
