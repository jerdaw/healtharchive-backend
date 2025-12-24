# Ops playbooks (task-oriented)

Playbooks are short, task-oriented checklists for recurring operator work.

If you only read one thing first:

- `operator-responsibilities.md` (what you must do to keep the site healthy)

Rules:

- Keep them brief and procedural.
- Avoid duplicating canonical docs; link to the runbook/checklist that owns the details.
- Prefer stable command entrypoints (scripts) so steps don’t drift.

Recommended starting points:

- Deploy + verify: `deploy-and-verify.md`
- Automation posture: `automation-maintenance.md`
- Monitoring + alerting setup: `monitoring-and-alerting.md`
- Observability scaffolding (dirs + secrets): `observability-bootstrap.md`
- Cadence overview: `../ops-cadence-checklist.md` (what to do weekly/monthly/quarterly)

Other playbooks:

- Restore test (quarterly): `restore-test.md`
- Dataset release integrity (quarterly): `dataset-release.md`
- Adoption signals entry (quarterly): `adoption-signals.md`
- Replay service (if enabled): `replay-service.md`
- Annual campaign operations (seasonal): `annual-campaign.md`
- Incident response (when something breaks): `incident-response.md`
- Security posture (ongoing): `security-posture.md`
