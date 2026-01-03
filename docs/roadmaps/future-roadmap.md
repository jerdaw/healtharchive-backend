# Future roadmap (backlog)

This file tracks **not-yet-implemented** work and planned upgrades.

It is intentionally **not** an implementation plan.

## How to use this file (workflow)

1. Pick a small set of items from this backlog.
2. Create a focused implementation plan in `docs/roadmaps/` (example name: `YYYY-MM-<topic>.md`).
3. Implement the work.
4. Update canonical documentation so operators/users can run and maintain the result.
5. Move the completed implementation plan to `docs/roadmaps/implemented/` and date it.

## External / IRL work (not implementable in git)

These items are intentionally “external” and require ongoing human follow-through.

- Secure at least 1 distribution partner (permission to name them publicly).
- Secure at least 1 verifier (permission to name them publicly).
- Maintain a public-safe mentions/citations log with real entries (links only; no private contact data).
- Healthchecks.io alignment: create missing checks for new automation timers and keep `/etc/healtharchive/healthchecks.env` in sync with what exists in Healthchecks.
  - See: `../deployment/systemd/README.md` and `../deployment/production-single-vps.md`

Track the current status and next actions in:

- `../operations/healtharchive-ops-roadmap.md`

Supporting materials:

- `../operations/outreach-templates.md`
- `../operations/partner-kit.md`
- `../operations/verification-packet.md`

## Technical backlog (candidates)

Keep this list short; prefer linking to the canonical doc that explains the item.

### Search & relevance (backend)

- Search ranking + snippet quality iteration, with repeatable “golden query” regression captures.
  - Harness + scripts: `../operations/search-quality.md`, `../../scripts/search-eval-run.sh`
- Authority signals for relevance (optional): outlinks / page signals feeding into ranking and/or tie-breakers.
  - See: `../operations/search-quality.md` (“Backfill outlinks + authority signals”)

### Archive UX (frontend)

- “Power controls” for archive/search views (discoverable + shareable via URL params).
  - Params to expose: `view=pages|snapshots`, `includeNon2xx=true`, `includeDuplicates=true`
  - See: `../../../healtharchive-frontend/docs/implementation-guide.md`

### Storage & retention (backend)

- Same-day dedupe path (storage-only optimization; provenance-preserving).
  - Requirements: dry-run mode, reversible/auditable log, and strict invariants (e.g., “same URL, same day, identical `Snapshot.content_hash`”).
  - See: `../operations/search-quality.md`, `../operations/growth-constraints.md`
- Storage/retention upgrades (only with a designed replay retention policy).
  - See: `../operations/growth-constraints.md`, `../deployment/replay-service-pywb.md`

### Reliability & CI (backend + frontend)

- End-to-end smoke coverage in CI for public-critical flows.
  - Target: `/archive`, `/snapshot/[id]`, plus API smoke calls (`/api/search`, `/api/sources`, `/api/snapshot/{id}`)
  - See: `../operations/monitoring-and-ci-checklist.md`

### Ops surface / environments (optional)

- Consider whether a separate staging backend is worth it (increases ops surface; only do if it buys real safety).
  - See: `../deployment/environments-and-configuration.md`

### Repo governance (future)

- Tighten GitHub merge discipline when there are multiple committers (PR-only + required checks).
  - See: `../operations/monitoring-and-ci-checklist.md`

### Dataset releases (healtharchive-datasets)

- Release pipeline hardening (more reproducible, less flaky).
  - Add retries/backoff, manifest validation, and checksum verification as required pre-publish steps.
  - See: `../../../healtharchive-datasets/README.md`, `../../../healtharchive-datasets/.github/workflows/publish-dataset-release.yml`

## Adjacent / optional (in this monorepo, not core HA)

- `rcdc/CDC_zim_mirror`: add startup DB sanity checks and clearer failure modes (empty/invalid LevelDB, missing prefixes, etc.).
