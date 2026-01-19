# Operations Documentation

## Start Here

**New operator?**

- **First:** [Operator Responsibilities](playbooks/operator-responsibilities.md) — Core duties
- **Deploy:** [Deploy & Verify](playbooks/deploy-and-verify.md) — Deployment workflow
- **Monitor:** [Monitoring Checklist](monitoring-and-ci-checklist.md) — Monitoring setup
- **Respond:** [Incident Response](playbooks/incident-response.md) — When something breaks

**Quick reference:**

| Task | Documentation |
| :--- | :--- |
| Daily checks | [Ops Cadence](ops-cadence-checklist.md) |
| Deploy changes | [Deploy & Verify](playbooks/deploy-and-verify.md) |
| Investigate issues | [Incident Response](playbooks/incident-response.md) |
| Monitor health | [Monitoring](monitoring-and-ci-checklist.md) |
| Quarterly tasks | [Restore Test](playbooks/restore-test.md), [Dataset Release](dataset-release-runbook.md) |

## All Operational Documentation

- Ops playbooks (task-oriented checklists): `playbooks/README.md`
- Incident notes / postmortems (internal): `incidents/README.md`
- Observability + private stats contract (public vs private boundaries): `observability-and-private-stats.md`
- Annual capture campaign (scope + seeds): `annual-campaign.md`
- Automation implementation plan (phased, production-only): `automation-implementation-plan.md`
- Monitoring + uptime + CI checklist: `monitoring-and-ci-checklist.md`
- Annual Crawl Alerting Strategy: `monitoring-and-alerting.md`
- Agent handoff guidelines (internal rules): `agent-handoff-guidelines.md`
- Claims registry (proof artifacts): `claims-registry.md`
- Data handling & retention (internal contract): `data-handling-retention.md`
- Export integrity contract (manifest + immutability): `export-integrity-contract.md`
- Automation verification rituals (timer checks): `automation-verification-rituals.md`
- Dataset release runbook (verification checklist): `dataset-release-runbook.md`
- Risk register (top risks + mitigations): `risk-register.md`
- Ops cadence checklist (internal routine): `ops-cadence-checklist.md`
- Ops UI friction log template (internal; ongoing): `../_templates/ops-ui-friction-log-template.md`
- Growth constraints (storage + scope budgets): `growth-constraints.md`
- Legacy crawl imports (historical import notes): `legacy-crawl-imports.md`
- Restore test procedure (quarterly): `restore-test-procedure.md`
- Restore test log template: `../_templates/restore-test-log-template.md`
- Adoption signals log template (public-safe, quarterly): `../_templates/adoption-signals-log-template.md`
- HealthArchive ops roadmap + todo (remaining tasks): `healtharchive-ops-roadmap.md`
- Partner kit (brief + citation + screenshots): `partner-kit.md`
- One-page brief (pointer to frontend public asset): `one-page-brief.md`
- Citation handout (pointer to frontend public asset): `citation-handout.md`
- Outreach templates (email copy): `outreach-templates.md`
- Verification packet (verifier handoff): `verification-packet.md`
- Mentions log (public-safe, link-only): `mentions-log.md`
- Mentions log template (public-safe): `mentions-log-template.md`
- Exports data dictionary (pointer to public asset): `exports-data-dictionary.md`
- Methods note outline (poster/preprint scaffold): `methods-note-outline.md`
- Search relevance evaluation (process + commands): `search-quality.md`
- Golden queries + expected results (living checklist): `search-golden-queries.md`
- Replay + preview automation plan (design + guardrails; includes `replay-reconcile`): `replay-and-preview-automation-plan.md`
- Production baseline drift checks (policy + snapshot + compare): `baseline-drift.md`

## Mission Reports & Logs

- **2026-01-19**: [Annual Crawl Hardening Shipment](reports/2026-01-19-deployment-log.md)
- **2026-01-19**: [Investigation: Indexing Delay / Zero Indexed Pages](reports/2026-01-19-indexing-investigation.md)
