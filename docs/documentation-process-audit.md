# Documentation process audit (2026-01-09)

Scope: HealthArchive project documentation processes and subprocesses across:

- `healtharchive-backend` (ops, runbooks, incident notes, canonical internal docs)
- `healtharchive-frontend` (public policy/reporting surfaces, UX copy, changelog)
- `healtharchive-datasets` (dataset release documentation and integrity posture)
- The local “workspace of sibling repos” convention used in `/home/jer/LocalSync/healtharchive/`

Goal: assess whether the documentation system is well-designed, maintainable, and aligned with modern best practices (docs-as-code + operational excellence), and identify concrete upgrades.

---

## Executive summary

Overall: the project’s documentation system is already unusually strong for its size. It is structured around a clear “docs-as-code” posture, high-signal operational procedures, and drift-resistant separation of backlog vs implementation plans vs canonical docs.

Key strengths (high confidence):

- **Drift prevention by design**: single canonical sources + pointer docs, and explicit backlog/plan/canonical separation (`docs/roadmaps/**` vs `docs/**`).
- **Operations maturity**: production runbook, playbooks, cadence checklists, monitoring/CI setup guidance, and safety posture are explicit and actionable.
- **Incident management**: severity rubric + incident template + operator response playbook + at least one real incident note showing good practice.
- **Public vs private boundaries**: explicit contracts for privacy-preserving usage metrics, issue-report retention, and non-public admin/metrics access.
- **Reproducibility**: dataset release integrity rules (checksums + manifest invariants) documented and operationalized.

Primary remaining gaps (fixable, low risk):

- **Templates / consistency**: you had strong examples, but no standard templates for new runbooks/playbooks, and changelog updates were not documented as an SOP.
- **Lifecycle + review cadence**: docs avoided duplication well, but “how we keep docs correct over time” could be more explicit (lightweight review + deprecation pattern).
- **Public communication integration**: incident notes were solid, but the “when do we update `/changelog` and/or `/status`?” expectation wasn’t explicit enough.

---

## Inventory of documentation “process surfaces”

This is the set of documents that define *how* documentation is produced, maintained, and used (not every domain-specific doc).

### Governance / doc architecture

- Canonical documentation policy and source-of-truth rules:
  - `healtharchive-backend/docs/documentation-guidelines.md`
- Index structure (discoverability):
  - `healtharchive-backend/docs/README.md`
  - `healtharchive-backend/docs/operations/README.md`
  - `healtharchive-backend/docs/operations/playbooks/README.md`
  - `healtharchive-backend/docs/roadmaps/README.md`
  - https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/README.md

### Planning / change management

- Backlog and implementation plan workflow:
  - `healtharchive-backend/docs/roadmap-process.md`
  - `healtharchive-backend/docs/roadmaps/future-roadmap.md`
  - `healtharchive-backend/docs/roadmaps/implemented/`

### Incidents and post-incident learning

- Incident SOP and artifacts:
  - `healtharchive-backend/docs/operations/incidents/README.md`
  - `healtharchive-backend/docs/operations/incidents/incident-template.md`
  - `healtharchive-backend/docs/operations/incidents/severity.md`
  - `healtharchive-backend/docs/operations/playbooks/incident-response.md`

### Operations and reliability subprocesses (repeatable routines)

- Cadence and routines:
  - `healtharchive-backend/docs/operations/ops-cadence-checklist.md`
- Monitoring/CI and deploy gating:
  - `healtharchive-backend/docs/operations/monitoring-and-ci-checklist.md`
  - `healtharchive-backend/docs/operations/playbooks/deploy-and-verify.md`
  - `healtharchive-backend/docs/operations/baseline-drift.md`
- Backup/restore validation:
  - `healtharchive-backend/docs/operations/restore-test-procedure.md`
  - `healtharchive-backend/docs/operations/restore-test-log-template.md`
- Data handling and privacy posture:
  - `healtharchive-backend/docs/operations/data-handling-retention.md`
  - `healtharchive-backend/docs/operations/observability-and-private-stats.md`

### Public-facing reporting surfaces (documentation for users)

- Changelog content lives in code, but is effectively “public documentation”:
  - https://github.com/jerdaw/healtharchive-frontend/blob/main/src/content/changelog.ts
  - (process) https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/changelog-process.md
- Status/impact pages are public reporting surfaces (operational transparency):
  - https://github.com/jerdaw/healtharchive-frontend/blob/main/src/app/%5Blocale%5D/status/page.tsx
  - https://github.com/jerdaw/healtharchive-frontend/blob/main/src/app/%5Blocale%5D/impact/page.tsx

### Release + reproducibility subprocesses

- Dataset releases and integrity expectations:
  - https://github.com/jerdaw/healtharchive-datasets/blob/main/README.md
  - `healtharchive-backend/docs/operations/export-integrity-contract.md`
  - `healtharchive-backend/docs/operations/dataset-release-runbook.md`

---

## Evaluation against modern best practices

This section uses a simple “Green / Yellow / Red” maturity signal.

### 1) Discoverability & information architecture — Green

Evidence:

- Dedicated indices exist for backend docs, ops docs, playbooks, and roadmaps.
- File naming is descriptive and stable (runbook, checklist, playbook).
- Cross-repo “canonical doc” pointers exist (env wiring, partner kit, data dictionary).

Residual risks:

- Some “project-level” navigation depends on having sibling repos locally (fine for operators, less ideal for single-repo readers on GitHub).

