# Documentation Guidelines (internal)

Keep documentation accurate, minimal, and easy to maintain across the app
monorepo and the separate datasets repo.

## Canonical sources

- Docs portal (published): https://jerdaw.github.io/healtharchive/
- Docs portal (local): Run `make docs-serve` in the backend repo root.
- Current site navigation config: `mkdocs.yml` (current source of truth for
  sidebar structure; update this reference when the docs platform changes).
- Public deployment/operations boundary summaries: `docs/deployment/README.md`
  and `docs/operations/README.md`
- Private deployment wiring, private project notes, and ops roadmap/todo:
  private/shared operations source of truth
- Future roadmap backlog (not-yet-implemented work): `docs/planning/roadmap.md`
- Implemented plans archive (historical records): `docs/planning/implemented/`
- Docs-platform migration prep inventory: `docs/planning/implemented/2026-04-15-zensical-migration-prep.md`
- Frontend documentation (canonical files): https://github.com/jerdaw/healtharchive/tree/main/frontend/docs
- Datasets documentation (canonical): https://github.com/jerdaw/healtharchive-datasets

## App/docs boundary (avoid bleed)

This documentation site is built from the repo-root `docs/` tree.

- Frontend docs are canonical in `frontend/docs/**` and are surfaced in the
  portal through the `docs/frontend/**` bridge. Edit the canonical files under
  `frontend/docs/**`.
- Datasets docs are canonical in the datasets repo and should be linked-to, not copied into this site.
- Frontend PRs should not break backend docs builds (and vice versa).

## Cross-surface linking (avoid drift)

When referencing frontend paths or another repo from docs in this repo:

- **For documentation references**: Use GitHub URLs
  ```markdown
  # Good
  See the [frontend i18n guide](https://github.com/jerdaw/healtharchive/blob/main/frontend/docs/i18n.md)

  # Avoid
  See `frontend/docs/i18n.md`
  ```

- **For command examples**: Workspace-relative paths are fine
  ```bash
  # This is appropriate in a development guide
  cd frontend && npm ci
  ```

- **For project names in prose**: Use simple names
  ```markdown
  The in-tree frontend under `frontend/` handles the public UI.
  ```

- Treat cross-repo references as pointers. Do not copy text across repos unless it is an intentional public-safe excerpt.
- Links to backend docs can use relative paths within this repo or URLs under `https://jerdaw.github.io/healtharchive/`.

### External pointer pages

If you want frontend or another repo’s docs to be discoverable from the docs
portal, surface them through a stable bridge path under `docs/` and add that
path to the current docs-site navigation config.
Do not maintain copied duplicate content when a bridge or canonical link is
enough.

## Navigation policy

### What goes in the current docs-site nav

- All README index pages
- Docs that are frequently accessed or critical for public understanding,
  methodology, local development, and contribution work
- At least one representative doc from each major category
- Public deployment/operations boundary summaries

### What stays README-only

- Public-safe specialist docs that are useful but not part of the primary reader path
- Operator runbooks, deployment procedures, and response playbooks belong in
  the private operations workspace. Public tracked copies should be high-level
  summaries or boundary stubs.
- Historical/archived roadmaps (implemented/)
- Log files and templates
- Highly specialized procedures

### Organizing new docs

When adding new docs:

1. Add to the appropriate directory
2. Update the directory's `README.md` index
3. If critical or frequently accessed, add to the current docs-site nav config
4. Ensure cross-links from related docs

## Using templates

Templates are stored in `docs/_templates/`. To use:

1. Copy the template to the appropriate directory
2. Rename with appropriate filename (remove `-template` suffix)
3. Fill in all sections
4. Add to directory README index
5. Add to the current docs-site nav config if appropriate

Available templates:

- `_templates/runbook-template.md` — For deployment procedures
- `_templates/playbook-template.md` — For operational tasks
- `_templates/incident-template.md` — For incident postmortems
- `_templates/decision-template.md` — For architectural decisions
- `_templates/restore-test-log-template.md` — For quarterly restore test logs
- `_templates/adoption-signals-log-template.md` — For event-triggered,
  independently observable public evidence; inactive by default
- `_templates/mentions-log-template.md` — For mentions log entries
- `_templates/ops-ui-friction-log-template.md` — For internal friction logging

## When adding or changing docs

- Prefer one canonical source. Use pointers elsewhere instead of copying text.
- Keep docs close to the code they describe.
- **Registry**: New critical docs should be added to the current docs-site nav
  config (today: `mkdocs.yml`). All docs should be added to their directory's
  `README.md` index.
