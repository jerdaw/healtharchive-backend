# WARC Discovery Consistency Improvements (Partial, Updated 2026-01-29)

**Status:** Partially Implemented | **Scope:** Keep WARC discovery and WARC counts coherent across status output, indexing, and cleanup.

## Outcomes (Implemented)

- Added an operator-facing manifest verification command (and associated tests):
  - Plan: `2026-01-29-warc-manifest-verification.md`

## Deferred / Follow-Through

Remaining follow-through work stayed as backlog items (not actively implemented in this plan):

- Unify and formalize discovery return semantics (e.g., a `WarcDiscoveryResult` summary type).
- Improve manifest error handling and reporting.
- Align `scripts/vps-crawl-status.sh` with the canonical Python discovery logic.

Backlog tracker:

- `../roadmap.md` (WARC discovery consistency follow-through)

## Historical Context

Detailed analysis and proposed changes are preserved in git history.
