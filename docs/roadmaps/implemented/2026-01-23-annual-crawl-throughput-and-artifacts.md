# Annual crawl throughput and WARC-first artifacts (implemented, 2026-01-23)

Status: implemented

## Goal

Increase annual crawl throughput on the single production VPS while staying aligned with campaign values:

- completeness-first within explicit scope boundaries
- accuracy and reproducibility
- search-first readiness (WARCs indexed ASAP)

## Constraints

- Production: Hetzner `cx33` (4 vCPU / 8GB RAM / 80GB SSD)
- Optional StorageBox is for cold storage/tiering, not crawl hot-path I/O.

## Changes implemented

### 1) WARC-first annual pipeline (skip optional ZIM build)

- Added `archive_tool` support for skipping the final `--warcs` ZIM stage:
  - `archive-tool --skip-final-build`
- Wired through DB job config:
  - `tool_options.skip_final_build = true`
- Annual source defaults now set `skip_final_build=true` so the crawl exits successfully once WARCs are produced, enabling indexing to start sooner.

Rationale:

- The backend indexes WARCs; `.zim` is an optional artifact and is not required for annual “done”.

### 2) Container `/dev/shm` tuning for stability

- Added `archive-tool --docker-shm-size <value>` and pass through to `docker run --shm-size`.
- Annual defaults set `docker_shm_size="1g"` for browser-driven crawl stability.

### 3) Modest parallelism on the single VPS

- Annual defaults increased to `initial_workers=2` for all three v1 sources.
- `canada.ca` sources default `stall_timeout_minutes=60` to avoid false-stall recoveries on long-tail pages.

### 4) Reduce duplicate/trap-like URL expansion on shared-host canada.ca

- Hardened the allowlist regexes for `hc` and `phac` content paths to exclude querystring/fragment variants (assets remain permissive).

## Docs updated

- Archive tool reference and internals docs for new flags.
- Annual campaign doc clarified WARC-first/search-first posture and PDF indexing non-goal for v1.
- Production runbook includes swap recommendation and “local SSD hot-path” guidance.
- Decision record captured in `docs/decisions/2026-01-23-annual-crawl-throughput-and-artifacts.md`.

## Verification

- Repo checks: `make ci` (ruff, mypy, pytest).
- Tests updated for:
  - new default tool options
  - canada.ca scope regex expectations
  - auto-recover tool option behavior

## Follow-ups (ops)

- Deploy the updated backend and restart the worker on production.
- For already-created annual jobs, ensure their `tool_options` reflect the desired values if you want them to take effect on the next retry/recovery cycle.

## References

- Decision record: `../decisions/2026-01-23-annual-crawl-throughput-and-artifacts.md`
- Annual campaign scope: `../operations/annual-campaign.md`
- Production runbook: `../deployment/production-single-vps.md`
