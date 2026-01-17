# Project documentation portal (multi-repo)

HealthArchive is a **multi-repo project**. This page is a “you are here” index
so it’s easy to find the right documentation regardless of which repo you’re
currently browsing.

## Repositories

- Backend (ops + runbooks + canonical internal docs):
  - GitHub: https://github.com/jerdaw/healtharchive-backend
  - Docs index: `docs/README.md`
- Frontend (public site UI + copy + i18n + public changelog):
  - GitHub: https://github.com/jerdaw/healtharchive-frontend
  - Docs index: `docs/README.md`
- Datasets (versioned, citable metadata-only dataset releases):
  - GitHub: https://github.com/jerdaw/healtharchive-datasets
  - Readme: `README.md`

## Where things live (source-of-truth map)

- Backend ops, runbooks, and internal procedures:
  - Docs site: https://docs.healtharchive.ca
  - GitHub: https://github.com/jerdaw/healtharchive-backend/tree/main/docs
- Backend architecture and developer workflows:
  - Architecture: https://docs.healtharchive.ca/architecture/
  - Development: https://docs.healtharchive.ca/development/
- Public reporting surfaces (status/impact/changelog) live in the frontend codebase:
  - Changelog SOP: https://github.com/jerdaw/healtharchive-frontend/blob/main/docs/changelog-process.md
  - Status page: https://github.com/jerdaw/healtharchive-frontend/blob/main/src/app/%5Blocale%5D/status/page.tsx
  - Impact page: https://github.com/jerdaw/healtharchive-frontend/blob/main/src/app/%5Blocale%5D/impact/page.tsx
- Dataset releases are published from the datasets repo (automation + integrity):
  - https://github.com/jerdaw/healtharchive-datasets/blob/main/README.md

## Local workspace note

Some docs refer to sibling repos using paths like `healtharchive-frontend/...`.
Those references assume a local “sibling repos” workspace (like
`/home/jer/LocalSync/healtharchive/`). When browsing a single repo on GitHub,
prefer the repo links above.
