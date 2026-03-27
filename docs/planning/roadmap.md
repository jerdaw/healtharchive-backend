# Future roadmap (backlog)

This file tracks **not-yet-implemented** work and planned upgrades.

It is intentionally **not** an implementation plan.

## How to use this file (workflow)

1. Pick a reasonable amount of work from the items in this backlog.
2. Create a focused implementation plan in `docs/planning/` (example name: `YYYY-MM-<topic>.md`).
3. Implement the work.
4. Update canonical documentation so operators/users can run and maintain the result.
5. Move the completed implementation plan to `docs/planning/implemented/` and date it.

## External / IRL work (not implementable in git)

These items are intentionally “external” and require ongoing human follow-through.

A consolidated, phased plan covering external outreach, scholarly outputs,
and application preparation is in:

- **`2026-02-admissions-strengthening-plan.md`** (active implementation plan)

That plan addresses Gates 1-4 below, the methods paper, dataset DOI, and
application-specific preparation on a ~12-week timeline.

Use that active plan, not this backlog file, as the canonical near-term
sequence for:

- reconciling real source/snapshot coverage counts across public materials
- updating the portfolio/about narrative page
- adding public uptime history and status-page evidence
- publishing the governance/ethics + data-retention summary
- verifier/partner/advisor outreach
- the methods paper + architecture diagram
- the first formal dataset release with a DOI

Individual items (for reference; see the plan above for execution order):

- External outreach + verification execution (operator-only):
  - Playbook: `../operations/playbooks/external/outreach-and-verification.md`
- Secure at least 1 distribution partner (permission to name them publicly).
- Secure at least 1 verifier (permission to name them publicly).
- Write and publish a methods paper (preprint + JOSS submission).
  - Outline: `../operations/methods-note-outline.md`
  - Plan: `2026-02-admissions-strengthening-plan.md` (Phase 2, item 2)
- Publish first formal dataset release with Zenodo DOI.
  - Runbook: `../operations/dataset-release-runbook.md`
  - Plan: `2026-02-admissions-strengthening-plan.md` (Phase 2, item 3)
- Maintain a public-safe mentions/citations log with real entries:
  - `../operations/mentions-log.md` (links only; no private contact data)
- Healthchecks.io alignment: keep systemd timers, `/etc/healtharchive/healthchecks.env`, and the Healthchecks UI in sync.
  - See: `../operations/playbooks/validation/healthchecks-parity.md` and `../deployment/production-single-vps.md`

Track the current status and next actions in:

- `../operations/healtharchive-ops-roadmap.md` for immediate PHAC + maintenance-window ops follow-through
- `2026-02-admissions-strengthening-plan.md` for the external-validation and scholarly-output sequence

Supporting materials:

- `../operations/outreach-templates.md`
- `../operations/partner-kit.md`
- `../operations/verification-packet.md`

## Transparency & public reporting (policy posture)

- Incident disclosure posture (current default: Option B):
  - Publish public-safe notes only when an incident changes user expectations (outage/degradation, integrity risk, security posture, policy change).
  - Decision record: `../decisions/2026-01-09-public-incident-disclosure-posture.md`
  - Revisit later: consider moving to “Option A” (always publish public-safe notes for sev0/sev1) once operations are demonstrably stable over multiple full campaign cycles.

## Real-world validation maturity (priority backlog)

Decision: these are all worth implementing because they materially improve external credibility, not just internal operations.

- 4-gate external validation target (cross-cutting):
  - Gate 1 (distribution): at least 1 named distribution partner with a public link/embed.
  - Gate 2 (verification): at least 1 named verifier with written confirmation and permission to name.
  - Gate 3 (citations discipline): mentions/citations log maintained with real, permission-aware public artifacts.
  - Gate 4 (repeatability evidence): quarterly dataset/recovery/automation/uptime artifacts show repeatable operations over multiple cycles.

Outstanding work (not fully implemented yet):

- Distribution partner proof (pending).
  - Existing scaffolding: `../operations/playbooks/external/outreach-and-verification.md`, `../operations/partner-kit.md`
  - Done when: one partner can be named publicly, with a durable public link/embed recorded in `../operations/mentions-log.md`.
- Verifier proof (pending).
  - Existing scaffolding: `../operations/verification-packet.md`
  - Done when: one verifier provides written confirmation and permission to be named publicly.
- Mentions/citations log discipline with real artifacts (partially implemented).
  - Existing scaffolding: `../operations/mentions-log.md`, `../_templates/mentions-log-template.md`
  - Done when: log has real dated entries tied to public links, and quarterly cadence updates are happening.
