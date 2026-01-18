# Documentation architecture improvements (v1) — implementation plan

Status: **planned** (created 2026-01-17)

## Goal

Improve the documentation architecture to better follow modern best practices while
preserving the project's existing strengths (clear taxonomy, multi-repo boundaries,
pointer-based cross-repo linking).

**Key improvements:**

1. **Enhanced discoverability** — Critical docs should be navigable without reading README indices
2. **Diátaxis-aligned structure** — Clear separation of tutorials, how-to guides, reference, and explanation
3. **Template isolation** — Move templates to dedicated directory
4. **Improved cross-repo linking** — Consistent GitHub URL patterns
5. **Navigation hierarchy** — Better use of MkDocs Material's navigation features

This plan produces **documentation reorganization only** — no code changes.

## Why this is "next" (roadmap selection)

Documentation improvements support all other work because:

- **Operator efficiency** — Faster access to runbooks and playbooks during incidents
- **Onboarding** — New contributors can find docs without tribal knowledge
- **Maintenance** — Clear structure reduces drift and duplication
- **Scalability** — Good architecture supports growing documentation

## Current state analysis

### Strengths (preserve these)

1. **Clear document taxonomy** — Runbooks, playbooks, decision records, policies, logs, templates
2. **Multi-repo boundary** — Pointer-based linking avoids duplication and drift
3. **README index pattern** — Each directory has a comprehensive index
4. **Quality bar defined** — Purpose, audience, preconditions, steps, verification, safety
5. **Lifecycle management** — Clear process for outdated docs and roadmap workflow

### Issues identified

| Issue | Impact | Priority |
|-------|--------|----------|
| **98 of 121 docs not in navigation** | Users must know to read READMEs to discover docs | High |
| **Critical docs hidden** | Production runbook not directly navigable | High |
| **Templates mixed with content** | `*-template.md` files appear as substantive docs | Medium |
| **Inconsistent cross-repo links** | Mix of workspace-relative and GitHub URLs | Medium |
| **Flat playbooks listing** | 32 playbooks in one README without categorization | Medium |
| **No audience-based entry points** | Operators, developers, researchers have same entry | Low |

### Navigation statistics

| Category | Files on disk | Files in nav | Coverage |
|----------|---------------|--------------|----------|
| Operations | 50+ | 7 | 14% |
| Deployment | 15+ | 1 | 7% |
| Development | 5 | 1 | 20% |
| Playbooks | 32 | 1 (index) | 3% |
| Roadmaps | 20 | 1 (index) | 5% |
| Decisions | 2 | 1 (index) | 50% |
| **Total** | **121** | **23** | **19%** |

---

## Scope, goals, constraints

### In-scope outcomes (what we will deliver)

- **Expanded navigation** — Key docs directly accessible from sidebar
- **Audience-based entry points** — Operators, developers, and researchers can find relevant docs
- **Template isolation** — Templates moved to `_templates/` directory
- **Categorized playbooks** — Logical groupings in navigation
- **Consistent cross-repo links** — Standardized on GitHub URLs
- **Updated documentation guidelines** — Reflect new structure

### Non-goals (explicitly out of scope)

