# Decision: Annual crawl throughput and WARC-first artifacts (2026-01-23)

Status: accepted

## Context

- HealthArchive’s annual campaign is **completeness-first** within explicit scope boundaries and **search-first** for readiness.
- Production runs on a **single VPS** (Hetzner `cx33`: 4 vCPU / 8GB RAM / 80GB SSD) with optional StorageBox for cold storage.
- The backend indexes **WARCs** into `Snapshot` rows; it does not read `.zim` files. Building ZIMs during the campaign adds wall-clock time and failure surface without improving “search readiness”.
- Large, browser-driven crawls (notably `canada.ca`) benefit from modest parallelism and a larger container `/dev/shm` to reduce timeouts/stalls and avoid restart churn.

## Decision

- Annual campaign jobs will default to **modest crawl parallelism** on the single VPS:
  - `tool_options.initial_workers = 2`
  - `tool_options.docker_shm_size = "1g"`
  - `tool_options.stall_timeout_minutes = 60` for `canada.ca` sources
- Annual campaign jobs will default to **WARC-first artifacts**:
  - `tool_options.skip_final_build = True` to skip optional `.zim` generation during the campaign.
- Shared-host `canada.ca` sources will default to **querystring-averse scope rules** for content paths to reduce duplicate/trap-like expansions while preserving completeness within intended boundaries.

## Rationale

- Using `2` workers on a 4 vCPU host is a conservative way to improve throughput without introducing multi-job concurrency complexity.
- Increasing container `/dev/shm` is a low-risk stability improvement for browser-driven crawls.
- Skipping ZIM generation keeps the critical path focused on: crawl WARCs → index → searchable. ZIMs can be generated later as a secondary artifact if desired.
- Excluding querystring/fragment variants for `canada.ca` content paths reduces duplicate work and the risk of trap-like URL expansions without relying on page/depth caps.

## Alternatives considered

- Keep `initial_workers=1` — rejected: underutilizes the host and increases the likelihood a single slow URL dominates wall-clock progress.
- Build `.zim` during the annual campaign — rejected: increases time-to-search-readiness and adds an extra failure surface; backend does not require ZIMs.
- Add page/depth caps — rejected: conflicts with completeness-first goals and risks silent truncation.
- Run multiple sources concurrently — deferred: increases ops surface area and complicates resource contention on a small VPS.

## Consequences

### Positive

- Faster wall-clock progress on the annual campaign without changing “what is in scope”.
- Reduced time lost to stalls/restarts on browser-driven crawls.
- Clear separation between “search readiness” (WARCs indexed) and optional offline artifacts (ZIMs).

### Negative / risks

- Slightly higher load on target sites due to parallelism; mitigation is modest concurrency and monitoring.
- Excluding querystring variants can omit some non-canonical pages; mitigation is explicit scope review if a source relies on query-driven content.

## Verification / rollout

- Rollout is via:
  - `ha_backend.job_registry` defaults for annual sources.
  - `archive_tool` support for `--skip-final-build` and `--docker-shm-size`.
- Verify with:
  - crawl metrics (progress age, stalled flag, restart rate),
  - successful indexing immediately after crawl completion,
  - spot-check that `canada.ca` scope still matches canonical content URLs and continues to capture referenced assets.

Rollback:

- Set annual defaults back to `initial_workers=1`, remove `docker_shm_size`, and set `skip_final_build=false`.
- Redeploy backend and restart the worker.

## References

- Related canonical docs:
  - `docs/operations/annual-campaign.md`
  - `docs/reference/archive-tool.md`
  - `docs/architecture.md`
- Related monitoring:
  - `docs/operations/monitoring-and-alerting.md`
- Related prior decision:
  - `docs/decisions/2026-01-19-annual-crawl-resiliency-and-queue-order.md`