- Quarterly dataset release impact trail (partially implemented; pipeline exists).
  - Existing scaffolding: `../operations/dataset-release-runbook.md`, `../operations/playbooks/external/adoption-signals.md`
  - Done when: at least two consecutive quarterly cycles have both (a) published dataset releases and (b) dated adoption-signal entries.
- Restore-test discipline as repeated practice (partially implemented; first cycle done).
  - Existing scaffolding: `../operations/restore-test-procedure.md`, `../operations/playbooks/validation/restore-test.md`
  - Done when: restore-test logs exist for at least two consecutive quarterly cycles.
- Automation discipline with evidence artifacts (partially implemented).
  - Existing scaffolding: `../operations/playbooks/validation/automation-maintenance.md`, `../operations/automation-verification-rituals.md`
  - Done when: quarterly posture snapshots and run evidence exist, and failures are visible in logs/monitoring.
- External uptime/availability history (partially implemented).
  - Existing backlog: item #32 and item #33 below.
  - Done when: external monitor history is publicly visible (badge/status trend), not just current `/api/health`.
- Transparency counts over time for reports/takedowns/resolution (new backlog item).
  - Scope: publish aggregate-only periodic counts such as reports received, takedown-category reports, and resolved reports.
  - Guardrails: no report text, no emails, no personal identifiers.
  - Done when: a public surface exposes these aggregate trends with documented update cadence.
- Advisory circle with named participants (new external backlog item).
  - Scope: recruit 1-3 advisors/verifiers willing to be named publicly, with permission.
  - Done when: named list + role description is published and refreshed at least annually.

## Technical backlog (candidates)

Keep this list short; prefer linking to the canonical doc that explains the item.

### Storage & retention (backend)

- Storage/retention upgrades (only with a designed replay retention policy).
  - See: `../operations/growth-constraints.md`, `../deployment/replay-service-pywb.md`

### Crawling & indexing reliability (backend)

- WARC discovery consistency follow-through (deferred Phase 2-4 work; keep behavior coherent across status, indexing, and cleanup).
  - Historical context: `implemented/2026-01-29-warc-discovery-consistency.md`
  - Already implemented: `implemented/2026-01-29-warc-manifest-verification.md`
- Resolve the PHAC no-progress resume-loop state and re-evaluate the temporary `public-health-notices` exclusion.
  - Context: the 2026 PHAC annual crawl first hit sustained `net::ERR_HTTP2_PROTOCOL_ERROR` churn on canada.ca, and later a deployed `--disable-http2` compatibility change removed the visible HTTP/2 storm but still did not restore measurable crawl progress.
  - Current repo status:
    - the monitor/control-plane gap is closed in git, so stages that emit no
      `crawlStatus` for a full stall window now trigger an explicit `no_stats`
      stall instead of silently hanging
    - HC/PHAC Browsertrix-only chrome args are now carried through managed
      Browsertrix config instead of incompatible zimit CLI passthrough
    - resumed HC/PHAC phases now preserve those managed Browsertrix overrides by
      merging them into the stable `.zimit_resume.yaml`
  - Immediate follow-through is tracked in `../operations/healtharchive-ops-roadmap.md`; keep maintenance-window cutovers there rather than duplicating them in this backlog.
  - Remaining work:
    - determine why PHAC resumed attempts can terminate immediately with
      `crawled=0 total=2 failed=2` and empty/unprocessable WARC output even when
      the managed Browsertrix HTTP/2 workaround is present
    - determine whether the current PHAC resume queue/state is poisoned or
      whether the same failure reproduces from a truly fresh crawl phase
    - design a source-compatible crawler/runtime mitigation that restores
      intended PHAC coverage without reintroducing failure churn
    - decide whether the temporary exclusion is still needed once the deeper
      issue is understood
  - Related docs: `../operations/annual-campaign.md`, `../operations/healtharchive-ops-roadmap.md`
- Diagnose annual crawl cost/failure drivers by content class and refine scope toward the user-facing website.
  - Goal: determine which URL families or content classes (HTML pages, render assets, documents, archives, media, datasets) dominate WARC bytes, timeout churn, and restart budgets.
  - Why this matters: large downloadable artifacts may not belong on the annual crawl frontier if the product goal is to preserve the user-facing website rather than every downloadable file.
  - Active plan: `2026-03-23-annual-crawl-content-cost-and-scope-diagnosis.md`