- Rewriting doc content (only reorganization)
- Adding new documentation (covered by other plans)
- Changing the multi-repo boundary approach (it's working well)
- Full Diátaxis compliance (too heavy for current scale)

### Constraints to respect

- **Backward compatibility** — Existing links should not break (use redirects if needed)
- **Minimal disruption** — Changes should be incremental and reviewable
- **README index preservation** — Keep READMEs as comprehensive indices
- **Build stability** — `make docs-build` must pass throughout

---

## Modern documentation best practices (reference)

### Diátaxis framework (adapted for HealthArchive)

| Type | Purpose | HealthArchive equivalent |
|------|---------|-------------------------|
| **Tutorials** | Learning-oriented, step-by-step | `development/live-testing.md`, `dev-environment-setup.md` |
| **How-to guides** | Task-oriented, problem-solving | Playbooks, runbooks |
| **Reference** | Information-oriented, accurate | Architecture, API docs, schemas |
| **Explanation** | Understanding-oriented, context | Decision records, guidelines |

### Navigation best practices

1. **7±2 rule** — Top-level sections should be 5-9 items
2. **3-click rule** — Users should find any doc within 3 clicks
3. **Progressive disclosure** — Start with overview, drill down to details
4. **Consistent depth** — Similar content at similar navigation depth
5. **Clear labeling** — Navigation labels should be self-explanatory

### MkDocs Material features to leverage

- `navigation.sections` — Group related pages under headers
- `navigation.tabs` — Top-level tabs for major sections (already enabled)
- `navigation.expand` — Auto-expand sections (optional)
- `navigation.indexes` — Section index pages
- `navigation.prune` — Hide empty sections

---

## Phase 1 — Template isolation

**Objective:** Move template files to a dedicated directory to prevent confusion.

### 1.1 Create templates directory

```
docs/_templates/
├── README.md              # How to use templates
├── runbook-template.md
├── playbook-template.md
├── incident-template.md
├── decision-template.md
├── restore-test-log-template.md
├── adoption-signals-log-template.md
├── mentions-log-template.md
└── ops-ui-friction-log-template.md
```

### 1.2 Move template files

```bash
mkdir -p docs/_templates
mv docs/deployment/runbook-template.md docs/_templates/
mv docs/operations/playbooks/playbook-template.md docs/_templates/
mv docs/operations/incidents/incident-template.md docs/_templates/
mv docs/decisions/decision-template.md docs/_templates/
mv docs/operations/restore-test-log-template.md docs/_templates/
mv docs/operations/adoption-signals-log-template.md docs/_templates/
mv docs/operations/mentions-log-template.md docs/_templates/
mv docs/operations/ops-ui-friction-log-template.md docs/_templates/
```

### 1.3 Update references

Update all docs that reference templates to use new paths.

### 1.4 Exclude from build (optional)

Templates don't need to be in the published site:

```yaml
# mkdocs.yml
exclude_docs: |
  frontend/**
  _templates/**
```

Or keep them in build but not in nav (current implicit behavior is fine).

**Deliverables:**
- `_templates/` directory created with README
- All template files moved
- References updated

**Exit criteria:** `make docs-build` passes; templates discoverable in one location.

---

## Phase 2 — Expand navigation with key operational docs

**Objective:** Add critical operational docs to sidebar navigation.

### 2.1 Identify must-have docs for navigation

**Deployment (add to nav):**
- `production-single-vps.md` — Current production runbook
- `environments-and-configuration.md` — Cross-repo configuration
- `hosting-and-live-server-to-dos.md` — Deployment checklist

**Development (add to nav):**
- `live-testing.md` — Local testing flows
- `dev-environment-setup.md` — Environment setup
- `testing-guidelines.md` — Test conventions

**Operations (add to nav):**
- `monitoring-and-ci-checklist.md` — Monitoring guidance
- `ops-cadence-checklist.md` — Recurring ops tasks
- `risk-register.md` — Risk tracking

### 2.2 Update mkdocs.yml navigation

```yaml
nav:
  - Home: README.md
  - Project: project.md
  - Operations:
      - Overview: operations/README.md
      - Ops Roadmap: operations/healtharchive-ops-roadmap.md
      - Incidents:
          - Overview: operations/incidents/README.md
          - Storage Hotpath: operations/incidents/2026-01-08-storage-hotpath-sshfs-stale-mount.md
          - Annual Crawl Stalled: operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md
          - PHAC Permission Denied: operations/incidents/2026-01-09-annual-crawl-phac-output-dir-permission-denied.md
          - Replay Smoke 503: operations/incidents/2026-01-16-replay-smoke-503-and-warctieringfailed.md
      - Playbooks:
          - Overview: operations/playbooks/README.md
          - Operator Responsibilities: operations/playbooks/operator-responsibilities.md
          - Deploy & Verify: operations/playbooks/deploy-and-verify.md
          - Incident Response: operations/playbooks/incident-response.md
      - Monitoring: operations/monitoring-and-ci-checklist.md
      - Ops Cadence: operations/ops-cadence-checklist.md
      - Risk Register: operations/risk-register.md
      - Baseline Drift: operations/baseline-drift.md
      - Dataset Release: operations/dataset-release-runbook.md
  - Backend:
      - Architecture: architecture.md
      - Decisions: decisions/README.md
      - Deployment:
          - Overview: deployment/README.md
          - Production Runbook: deployment/production-single-vps.md
          - Configuration: deployment/environments-and-configuration.md
          - Checklist: deployment/hosting-and-live-server-to-dos.md
      - Development:
          - Overview: development/README.md
          - Live Testing: development/live-testing.md
          - Dev Setup: development/dev-environment-setup.md
          - Testing: development/testing-guidelines.md
      - Guidelines: documentation-guidelines.md
  - API: api.md
  - Frontend:
      - Overview: frontend-external/README.md
      - I18n: frontend-external/i18n.md
      - Implementation: frontend-external/implementation-guide.md
  - Datasets: datasets-external/README.md
  - Roadmaps:
      - Overview: roadmaps/README.md
      - Backlog: roadmaps/future-roadmap.md
```

**Deliverables:**
- Updated mkdocs.yml with expanded navigation
- Key docs directly accessible

**Exit criteria:** Critical docs accessible within 2 clicks from homepage.

---

## Phase 3 — Categorize playbooks in navigation

**Objective:** Group 32 playbooks into logical categories for easier navigation.

### 3.1 Define playbook categories

| Category | Playbooks | Purpose |
|----------|-----------|---------|
| **Core Operations** | operator-responsibilities, deploy-and-verify, incident-response | Daily operator work |
| **Observability** | monitoring-and-alerting, observability-* (7 files) | Monitoring setup/maintenance |
| **Crawl & Archive** | crawl-preflight, crawl-stalls, annual-campaign, cleanup-automation | Crawl lifecycle |
| **Storage** | warc-storage-tiering, warc-integrity-verification, storagebox-* | Storage management |
| **Validation** | coverage-guardrails, replay-smoke-tests, restore-test, dataset-release | Quality assurance |
| **External** | outreach-and-verification, adoption-signals, security-posture | External-facing |

### 3.2 Update playbooks README with categories

Restructure `operations/playbooks/README.md` to group by category:

```markdown
# Ops playbooks (task-oriented)

## Core Operations
- Operator responsibilities: `operator-responsibilities.md`
- Deploy & verify: `deploy-and-verify.md`
- Incident response: `incident-response.md`
- Admin proxy: `admin-proxy.md`

## Observability Setup
- Bootstrap: `observability-bootstrap.md`
- Exporters: `observability-exporters.md`
- Prometheus: `observability-prometheus.md`
- Grafana: `observability-grafana.md`
- Dashboards: `observability-dashboards.md`
- Alerting: `observability-alerting.md`
- Maintenance: `observability-maintenance.md`
- Automation maintenance: `automation-maintenance.md`

## Crawl & Archive Operations
- Crawl preflight: `crawl-preflight.md`
- Crawl stalls: `crawl-stalls.md`
- Annual campaign: `annual-campaign.md`
- Cleanup automation: `cleanup-automation.md`
- Replay service: `replay-service.md`

## Storage Management
- WARC storage tiering: `warc-storage-tiering.md`
- WARC integrity: `warc-integrity-verification.md`
- Stale mount recovery: `storagebox-sshfs-stale-mount-recovery.md`
- Recovery drills: `storagebox-sshfs-stale-mount-drills.md`

## Validation & Testing
- Coverage guardrails: `coverage-guardrails.md`
- Replay smoke tests: `replay-smoke-tests.md`
- Restore test: `restore-test.md`
- Dataset release: `dataset-release.md`
- Healthchecks parity: `healthchecks-parity.md`

## External & Outreach
- Outreach & verification: `outreach-and-verification.md`
- Adoption signals: `adoption-signals.md`
- Security posture: `security-posture.md`
```

### 3.3 Update navigation with category sections

```yaml
- Playbooks:
    - Overview: operations/playbooks/README.md
    - Core:
        - Operator Responsibilities: operations/playbooks/operator-responsibilities.md
        - Deploy & Verify: operations/playbooks/deploy-and-verify.md
        - Incident Response: operations/playbooks/incident-response.md
    - Observability:
        - Setup: operations/playbooks/observability-bootstrap.md
        - Alerting: operations/playbooks/observability-alerting.md
    - Storage:
        - WARC Tiering: operations/playbooks/warc-storage-tiering.md
        - Stale Mount Recovery: operations/playbooks/storagebox-sshfs-stale-mount-recovery.md
```

(Include key playbooks; others remain discoverable via README)

**Deliverables:**
- Playbooks README restructured with categories
- Navigation updated with playbook categories

**Exit criteria:** Playbooks are logically grouped and key ones are directly navigable.

---

## Phase 4 — Standardize cross-repo linking

**Objective:** Ensure consistent GitHub URL patterns for cross-repo references.

### 4.1 Define linking conventions

**Rule:** Use GitHub URLs for cross-repo references, not workspace-relative paths.

**Pattern:**
```markdown
# Good (stable GitHub URL)
See the [bilingual dev guide](https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/development/bilingual-dev-guide.md)

# Avoid (workspace-relative)
See `healtharchive-frontend/docs/development/bilingual-dev-guide.md`
```

### 4.2 Identify files with workspace-relative links

Scan roadmap files (especially implemented ones) for patterns like:
- `healtharchive-frontend/...`
- `healtharchive-datasets/...`
- `../../../healtharchive-frontend/...`

### 4.3 Update links to GitHub URLs

For each workspace-relative link:
- Convert to `https://github.com/jerdaw/<repo>/blob/main/<path>`
- Or use docs.healtharchive.ca links for backend docs

### 4.4 Document convention in guidelines

Add to `documentation-guidelines.md`:

```markdown
## Cross-repo link format

When linking to other repositories:

- Use GitHub URLs: `https://github.com/jerdaw/<repo>/blob/main/<path>`
- For backend docs: `https://docs.healtharchive.ca/<path>`
- Do not use workspace-relative paths like `healtharchive-frontend/...`

GitHub URLs are stable and work in all contexts (browser, IDE, published docs).
```

**Deliverables:**
- Workspace-relative links converted to GitHub URLs
- Convention documented in guidelines

**Exit criteria:** All cross-repo links use stable URLs.

---

## Phase 5 — Add audience-based entry points

**Objective:** Create clear starting points for different audiences.

### 5.1 Define audience personas

| Persona | Primary needs | Entry point |
|---------|--------------|-------------|
| **Operator** | Run the service, respond to incidents | Operations overview |
| **Developer** | Contribute code, run locally | Development overview |
| **Researcher** | Use the archive, cite data | Project overview + API |

### 5.2 Enhance README index pages

Update each major README to include:
- "Start here if you are..." section
- Quick links to most common tasks
- Clear pointers to related sections

**Example for operations/README.md:**

```markdown
# Operations docs

## Start here

- **New operator?** Start with [Operator Responsibilities](playbooks/operator-responsibilities.md)
- **Incident in progress?** Go to [Incident Response](playbooks/incident-response.md)
- **Deploying changes?** See [Deploy & Verify](playbooks/deploy-and-verify.md)
- **Setting up monitoring?** See [Observability Bootstrap](playbooks/observability-bootstrap.md)

## Quick reference

| Task | Doc |
|------|-----|
| Daily checks | [Ops Cadence](ops-cadence-checklist.md) |
| Weekly tasks | [Ops Cadence](ops-cadence-checklist.md) |
| Quarterly tasks | [Restore Test](playbooks/restore-test.md), [Adoption Signals](playbooks/adoption-signals.md) |

## All docs

[Full list below...]
```

### 5.3 Update home README

Enhance `docs/README.md` with audience-based navigation:

```markdown
# HealthArchive Documentation

## Quick start by role

- **Operators**: Start with [Operations Overview](operations/README.md)
- **Developers**: Start with [Development Guide](development/README.md)
- **API consumers**: Start with [API Documentation](api.md)

## Key resources

| Need | Doc |
|------|-----|
| Architecture overview | [Architecture](architecture.md) |
| Production runbook | [Production Single-VPS](deployment/production-single-vps.md) |
| Local development | [Live Testing](development/live-testing.md) |
| Incident response | [Incident Response](operations/playbooks/incident-response.md) |
```

**Deliverables:**
- README pages enhanced with audience-based navigation
- Home page includes role-based quick start

**Exit criteria:** Any persona can find their starting point within 1 click.

---

## Phase 6 — Documentation guidelines update

**Objective:** Update guidelines to reflect new structure and conventions.

### 6.1 Add navigation policy

```markdown
## Navigation policy

### What goes in mkdocs.yml nav

- All README index pages
- Docs that are frequently accessed or critical for operations
- At least one doc from each major category

### What stays README-only

- Detailed playbooks beyond the core set
- Historical/archived roadmaps (implemented/)
- Log files and templates
- Highly specialized procedures

### Organizing new docs

When adding new docs:
1. Add to appropriate directory
2. Update the directory's README.md index
3. If critical or frequently accessed, add to mkdocs.yml nav
4. Ensure cross-links from related docs
```

### 6.2 Add template usage section

```markdown
## Using templates

Templates are stored in `docs/_templates/`. To use:

1. Copy the template to the appropriate directory
2. Rename with appropriate filename (remove `-template` suffix)
3. Fill in all sections
4. Add to directory README index
5. Add to mkdocs.yml nav if appropriate

Available templates:
- `runbook-template.md` — For deployment procedures
- `playbook-template.md` — For operational tasks
- `incident-template.md` — For incident postmortems
- `decision-template.md` — For architectural decisions
```

### 6.3 Add cross-repo linking section

(From Phase 4)

**Deliverables:**
- Guidelines updated with navigation policy
- Guidelines updated with template usage
- Guidelines updated with cross-repo linking convention

**Exit criteria:** Guidelines reflect current structure and conventions.

---

## Phase 7 — Validation and finalization

**Objective:** Verify all changes and update indexes.

### 7.1 Verify build

```bash
make docs-build
```

Ensure no warnings about broken links or missing files.

### 7.2 Test navigation

- Verify each nav item links to correct file
- Check that critical docs are accessible within 2-3 clicks
- Verify playbook categories are intuitive

### 7.3 Update link checker

Run link checker to find any broken cross-references:

```bash
# If using lychee or similar
lychee docs/**/*.md
```

### 7.4 Archive this plan

Move to `docs/roadmaps/implemented/` when complete.

**Deliverables:**
- Build passes
- Navigation tested
- Links verified
- Plan archived

**Exit criteria:** Documentation is reorganized and all links work.

---

## Risk register (pre-mortem)

- **Risk:** Navigation becomes too deep/complex.
  - **Mitigation:** Keep top-level sections to 7±2; use README discovery for deep content.
- **Risk:** Moving templates breaks existing workflows.
  - **Mitigation:** Update all references; keep templates in predictable location.
- **Risk:** Cross-repo link changes break external references.
  - **Mitigation:** Only change internal references; external links are stable GitHub URLs.
- **Risk:** Over-engineering the navigation.
  - **Mitigation:** Focus on high-impact changes; preserve README index pattern.

---

## Summary of changes

### File movements

| From | To |
|------|-----|
| `deployment/runbook-template.md` | `_templates/runbook-template.md` |
| `operations/playbooks/playbook-template.md` | `_templates/playbook-template.md` |
| `operations/incidents/incident-template.md` | `_templates/incident-template.md` |
| `decisions/decision-template.md` | `_templates/decision-template.md` |
| `operations/*-template.md` (4 files) | `_templates/*-template.md` |

### Navigation additions (mkdocs.yml)

| Section | Added docs |
|---------|-----------|
| Deployment | production-single-vps, environments-and-configuration, hosting-and-live-server-to-dos |
| Development | live-testing, dev-environment-setup, testing-guidelines |
| Operations | monitoring-and-ci-checklist, ops-cadence-checklist, risk-register |
| Playbooks | operator-responsibilities, deploy-and-verify, incident-response + category groupings |
| Roadmaps | future-roadmap |

### Documentation updates

| File | Change |
|------|--------|
| `documentation-guidelines.md` | Navigation policy, template usage, cross-repo linking |
| `operations/README.md` | Audience-based quick start |
| `operations/playbooks/README.md` | Category groupings |
| `README.md` (home) | Role-based quick start |

---

## Appendix: Proposed mkdocs.yml nav structure

```yaml
nav:
  - Home: README.md
  - Project: project.md
  - Operations:
      - Overview: operations/README.md
      - Ops Roadmap: operations/healtharchive-ops-roadmap.md
      - Monitoring: operations/monitoring-and-ci-checklist.md
      - Ops Cadence: operations/ops-cadence-checklist.md
      - Risk Register: operations/risk-register.md
      - Baseline Drift: operations/baseline-drift.md
      - Dataset Release: operations/dataset-release-runbook.md
      - Incidents:
          - Overview: operations/incidents/README.md
          - Storage Hotpath: operations/incidents/2026-01-08-storage-hotpath-sshfs-stale-mount.md
          - Annual Crawl Stalled: operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md
          - PHAC Permission Denied: operations/incidents/2026-01-09-annual-crawl-phac-output-dir-permission-denied.md
          - Replay Smoke 503: operations/incidents/2026-01-16-replay-smoke-503-and-warctieringfailed.md
      - Playbooks:
          - Overview: operations/playbooks/README.md
          - Core:
              - Operator Responsibilities: operations/playbooks/operator-responsibilities.md
              - Deploy & Verify: operations/playbooks/deploy-and-verify.md
              - Incident Response: operations/playbooks/incident-response.md
          - Observability:
              - Bootstrap: operations/playbooks/observability-bootstrap.md
              - Alerting: operations/playbooks/observability-alerting.md
          - Storage:
              - WARC Tiering: operations/playbooks/warc-storage-tiering.md
  - Backend:
      - Architecture: architecture.md
      - Decisions: decisions/README.md
      - Deployment:
          - Overview: deployment/README.md
          - Production Runbook: deployment/production-single-vps.md
          - Configuration: deployment/environments-and-configuration.md
          - Checklist: deployment/hosting-and-live-server-to-dos.md
      - Development:
          - Overview: development/README.md
          - Live Testing: development/live-testing.md
          - Dev Setup: development/dev-environment-setup.md
          - Testing: development/testing-guidelines.md
      - Guidelines: documentation-guidelines.md
  - API: api.md
  - Frontend:
      - Overview: frontend-external/README.md
      - I18n: frontend-external/i18n.md
      - Implementation: frontend-external/implementation-guide.md
  - Datasets: datasets-external/README.md
  - Roadmaps:
      - Overview: roadmaps/README.md
      - Backlog: roadmaps/future-roadmap.md
```

This structure:
- Keeps top-level sections to 8 items (within 7±2 guideline)
- Exposes critical docs directly in navigation
- Groups playbooks by category
- Preserves README index pattern for deep discovery
- Maintains existing strengths while improving discoverability
