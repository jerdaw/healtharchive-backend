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

When you reference another repo from docs in this repo:

- Prefer **GitHub links** (or `docs.healtharchive.ca` links for backend docs) over local workspace-relative paths.
- Treat cross-repo references as pointers. Do not copy text across repos unless it is an intentional public-safe excerpt.

### External pointer pages

If you want another repo’s docs to be discoverable from the docs portal, add a
small pointer page under `docs/*-external/` and add it to `mkdocs.yml` `nav`.
Do not mirror the other repo’s docs into this site.

## When adding or changing docs

- Prefer one canonical source. Use pointers elsewhere instead of copying text.
- Keep docs close to the code they describe.
- **Registry**: New docs must be added to the `nav` section of `mkdocs.yml` to appear in the sidebar navigation.
- Use MkDocs Material features like **Admonitions** (`!!! note`), **Tabs**, and **Mermaid** diagrams.
- Documentation should be English-only; do not duplicate it in other languages.
- Avoid "phase" labels or other implementation-ordering labels outside `docs/roadmaps` and explicit implementation plans. The order that something was implemented in is not something that needs documentation; rather documentation should focus on key elements of what was implemented, how it was implemented, and how it is to be used.
- Keep public copy public-safe (no secrets, private emails, or internal IPs).
- If you sync your workspace via Syncthing, treat `.stignore` as “sync ignore” (like `.gitignore`) and ensure it excludes build artifacts and machine-local dev artifacts (e.g., `.venv/`, `node_modules/`, `.dev-archive-root/`). Secrets may sync via Syncthing, but must remain git-ignored.

## Document types (taxonomy)

Use consistent doc types so people know what to expect:

- **Index (`README.md`)**: navigation only; points to canonical docs.
- **Runbook**: step-by-step operational procedure (deploy, restore, rebuild).
  - Lives under `docs/deployment/`.
  - Template: `docs/deployment/runbook-template.md`
- **Playbook**: short, task-oriented checklist (“first 3 commands”).
  - Lives under `docs/operations/playbooks/` (or `docs/development/playbooks/` for dev workflows).
  - Template: `docs/operations/playbooks/playbook-template.md`
- **Decision record (ADR-lite)**: high-stakes choices that should remain legible over time (security/privacy/public surface/invariants).
  - Lives under `docs/decisions/`.
  - Template: `docs/decisions/decision-template.md`
- **Checklist**: a minimal verification list (often used by a runbook/playbook).
- **Policy / contract**: invariants and boundaries that should not drift (security posture, public/private boundary, export immutability).
- **Log / record**: dated, append-only operational evidence (restore tests, adoption signals, mentions).
- **Template**: a scaffold used to create logs or incident notes.
- **Pointer**: a short file that links to the canonical doc (avoid copying text).

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
