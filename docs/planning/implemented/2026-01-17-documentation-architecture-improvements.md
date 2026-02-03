# Documentation Architecture Improvements (Implemented 2026-01-17)

**Status:** Implemented | **Scope:** Improve documentation discoverability, navigation, and organization following Diátaxis framework principles.

## Outcomes

- **Template isolation:** Moved 8 templates to `docs/_templates/` directory
- **Expanded navigation:** Critical docs (production runbook, live testing, monitoring) directly accessible in sidebar
- **Playbook categorization:** Grouped 32 playbooks into logical categories (Core, Observability, Crawl, Storage, Validation, External)
- **Cross-repo linking:** Standardized on GitHub URLs for cross-repo references
- **Audience-based entry points:** Added role-based quick start sections to README index pages
- **Navigation coverage:** Increased from ~19% to ~60% of docs in mkdocs.yml

## Canonical Docs Updated

- [documentation-guidelines.md](../../documentation-guidelines.md) — Navigation policy, template usage, cross-repo linking conventions
- [operations/README.md](../../operations/README.md) — Audience-based quick start
- [operations/playbooks/README.md](../../operations/playbooks/README.md) — Category groupings
- [README.md](../../README.md) — Role-based quick start
- `mkdocs.yml` — Expanded navigation structure

## Key Decisions

- **Preserve README index pattern:** Keep READMEs as comprehensive indices; navigation adds direct access to critical docs
- **7±2 rule for top-level:** Keep top-level sections manageable (8 items)
- **Progressive disclosure:** Start with overview, drill down to details via navigation or README links
- **Templates excluded from nav:** `_templates/` for authoring reference, not published content

## Files Moved

Templates were consolidated under `docs/_templates/` (historical file locations are preserved in git history).

| Template | Canonical path |
|----------|----------------|
| Runbook template | `docs/_templates/runbook-template.md` |
| Playbook template | `docs/_templates/playbook-template.md` |
| Incident template | `docs/_templates/incident-template.md` |
| Decision template | `docs/_templates/decision-template.md` |
| Log templates | `docs/_templates/*.md` |

## Historical Context

7-phase implementation (690+ lines) with detailed navigation proposals, Diátaxis framework reference, and MkDocs Material feature analysis. Preserved in git history.
