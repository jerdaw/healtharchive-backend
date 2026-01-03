# Documentation Guidelines (internal)

Keep documentation accurate, minimal, and easy to maintain across repos.

## Canonical sources

- Cross-repo environment wiring: `healtharchive-backend/docs/deployment/environments-and-configuration.md`
- Ops roadmap/todo: `healtharchive-backend/docs/operations/healtharchive-ops-roadmap.md`
  - Keep the non-git copy in `/home/jer/LocalSync/healtharchive/docs/operations/healtharchive-ops-roadmap.md` synced.
- Public partner kit and data dictionary:
  - `healtharchive-frontend/public/partner-kit/healtharchive-brief.md`
  - `healtharchive-frontend/public/partner-kit/healtharchive-citation.md`
  - `healtharchive-frontend/public/exports/healtharchive-data-dictionary.md`
- Future roadmap backlog (not-yet-implemented work): `healtharchive-backend/docs/roadmaps/future-roadmap.md`
- Implemented plans archive (historical records): `healtharchive-backend/docs/roadmaps/implemented/`
- Historical upgrade program (archived): `healtharchive-backend/docs/roadmaps/implemented/2025-12-24-6-phase-upgrade-roadmap-2025.md`

## When adding or changing docs

- Prefer one canonical source. Use pointers elsewhere instead of copying text.
- Keep docs close to the code they describe; if cross-repo, link to the canonical doc.
- Update the relevant docs index (`docs/README.md` or `docs/operations/README.md`).
- Documentation should be English-only; do not duplicate it in other languages.
- Avoid "phase" labels or other implementation-ordering labels outside `docs/roadmaps` and explicit implementation plans. The order that something was implemented in is not something that needs documentation; rather documentation should focus on key elements of what was implemented, how it was implemented, and how it is to be used.
- Keep public copy public-safe (no secrets, private emails, or internal IPs).
- If you sync your workspace via Syncthing, treat `.stignore` as “sync ignore” (like `.gitignore`) and ensure it excludes build artifacts and machine-local dev artifacts (e.g., `.venv/`, `node_modules/`, `.dev-archive-root/`). Secrets may sync via Syncthing, but must remain git-ignored.


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
- Put ops playbooks (task-oriented checklists) in `docs/operations/playbooks/`.
- Put deployment/runbooks in `docs/deployment`.
- Put developer workflows (local setup, testing, debugging) in `docs/development`.
- Put dev playbooks (task workflows) in `docs/development/playbooks/`.
