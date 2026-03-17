# Repo Governance and CI Maintenance (Implemented 2026-03-17)

## Summary

Completed a small cross-repo governance and CI maintenance pass covering
frontend, backend, and datasets hygiene.

## What Changed

- Normalized git authorship display with `.mailmap` in all three repos.
  - Canonicalized historical `Jeremy Dawson`, `jerdaw`, and dataset `CI User`
    commit identities to a human-only canonical author.
- Normalized agent tool compatibility and attribution policy in datasets.
  - Added explicit human-only authorship guidance to `healtharchive-datasets/AGENTS.md`.
  - Added `GEMINI.md -> AGENTS.md` symlink in the datasets repo.
- Reduced automated GitHub maintenance noise while preserving CI safety.
  - Backend docs workflow now installs the docs dependency set before `make docs-build`.
  - Canonical CI/Dependabot documentation now reflects the current frontend and
    backend required checks and the patch/minor-only Dependabot auto-merge policy.
  - Removed the completed `.mailmap` item from the future roadmap backlog.
- Cleaned up unused frontend GitHub Pages publishing at the repo-settings level.
  - HealthArchive frontend is Vercel-hosted, so GitHub Pages/Jekyll builds were
    pure noise and were disabled.

## Canonical Docs Updated

- `docs/operations/monitoring-and-ci-checklist.md`
- `docs/planning/roadmap.md`

## Verification

- Local repo status checked across backend, frontend, and datasets before edits.
- Relevant local checks were re-run after edits.
- GitHub Actions runs were inspected for the affected workflows.
- Open Dependabot and maintainer PR state was reviewed with `gh`.

## Follow-up Notes

- Existing active planning docs under `docs/planning/` remain active because they
  still include operator-run work or broader external work that is not complete.
- Frontend major-version Dependabot PRs remain manual by policy and should be
  handled case-by-case after CI and release-note review.
