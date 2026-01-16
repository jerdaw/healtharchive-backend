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

- Enable tiering health metrics + alerting:
  - Enable: `sudo systemctl enable --now healtharchive-tiering-metrics.timer`
  - Then confirm metrics: `curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_tiering_'`
  - Wire alerts (Prometheus/Alertmanager) on `healtharchive_tiering_metrics_ok==0` or a sustained “tiering unhealthy” signal.

## IRL / external validation (pending)

Track external validation/outreach work (partner, verifier, mentions/citations log) in:

- `../roadmaps/future-roadmap.md`
