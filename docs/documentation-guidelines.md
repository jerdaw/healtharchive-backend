# Documentation Guidelines (internal)

Keep documentation accurate, minimal, and easy to maintain across repos.

## Canonical sources

- Docs portal (published): https://docs.healtharchive.ca
- Docs portal (local): Run `make docs-serve` in the backend repo root.
- Navigation config: `mkdocs.yml` (source of truth for sidebar structure).
- Cross-repo environment wiring: `docs/deployment/environments-and-configuration.md`
- Ops roadmap/todo: `docs/operations/healtharchive-ops-roadmap.md`
- Future roadmap backlog (not-yet-implemented work): `docs/roadmaps/future-roadmap.md`
- Implemented plans archive (historical records): `docs/roadmaps/implemented/`
- Frontend documentation (canonical): https://github.com/jerdaw/healtharchive-frontend/tree/main/docs
- Datasets documentation (canonical): https://github.com/jerdaw/healtharchive-datasets

## Multi-repo boundary (avoid bleed)

This documentation site is built from the **backend repo only**.

- Frontend docs are canonical in the frontend repo (`docs/**`) and should be linked-to, not copied into this site.
- Datasets docs are canonical in the datasets repo and should be linked-to, not copied into this site.
- Frontend PRs should not break backend docs builds (and vice versa).

## Cross-repo linking (avoid drift)

When referencing another repo from docs in this repo:

- **For documentation references**: Use GitHub URLs
  ```markdown
  # Good
  See the [frontend i18n guide](https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/i18n.md)

  # Avoid
  See `healtharchive-frontend/docs/i18n.md`
  ```

- **For command examples**: Workspace-relative paths are fine
  ```bash
  # This is appropriate in a development guide
  cd ../healtharchive-frontend && npm ci
  ```

- **For project names in prose**: Use simple names
  ```markdown
  The healtharchive-frontend repository handles the public UI.
  ```

- Treat cross-repo references as pointers. Do not copy text across repos unless it is an intentional public-safe excerpt.
- Links to backend docs can use relative paths within this repo or `docs.healtharchive.ca` URLs.

### External pointer pages

If you want another repo’s docs to be discoverable from the docs portal, add a
small pointer page under `docs/*-external/` and add it to `mkdocs.yml` `nav`.
Do not mirror the other repo’s docs into this site.

## Navigation policy

### What goes in mkdocs.yml nav

- All README index pages
- Docs that are frequently accessed or critical for operations
- At least one representative doc from each major category
- Core playbooks (operator responsibilities, deploy & verify, incident response)

### What stays README-only

- Detailed playbooks beyond the core set (discoverable via playbooks/README.md)
- Historical/archived roadmaps (implemented/)
- Log files and templates
- Highly specialized procedures

### Organizing new docs

When adding new docs:

1. Add to the appropriate directory
2. Update the directory's `README.md` index
3. If critical or frequently accessed, add to `mkdocs.yml` nav
4. Ensure cross-links from related docs

## Using templates

Templates are stored in `docs/_templates/`. To use:

1. Copy the template to the appropriate directory
2. Rename with appropriate filename (remove `-template` suffix)
3. Fill in all sections
4. Add to directory README index
5. Add to `mkdocs.yml` nav if appropriate

Available templates:

- `runbook-template.md` — For deployment procedures
- `playbook-template.md` — For operational tasks
- `incident-template.md` — For incident postmortems
- `decision-template.md` — For architectural decisions
- `restore-test-log-template.md` — For quarterly restore test logs
- `adoption-signals-log-template.md` — For quarterly adoption signals
- `mentions-log-template.md` — For mentions log entries
- `ops-ui-friction-log-template.md` — For internal friction logging

## When adding or changing docs

- Prefer one canonical source. Use pointers elsewhere instead of copying text.
- Keep docs close to the code they describe.
- **Registry**: New critical docs should be added to the `nav` section of `mkdocs.yml`. All docs should be added to their directory's `README.md` index.
- Use MkDocs Material features like **Admonitions** (`!!! note`), **Tabs**, and **Mermaid** diagrams.
- Documentation should be English-only; do not duplicate it in other languages.
- Avoid "phase" labels or other implementation-ordering labels outside `docs/roadmaps` and explicit implementation plans. The order that something was implemented in is not something that needs documentation; rather documentation should focus on key elements of what was implemented, how it was implemented, and how it is to be used.
- Keep public copy public-safe (no secrets, private emails, or internal IPs).
- If you sync your workspace via Syncthing, treat `.stignore` as "sync ignore" (like `.gitignore`) and ensure it excludes build artifacts and machine-local dev artifacts (e.g., `.venv/`, `node_modules/`, `.dev-archive-root/`). Secrets may sync via Syncthing, but must remain git-ignored.

## Documentation framework (Diátaxis)

HealthArchive documentation follows the **Diátaxis framework** for clarity and user-centered organization. Diátaxis divides documentation into four types based on user needs:

### Four Documentation Types

| Type | Purpose | User Action | Examples |
|------|---------|-------------|----------|
| **Tutorials** | Learning-oriented | Following steps to gain skills | First contribution guide, architecture walkthrough |
| **How-To Guides** | Task-oriented | Solving specific problems | Playbooks, runbooks, checklists |
| **Reference** | Information-oriented | Looking up details | API docs, CLI reference, data model |
| **Explanation** | Understanding-oriented | Understanding concepts | Architecture, decisions, guidelines |

