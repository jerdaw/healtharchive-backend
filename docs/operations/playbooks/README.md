# Ops playbooks (task-oriented)

Playbooks are short, task-oriented checklists for recurring operator work.

If you only read one thing first:

- [operator-responsibilities.md](core/operator-responsibilities.md) (what you must do to keep the site healthy)

Rules:

- Keep them brief and procedural.
- Avoid duplicating canonical docs; link to the runbook/checklist that owns the details.
- Prefer stable command entrypoints (scripts) so steps don't drift.
- Use the template for new playbooks: `../../_templates/playbook-template.md`

## Directory Structure

Playbooks are organized by category:

```
playbooks/
├── core/           # Essential daily operator work
├── observability/  # Monitoring and alerting setup
├── crawl/          # Crawl and archive lifecycle
├── storage/        # WARC storage and integrity
├── validation/     # Quality assurance and verification
└── external/       # External-facing operations
```

## Core Operations

Essential playbooks for daily operator work:

- **Operator responsibilities**: [core/operator-responsibilities.md](core/operator-responsibilities.md) — Core operator duties
- **Deploy & verify**: [core/deploy-and-verify.md](core/deploy-and-verify.md) — Deployment workflow
- **Incident response**: [core/incident-response.md](core/incident-response.md) — When something breaks
- **Admin proxy**: [core/admin-proxy.md](core/admin-proxy.md) — Browser-friendly ops triage
- **Replay service**: [core/replay-service.md](core/replay-service.md) — Replay service operations

## Observability

Monitoring infrastructure setup and maintenance:

- **Setup guide**: [observability/observability-guide.md](observability/observability-guide.md) — Complete observability stack setup (Prometheus, Grafana, Alertmanager)
- **Monitoring setup**: [observability/monitoring-and-alerting.md](observability/monitoring-and-alerting.md) — External monitors and Healthchecks

## Crawl & Archive Operations

Managing crawls and archive lifecycle:

- **Crawl preflight**: [crawl/crawl-preflight.md](crawl/crawl-preflight.md) — Pre-crawl audit (before annual/large crawls)
- **Crawl stalls**: [crawl/crawl-stalls.md](crawl/crawl-stalls.md) — Stalled progress + status snapshot
- **Annual campaign**: [crawl/annual-campaign.md](crawl/annual-campaign.md) — Seasonal campaign operations
- **Controlled restart**: [crawl/2026-01-annual-campaign-controlled-restart.md](crawl/2026-01-annual-campaign-controlled-restart.md) — 2026 annual crawl restart procedure
- **Cleanup automation**: [crawl/cleanup-automation.md](crawl/cleanup-automation.md) — Safe temp cleanup

## Storage Management

WARC storage and integrity:

- **WARC storage tiering**: [storage/warc-storage-tiering.md](storage/warc-storage-tiering.md) — SSD + Storage Box tiering
- **WARC integrity**: [storage/warc-integrity-verification.md](storage/warc-integrity-verification.md) — Verify WARCs
- **Stale mount recovery**: [storage/storagebox-sshfs-stale-mount-recovery.md](storage/storagebox-sshfs-stale-mount-recovery.md) — Errno 107 recovery
- **Recovery drills**: [storage/storagebox-sshfs-stale-mount-drills.md](storage/storagebox-sshfs-stale-mount-drills.md) — Safe production drills

## Validation & Testing

Quality assurance and verification:

- **Restore test**: [validation/restore-test.md](validation/restore-test.md) — Quarterly restore test
- **Dataset release**: [validation/dataset-release.md](validation/dataset-release.md) — Dataset release integrity (quarterly)
- **Coverage guardrails**: [validation/coverage-guardrails.md](validation/coverage-guardrails.md) — Annual regression checks
- **Replay smoke tests**: [validation/replay-smoke-tests.md](validation/replay-smoke-tests.md) — Daily replay validation
- **Healthchecks parity**: [validation/healthchecks-parity.md](validation/healthchecks-parity.md) — Env/systemd/Healthchecks sync
- **Security posture**: [validation/security-posture.md](validation/security-posture.md) — Ongoing security checks
- **Automation maintenance**: [validation/automation-maintenance.md](validation/automation-maintenance.md) — Keep automation healthy

## External & Outreach

External-facing operations:

- **Outreach & verification**: [external/outreach-and-verification.md](external/outreach-and-verification.md) — External outreach workflow
- **Adoption signals**: [external/adoption-signals.md](external/adoption-signals.md) — Quarterly adoption signals entry

## Quick Reference

| Frequency | Tasks | Playbooks |
|-----------|-------|-----------|
| **Daily** | Service health, crawl status | ops-cadence-checklist.md, crawl/crawl-stalls.md |
| **Weekly** | Monitoring review, automation posture | ops-cadence-checklist.md, validation/automation-maintenance.md |
| **Monthly** | Reliability review, docs drift | ops-cadence-checklist.md |
| **Quarterly** | Restore test, dataset release, adoption signals | validation/restore-test.md, validation/dataset-release.md, external/adoption-signals.md |
| **Annual** | Campaign readiness, coverage guardrails | crawl/annual-campaign.md, validation/coverage-guardrails.md |

For the complete operations cadence: `../ops-cadence-checklist.md`