- Treat generator-specific wiring as an implementation detail. Organize content
  so it can survive a docs-platform migration without rewriting the whole docs tree.
- The published portal remains on MkDocs 1.x in the current wave. Do not start
  the Zensical migration here until the shared earlier waves succeed and the
  required plugin parity (`tags`, `social`, `swagger-ui-tag`) is actually
  proven.
- Use docs-site features that the current stack supports, like
  **Admonitions** (`!!! note`), **Tabs**, and **Mermaid** diagrams. Avoid
  leaning on generator-specific behavior when plain Markdown is enough.
- Documentation should be English-only; do not duplicate it in other languages.
- Do not include AI-assistant authorship attribution in docs metadata/prose; document only human authors/contributors.
- Avoid "phase" labels or other implementation-ordering labels outside `docs/planning/roadmap.md` and `docs/planning/implemented/`. The order that something was implemented in is not something that needs documentation; rather documentation should focus on key elements of what was implemented, how it was implemented, and how it is to be used.
- Keep public copy public-safe (no secrets, private emails, or internal IPs).
- Do not treat ignored local `private/` folders as durable documentation;
  private project and operations notes belong in the private/shared operations
  source of truth, and actual secrets remain in Bitwarden or deployment
  environments.
- Treat `docs/llms.txt` as a published public artifact. The generator should
  include only public-safe overview, architecture, API, local development, and
  contribution docs; do not include agent instructions, private ops runbooks,
  deployment inventories, or active planning notes.
- `docs/openapi.json` and `docs/llms.txt` are generated, git-ignored local
  outputs. Regenerate them through `make docs-refs`, `make docs-build`, or
  `make docs-serve`; do not hand-edit or commit them. If either output is
  wrong, update the API schema, source docs, or generator script that feeds it.
- Treat path-like inline-code tokens under repository prefixes recognized by
  `make docs-refs` as resolvable repository references: each must exist when
  the checker runs, either because it is tracked or because an earlier target
  step generates it. Bare inline-code artifact or category names that the
  checker does not recognize as repository references may remain illustrative
  and need not resolve. Describe unmanaged local artifacts under a recognized
  repository prefix by category in prose; do not create placeholder files
  solely to satisfy documentation checks.
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
- Examples: `tutorials/first-contribution.md`, `tutorials/architecture-walkthrough.md`, `tutorials/debug-crawl.md`
- Characteristics: Step-by-step, hands-on, designed for learning

**How-To Guides** (Tasks):
- **Runbooks**: Deployment procedures in `docs/deployment/` (template: `_templates/runbook-template.md`)
- **Playbooks**: Operational tasks in `docs/operations/playbooks/` or `docs/development/playbooks/` (template: `_templates/playbook-template.md`)
- **Checklists**: Minimal verification lists
- Characteristics: Goal-oriented, assume some knowledge, focused on results

**Reference** (Information):
- Lives under `docs/reference/` or specialized files (`api.md`, etc.)
- Examples: `reference/data-model.md`, `reference/cli-commands.md`, `reference/archive-tool.md`
- Also: API documentation (`api.md`), Architecture sections
- Characteristics: Factual, precise, structured for lookup

**Explanation** (Understanding):
- **Decision records**: In `docs/decisions/` (template: `_templates/decision-template.md`)
- **Policies/contracts**: Invariants and boundaries
- **Guidelines**: This file, `documentation-process-audit.md`
- **Architecture**: `architecture.md` (blends reference and explanation)
- Characteristics: Background, context, "why" not "how"

### Additional Document Types

These support but don't replace the four main types:

- **Index (`README.md`)**: Navigation only; points to canonical docs
- **Log/record**: Dated, append-only operational or organic public evidence;
  adoption evidence is recorded only when a verifiable event occurs, not on a
  standing cadence
- **Template**: Scaffolds in `docs/_templates/`
- **Bridge/alias**: Short files or bridge paths that surface canonical docs without duplicating ownership (for example `docs/frontend/` and legacy compatibility aliases)

## Document types (detailed taxonomy)

Use consistent doc types so people know what to expect:

## Quality bar (definition of done)

For anything procedural (runbook/playbook/checklist) that is public-safe, include:

- **Purpose**: why this doc exists and what it covers.
- **Audience + access**: who should run it, without naming private production accounts or access paths.
- **Preconditions**: required state and inputs, using placeholders for private runtime paths, env files, and service names.
- **Steps**: explicit commands for local/dev workflows; public production docs should link to private operator material instead of publishing live commands.
- **Verification**: what “done” means (health checks, drift check, smoke tests).
- **Safety**: common public-safe footguns and continuity notes; detailed restoration procedures stay private.
- **References**: links to canonical docs, incident notes, or roadmaps.

For anything public-facing (policy pages, changelog, partner kit):

- Keep it **public-safe** (no secrets/emails/internal hostnames; avoid sensitive incident details).
- Prefer stable claims tied to stable artifacts (URLs, tags, filenames, commit SHAs).
- Record meaningful changes in the public changelog:
  - Process: https://github.com/jerdaw/healtharchive/blob/main/frontend/docs/changelog-process.md

## Lifecycle (avoid drift)

Docs should reflect **current reality**. If something is intentionally outdated:

- Put a short note at the top: what changed, and where the new canonical doc lives.
- Prefer updating the doc over adding a second “new doc” (avoid forks).
- For long historical artifacts, move them under `docs/planning/implemented/` (dated).

Suggested cadence (keep it lightweight):

- **After any production change**: update the private runbook/playbook and the public-safe summary if user, contributor, or methodology expectations changed.
- **After sev0/sev1 incidents**: ensure private response notes capture restoration details, and public follow-ups exist when user expectations changed.
- **Quarterly**: review the public deployment/operations summaries and the private operator playbooks for drift.

## Roadmap workflow

This project separates **backlog** vs **implementation plans** vs **canonical docs** to reduce drift.

- Short pointer (for new contributors): `roadmap-process.md`
- `docs/planning/roadmap.md` is the single backlog of not-yet-implemented items.
- When you start work, create a focused implementation plan under `docs/planning/`.
- When the work is done, update canonical public docs and any private operations docs needed to keep the result maintainable.
- For production-impacting work, use the private production closeout checklist before declaring the work closed; record only public-safe outcomes in tracked docs.
- Then move the implementation plan into `docs/planning/implemented/` with a dated filename.

Rule of thumb: documentation should describe **what exists and how to use/operate it**, not the order it was implemented.

## Implementation plan lifecycle

Implementation plans follow a specific lifecycle to prevent documentation sprawl:

### 1. Active planning
Create in `docs/planning/` with dated filename: `YYYY-MM-DD-<short-slug>.md`

### 2. During implementation
Update with progress notes as needed. Keep the focus on outcomes and decisions.

### 3. After completion
- Update all canonical docs (deployment/operations/development) with outcomes
- Run the production closeout checklist for production-impacting work
- Compress the plan to summary format (see below)
- Move to `docs/planning/implemented/`

### 4. Compressed format for implemented plans

Completed plans >200 lines should be compressed to this format (~40-80 lines):

```markdown
# [Title] (Implemented YYYY-MM-DD)

**Status:** Implemented | **Scope:** [1-2 sentences describing what was done]

## Outcomes
- [Bullet list of what was delivered]

## Canonical Docs Updated
- [Links to docs that now contain this information]

## Decisions Created (if any)
- [Links to decision records]

## Historical Context
[Brief note that detailed implementation history is preserved in git]
```

**Why compress?** Detailed phase-by-phase narratives become historical journals that duplicate content now in canonical docs. Compression:
- Reduces maintenance burden
- Prevents documentation sprawl
- Preserves outcomes while keeping the archive scannable

**Detailed history:** Git preserves the full implementation narrative for anyone who needs it.

## Naming and organization

- Use descriptive filenames (`runbook`, `checklist`, `guidelines`) and avoid phase prefixes.
- File titles and filenames should reflect the document’s actual purpose and content. If the purpose or content changes, rename the file and update links as needed.
- Put roadmaps and active implementation plans in `docs/planning`.
- Move completed implementation plans into `docs/planning/implemented/` (dated).
- Put public-safe operational summaries in `docs/operations`.
- Put public-safe incident summaries in `docs/operations/incidents/` only when disclosure is intentional.
- Put public-safe ops playbook summaries in `docs/operations/playbooks/`; detailed operator checklists stay private.
- Put public-safe deployment summaries in `docs/deployment`; live deployment runbooks stay private.
- Put developer workflows (local setup, testing, debugging) in `docs/development`.
- Put dev playbooks (task workflows) in `docs/development/playbooks/`.