**Key principle**: Keep these types separate. Don't mix tutorials with reference material, or how-to guides with explanations.

**Learn more**: [diataxis.fr](https://diataxis.fr/)

### Mapping to Our Taxonomy

Our existing document types map to Diátaxis categories:

**Tutorials** (Learning):
- Lives under `docs/tutorials/`
- Examples: `first-contribution.md`, `architecture-walkthrough.md`, `debug-crawl.md`
- Characteristics: Step-by-step, hands-on, designed for learning

**How-To Guides** (Tasks):
- **Runbooks**: Deployment procedures in `docs/deployment/` (template: `runbook-template.md`)
- **Playbooks**: Operational tasks in `docs/operations/playbooks/` or `docs/development/playbooks/` (template: `playbook-template.md`)
- **Checklists**: Minimal verification lists
- Characteristics: Goal-oriented, assume some knowledge, focused on results

**Reference** (Information):
- Lives under `docs/reference/` or specialized files (`api.md`, etc.)
- Examples: `data-model.md`, `cli-commands.md`, `archive-tool.md`
- Also: API documentation (`api.md`), Architecture sections
- Characteristics: Factual, precise, structured for lookup

**Explanation** (Understanding):
- **Decision records**: In `docs/decisions/` (template: `decision-template.md`)
- **Policies/contracts**: Invariants and boundaries
- **Guidelines**: This file, `documentation-process-audit.md`
- **Architecture**: `architecture.md` (blends reference and explanation)
- Characteristics: Background, context, "why" not "how"

### Additional Document Types

These support but don't replace the four main types:

- **Index (`README.md`)**: Navigation only; points to canonical docs
- **Log/record**: Dated, append-only operational evidence (restore tests, adoption signals)
- **Template**: Scaffolds in `docs/_templates/`
- **Pointer**: Short files linking to canonical docs (e.g., `frontend-external/`)

## Document types (detailed taxonomy)

Use consistent doc types so people know what to expect:

## Quality bar (definition of done)

For anything procedural (runbook/playbook/checklist), include:

- **Purpose**: why this doc exists and what it covers.
- **Audience + access**: who should run it, and from where (local vs VPS; `haadmin` vs `root`).
- **Preconditions**: required state and inputs (paths, env vars, service names).
- **Steps**: explicit commands (prefer stable scripts), ordered, with “what this changes”.
- **Verification**: what “done” means (health checks, drift check, smoke tests).
- **Safety**: common footguns, irreversible actions, and rollback/recovery notes.
- **References**: links to canonical docs, incident notes, or roadmaps.

For anything public-facing (policy pages, changelog, partner kit):

- Keep it **public-safe** (no secrets/emails/internal hostnames; avoid sensitive incident details).
- Prefer stable claims tied to stable artifacts (URLs, tags, filenames, commit SHAs).
- Record meaningful changes in the public changelog:
  - Process: https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/changelog-process.md

## Lifecycle (avoid drift)

Docs should reflect **current reality**. If something is intentionally outdated:

- Put a short note at the top: what changed, and where the new canonical doc lives.
- Prefer updating the doc over adding a second “new doc” (avoid forks).
- For long historical artifacts, move them under `docs/roadmaps/implemented/` (dated).

Suggested cadence (keep it lightweight):

- **After any production change**: update the relevant runbook/playbook and keep deploy/verify steps accurate.
- **After sev0/sev1 incidents**: ensure recovery steps are captured and follow-ups exist (roadmap or TODOs).
- **Quarterly**: skim the production runbook + incident response playbook and fix any drift discovered during routine ops.

## Roadmap workflow

This project separates **backlog** vs **implementation plans** vs **canonical docs** to reduce drift.

- Short pointer (for new contributors): `roadmap-process.md`
- `docs/roadmaps/future-roadmap.md` is the single backlog of not-yet-implemented items.
- When you start work, create a focused implementation plan under `docs/roadmaps/`.
- When the work is done, update canonical docs (deployment/ops/dev) so the result is maintainable.
- Then move the implementation plan into `docs/roadmaps/implemented/` with a dated filename.

Rule of thumb: documentation should describe **what exists and how to use/operate it**, not the order it was implemented.

## Naming and organization

- Use descriptive filenames (`runbook`, `checklist`, `guidelines`) and avoid phase prefixes.
- File titles and filenames should reflect the document’s actual purpose and content. If the purpose or content changes, rename the file and update links as needed.
- Put roadmaps and active implementation plans in `docs/roadmaps`.
- Move completed implementation plans into `docs/roadmaps/implemented/` (dated).
- Put operational procedures in `docs/operations`.
- Put incident notes / lightweight postmortems in `docs/operations/incidents/` (template: `docs/operations/incidents/incident-template.md`).
- Put ops playbooks (task-oriented checklists) in `docs/operations/playbooks/`.
- Put deployment/runbooks in `docs/deployment`.
- Put developer workflows (local setup, testing, debugging) in `docs/development`.
- Put dev playbooks (task workflows) in `docs/development/playbooks/`.
