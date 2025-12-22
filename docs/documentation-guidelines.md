# Documentation Guidelines (internal)

Keep documentation accurate, minimal, and easy to maintain across repos.

## Canonical sources

-   Cross-repo environment wiring: `healtharchive-backend/docs/deployment/environments-and-configuration.md`
-   Ops roadmap/todo: `healtharchive-backend/docs/operations/healtharchive-ops-roadmap.md`
    -   Keep the non-git copy in `/home/jer/LocalSync/healtharchive/docs/operations/healtharchive-ops-roadmap.md` synced.
-   Public partner kit and data dictionary:
    -   `healtharchive-frontend/public/partner-kit/healtharchive-brief.md`
    -   `healtharchive-frontend/public/partner-kit/healtharchive-citation.md`
    -   `healtharchive-frontend/public/exports/healtharchive-data-dictionary.md`
-   Historical upgrade program: `healtharchive-backend/docs/roadmaps/healtharchive-6-phase-upgrade-2025.md`

## When adding or changing docs

-   Prefer one canonical source. Use pointers elsewhere instead of copying text.
-   Keep docs close to the code they describe; if cross-repo, link to the canonical doc.
-   Update the relevant docs index (`docs/README.md` or `docs/operations/README.md`).
-   Avoid "phase" labels outside `docs/roadmaps` and explicit implementation plans; use "step" or "milestone".
-   Keep public copy public-safe (no secrets, private emails, or internal IPs).
-   If you sync your workspace via Syncthing, treat `.stignore` as “sync ignore” (like `.gitignore`) and ensure it excludes build artifacts and local secrets (e.g., `.env*`, `.venv/`, `node_modules/`).

## Naming and organization

-   Use descriptive filenames (`runbook`, `checklist`, `guidelines`) and avoid phase prefixes.
-   File titles and filenames should reflect the document’s actual purpose and content. If the purpose or content changes, rename the file and update links as needed.
-   Put historical plans in `docs/roadmaps`.
-   Put operational procedures in `docs/operations`.
-   Put deployment/runbooks in `docs/deployment`.
-   Put developer workflows (local setup, testing, debugging) in `docs/development`.
