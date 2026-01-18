# Ops playbooks (task-oriented)

Playbooks are short, task-oriented checklists for recurring operator work.

If you only read one thing first:

- `operator-responsibilities.md` (what you must do to keep the site healthy)

Rules:

- Keep them brief and procedural.
- Avoid duplicating canonical docs; link to the runbook/checklist that owns the details.
- Prefer stable command entrypoints (scripts) so steps don't drift.
- Use the template for new playbooks: `../../_templates/playbook-template.md`

## Core Operations

Essential playbooks for daily operator work:

- **Operator responsibilities**: `operator-responsibilities.md` — Core operator duties
- **Deploy & verify**: `deploy-and-verify.md` — Deployment workflow
- **Incident response**: `incident-response.md` — When something breaks
- **Admin proxy**: `admin-proxy.md` — Browser-friendly ops triage
- **Automation posture**: `automation-maintenance.md` — Keep automation healthy

## Observability Setup

Setting up and maintaining monitoring infrastructure:

- **Bootstrap**: `observability-bootstrap.md` — Dirs + secrets scaffolding
- **Exporters**: `observability-exporters.md` — Node + Postgres exporters (loopback-only)
- **Prometheus**: `observability-prometheus.md` — Scrape config + retention (loopback-only)
- **Grafana**: `observability-grafana.md` — Grafana install (loopback-only) + tailnet access
- **Dashboards**: `observability-dashboards.md` — Dashboard provisioning
- **Alerting**: `observability-alerting.md` — Prometheus + Alertmanager (minimal, high-signal)
- **Monitoring setup**: `monitoring-and-alerting.md` — Complete monitoring setup guide
- **Maintenance**: `observability-maintenance.md` — Keep observability healthy

## Crawl & Archive Operations

Managing crawls and archive lifecycle:

- **Crawl preflight**: `crawl-preflight.md` — Pre-crawl audit (before annual/large crawls)
- **Crawl stalls**: `crawl-stalls.md` — Stalled progress + status snapshot
- **Annual campaign**: `annual-campaign.md` — Seasonal campaign operations
- **Cleanup automation**: `cleanup-automation.md` — Safe temp cleanup
- **Replay service**: `replay-service.md` — Replay service operations (if enabled)

## Storage Management

WARC storage and integrity:

- **WARC storage tiering**: `warc-storage-tiering.md` — SSD + Storage Box tiering
- **WARC integrity**: `warc-integrity-verification.md` — Verify WARCs
- **Stale mount recovery**: `storagebox-sshfs-stale-mount-recovery.md` — Errno 107 recovery
- **Recovery drills**: `storagebox-sshfs-stale-mount-drills.md` — Safe production drills

## Validation & Testing

Quality assurance and verification:

- **Coverage guardrails**: `coverage-guardrails.md` — Annual regression checks
- **Replay smoke tests**: `replay-smoke-tests.md` — Daily replay validation
- **Restore test**: `restore-test.md` — Quarterly restore test
- **Dataset release**: `dataset-release.md` — Dataset release integrity (quarterly)
- **Healthchecks parity**: `healthchecks-parity.md` — Env ↔ systemd ↔ Healthchecks sync

## External & Outreach

External-facing operations:

- **Outreach & verification**: `outreach-and-verification.md` — External outreach workflow
- **Adoption signals**: `adoption-signals.md` — Quarterly adoption signals entry
- **Security posture**: `security-posture.md` — Ongoing security checks

## Quick Reference

| Frequency | Tasks | Playbooks |
|-----------|-------|-----------|
| **Daily** | Service health, crawl status | ops-cadence-checklist.md, crawl-stalls.md |
| **Weekly** | Monitoring review, automation posture | ops-cadence-checklist.md, automation-maintenance.md |
| **Monthly** | Reliability review, docs drift | ops-cadence-checklist.md |
| **Quarterly** | Restore test, dataset release, adoption signals | restore-test.md, dataset-release.md, adoption-signals.md |
| **Annual** | Campaign readiness, coverage guardrails | annual-campaign.md, coverage-guardrails.md |

For the complete operations cadence: `../ops-cadence-checklist.md`
