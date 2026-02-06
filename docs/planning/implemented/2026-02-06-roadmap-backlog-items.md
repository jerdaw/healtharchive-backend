# Roadmap Backlog Items Implementation

**Date**: 2026-02-06
**Status**: Completed
**Implementation time**: ~20 hours (estimated)

## Summary

Implemented four roadmap items that improved crawl/storage reliability and monitoring without compromising data integrity:

1. WARC discovery consistency (single shared implementation across scripts + CLI + indexing)
2. Annual crawl guardrails (refuse to crawl if annual outputs are still on root disk)
3. Canary replay job (separates replay failures from tiering/storage failures)
4. Same-day snapshot deduplication (dry-run by default; reversible)

## Shipped changes (high level)

- WARC discovery: unified WARC discovery logic and removed script drift risk.
- Guardrails: worker safety checks prevent annual crawls from filling `/` if tiering fails.
- Canary replay: a tiny non-tiered job improves replay smoke signal quality.
- Deduplication: adds a reversible dedupe flag + audit trail; search excludes deduped by default (opt-in to include).

## Operator notes

- Canary job setup (optional): `ha-backend create-canary-job`
- Deduplication is dry-run by default:
  - Dry-run: `ha-backend dedupe-snapshots --id <job_id>`
  - Apply: `ha-backend dedupe-snapshots --id <job_id> --apply`
  - Restore: `ha-backend restore-deduped-snapshots --id <job_id> --apply`

## Migration

- The deduplication schema is shipped via Alembic and is required for production:
  - `alembic/versions/0014_snapshot_deduplication.py`

## Verification

- Local: `make ci` passes.
- VPS: deploy script runs Alembic upgrades and the public surface verifier exercises `/api/search` and `/api/changes`.

---

## Related Documents

- Roadmap: `../roadmap.md` (updated to remove completed items)
- WARC discovery plan: `2026-01-29-warc-discovery-consistency.md`
- Annual disk incident: `../operations/incidents/2026-02-04-annual-crawl-output-dirs-on-root-disk.md`
- Search quality: `../operations/search-quality.md`
- Growth constraints: `../operations/growth-constraints.md`