### 2) Single source of truth / drift control — Green

Evidence:

- Explicit canonical sources + pointer strategy.
- Explicit separation:
  - backlog (`roadmaps/future-roadmap.md`)
  - active plans (`docs/roadmaps/*.md`)
  - canonical docs (deployment/ops/dev)

Residual risks:

- Any duplicated non-git copies (e.g., ops roadmap) are inherently drift-prone; currently mitigated by explicit “keep synced” guidance.

### 3) Operational excellence (runbooks, playbooks, verification) — Green

Evidence:

- Production runbook is explicit about topology, security posture, and setup steps:
  - `healtharchive-backend/docs/deployment/production-single-vps.md`
- Deploy is treated as a verified procedure with a defined gate (“green main” + VPS verification).
- Baseline drift is operationalized as policy+observed+diff.
- Restore tests and dataset verification have explicit SOPs and templates.

Residual risks:

- As more workflows accumulate, templates become important to prevent playbooks/runbooks diverging in structure/quality.

### 4) Incident response & learning system — Green (with small upgrades)

Evidence:

- Incident notes have a clear SOP, a severity rubric, and a good template.
- The template includes: impact, detection, timeline, root cause, recovery, verification, action items.
- The repo has at least one high-quality real incident note, with follow-ups tied to a roadmap.

Residual risks:

- Public communication expectations (status/changelog) were implicit; now explicit guidance exists, but you may still want to decide a project stance:
  - “We always publish a public-safe note for sev0/sev1” vs “only when it changes user expectations”.

### 5) Public transparency & user-facing documentation — Yellow

Evidence:

- The site includes `/governance`, `/terms`, `/privacy`, `/changelog`, `/report`, `/status`, `/impact`.
- Copy inventory and disclaimer matrices exist to keep safety posture coherent.

Gaps:

- The changelog is a core public accountability surface, but without an explicit SOP it risks becoming stale or inconsistent (especially across EN/FR).

### 6) Security + privacy documentation posture — Green

Evidence:

- Clear “no secrets in git” posture across docs.
- Admin/metrics are explicitly private-only; tailnet access model is documented.
- Data retention and PHI risk are explicitly addressed for issue reports and logs.

Residual risks:

- If the project ever adds more operators, formalize “who has access to what” and credential rotation as explicit operator subprocesses.

### 7) Reproducibility and research integrity — Green

Evidence:

- Export endpoints have defined ordering/pagination invariants.
- Dataset releases are immutable objects with checksum verification and manifest invariants.
- Corrections are expected to be documented rather than silently rewriting history.

---

## Improvements implemented in this audit (2026-01-09)

These are low-risk upgrades that make doc creation and maintenance more consistent:

- Docs reference sanity checks (broken links/path refs):
  - Backend: `healtharchive-backend/scripts/check_docs_references.py` (wired into `healtharchive-backend/Makefile`)
  - Frontend: https://github.com/jerdaw/healtharchive-frontend/blob/main/scripts/check-doc-references.mjs (wired into `package.json`)
  - Datasets: https://github.com/jerdaw/healtharchive-datasets/blob/main/scripts/check_docs_references.py (wired into `Makefile`)
- Standardized templates:
  - `healtharchive-backend/docs/deployment/runbook-template.md`
  - `healtharchive-backend/docs/operations/playbooks/playbook-template.md`
- Decision records mechanism:
  - `healtharchive-backend/docs/decisions/README.md`
  - `healtharchive-backend/docs/decisions/decision-template.md`
- Clearer doc taxonomy, quality bar, and lifecycle guidance:
  - `healtharchive-backend/docs/documentation-guidelines.md`
- Public changelog SOP (source of truth, format, localization rules):
  - https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/changelog-process.md
- Stronger “incident → public-safe note” expectation (optional but recommended for sev0/sev1):
  - `healtharchive-backend/docs/operations/incidents/README.md`
  - `healtharchive-backend/docs/operations/incidents/incident-template.md`
  - `healtharchive-backend/docs/operations/incidents/severity.md`
  - `healtharchive-backend/docs/operations/ops-cadence-checklist.md`
- Process nudges in PR templates:
  - `healtharchive-backend/.github/pull_request_template.md`
  - https://github.com/jerdaw/healtharchive-frontend/blob/main/.github/pull_request_template.md

---

## Recommendations (next steps)

### P0 (high value, low effort)

- Decide an explicit **public incident disclosure posture**:
  - Option A: always add a public-safe `/changelog` entry for sev0/sev1 incidents.
  - Option B: only add a public-safe entry when it changes user expectations (outage, integrity risk, policy change).
- Make doc maintenance part of normal ops:
  - During the quarterly cadence, skim the production runbook + incident response playbook and fix drift discovered during real operations.

### P1 (medium value, moderate effort)

- (Implemented) Docs link/path sanity checks + decision records are now in place.

### P2 (later / if team grows)

- If/when there are multiple regular committers:
  - switch to PR-only merges (branch protection required checks),
  - introduce CODEOWNERS for high-risk areas (deployment/ops/policy pages),
  - require review for public-policy copy changes.

---

## “Top notch” principles to keep

- Prefer stable, scripted entrypoints over fragile shell snippets.
- Keep internal docs public-safe by default (assume they may be shared).
- Separate “what exists and how to operate it” from “how we got here” (roadmaps/implemented plans).
- Treat verification as first-class: every operational procedure should define what “done” means.
