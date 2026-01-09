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
  - `healtharchive-backend/docs/deployment/**`
  - `healtharchive-backend/docs/operations/**`
- Backend architecture and developer workflows:
  - `healtharchive-backend/docs/architecture.md`
  - `healtharchive-backend/docs/development/**`
- Public reporting surfaces (status/impact/changelog) live in the frontend codebase:
  - Changelog SOP: `healtharchive-frontend/docs/changelog-process.md`
  - Status page: `healtharchive-frontend/src/app/[locale]/status/page.tsx`
  - Impact page: `healtharchive-frontend/src/app/[locale]/impact/page.tsx`
- Dataset releases are published from the datasets repo (automation + integrity):
  - `healtharchive-datasets/README.md`

## Local workspace note

Some docs refer to sibling repos using paths like `healtharchive-frontend/...`.
Those references assume a local “sibling repos” workspace (like
`/home/jer/LocalSync/healtharchive/`). When browsing a single repo on GitHub,
use the GitHub links above to navigate instead.