- Continue crawl telemetry calibration from live annual-crawl runs, but use dashboard trends (crawl rate / phase churn / progress age) rather than direct throughput alerts.
  - Current focus: validate dashboard thresholds/visual cues and only promote a signal back into Alertmanager if it becomes clearly actionable.
  - Related docs: `../operations/monitoring-and-alerting.md`, `../operations/healtharchive-ops-roadmap.md`
- Consider whether a separate staging backend is worth it (increases ops surface; only do if it buys real safety).
  - See: `../deployment/environments-and-configuration.md`

### Repo governance (future)

- Tighten GitHub merge discipline when there are multiple committers (PR-only + required checks).
  - See: `../operations/monitoring-and-ci-checklist.md`

## Quality, governance, and product backlog (cross-repo)

This section tracks not-yet-implemented quality/governance work across backend, frontend, and datasets repos.
Completed items were removed from this backlog and archived in:

- `implemented/2026-02-12-governance-seo-and-security-foundations.md`
- Numbering is intentionally sparse to preserve stable item IDs from the original audit list.

### Governance and standards

<!-- Items #1 (CITATION.cff) and #2 (SECURITY.md) removed 2026-03-25: confirmed present in all three repos (backend, frontend, datasets). Completed as part of implemented/2026-02-12-governance-seo-and-security-foundations.md. -->

3. **Add a code of conduct to all repos** (S: 1h) — covered by `2026-02-admissions-strengthening-plan.md` Phase 3, item 6
4. **Add LICENSE to datasets repo** (S: 30m) — confirmed still missing as of 2026-03-25
5. **Add GitHub issue and PR templates across repos** (S: 2-3h) — covered by `2026-02-admissions-strengthening-plan.md` Phase 3, item 6; confirmed not yet present
7. **Add changelog/release tags to backend and frontend** (M: 1 day) — covered by `2026-02-admissions-strengthening-plan.md` Phase 3, item 6

### Reliability, security, and CI

23. **Create formal accessibility audit document** (M: 1-2 days) — covered by `2026-02-admissions-strengthening-plan.md` Phase 3, item 4
24. **Add frontend error boundary components** (M: 1 day)

### Documentation and operations maturity

26. **Create explicit data retention schedule table** (S: 2h) — covered by `2026-02-admissions-strengthening-plan.md` Phase 1, item 1d
27. **Add disaster recovery SLOs (RTO/RPO)** (S: 1-2h)
28. **Write first-responder / on-call runbook** (S: 2-3h)
29. **Create change-management runbook** (S: 2-3h)
30. **Formalize ethics/research exemption statement** (S: 1-2h) — covered by `2026-02-admissions-strengthening-plan.md` Phase 1, item 1d
31. **Add error tracking integration (Sentry)** (M: 1 day)
32. **Add automated uptime monitoring badge** (S: 1-2h) — covered by `2026-02-admissions-strengthening-plan.md` Phase 1, item 1c; external monitor (UptimeRobot) is described in the monitoring checklist but public badge and history page are not yet confirmed live as of 2026-03-25
33. **Add public status page content with uptime history** (M: 1 day) — covered by `2026-02-admissions-strengthening-plan.md` Phase 1, item 1c; `../operations/service-levels.md` notes no dedicated status page yet
34b. **Measure and record API/operational performance baselines** (S: 1-2h) — all baseline fields in `../operations/service-levels.md` remain TBD since 2026-01-18; collect real p50/p95 measurements from production under normal load and fill in the table

### Frontend quality and portfolio communication

35. **Consolidate bilingual strings (remove inline ternaries)** (L: 1-2 weeks)
36. **Add automated performance/Lighthouse testing** (M: 1 day)
37. **Add automated link checking to frontend CI** (S: 1-2h)
38. **Add coverage badges to READMEs** (S: 1-2h) — covered by `2026-02-admissions-strengthening-plan.md` Phase 3, item 6
39. **Create portfolio-ready project summary page** (M: 1 day) — covered by `2026-02-admissions-strengthening-plan.md` Phase 1, item 1b
40. **Generate architecture diagrams (Mermaid/D2)** (M: 1 day) — covered by `2026-02-admissions-strengthening-plan.md` Phase 2, item 2 (sub-task of methods paper)
41. **Create public changelog page on frontend** (M: 1 day) — covered by `2026-02-admissions-strengthening-plan.md` Phase 3, item 6
42. **Create automated WARC/data integrity report** (M: 1 day)

## Adjacent / optional (in this monorepo, not core HA)

- `rcdc/CDC_zim_mirror`: add startup DB sanity checks and clearer failure modes (empty/invalid LevelDB, missing prefixes, etc.).
