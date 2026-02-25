# Crawl Health Remediation (Implemented 2026-02-25)

**Status:** Implemented | **Scope:** Fixed the root causes of the 2026 annual crawl campaign stall — zero indexed pages after nearly 2 months, 70+ auto-recoveries on the HC job — through scope regex refinement, a progress circuit breaker, dependency group separation, and two new operational alerts.

## Outcomes

- **Scope regex narrowed (HC + PHAC):** DAM paths (`content/dam/hc-sc/.*`, `content/dam/phac-aspc/.*`) now only enqueue web-renderable assets (css/js/json/svg/images/fonts). PDFs and binary documents are excluded from the crawl queue, eliminating the `Navigation timeout` thrashing loop. Subresource capture is unaffected.
- **Progress circuit breaker added to `vps-crawl-auto-recover.py`:** New `--min-progress-per-recovery` (default 10) and `--progress-circuit-window` (default 3) args. The watchdog now skips recovery when the last N consecutive recoveries each produced fewer than the threshold pages, preventing unbounded restart churn.
- **Dependency groups separated in `pyproject.toml`:** Split `[dev]` (test/lint tools) and `[docs]` (mkdocs-material, pillow, cairosvg). CI installs only `[dev]`, so `pip-audit` runs clean without `--ignore-vuln CVE-2026-25990`. Local `make venv` installs both.
- **Two new Prometheus alert rules:** `HealthArchiveDeployLockPersistent` (deploy lock held >4h) and `HealthArchiveCrawlTempDirsHigh` (>100 temp dirs per job for >1h), covering two previously invisible failure modes.
- **Monitoring doc updated:** Added state-file mtime staleness note to section 4 (use `last_progress_age_seconds`, not `state_mtime_age_seconds`); added sections 7 and 8 for the new alerts.

## Canonical Docs Updated

- `docs/operations/monitoring-and-alerting.md` — sections 4, 7, 8
- `ops/observability/alerting/healtharchive-alerts.yml` — two new rules in `healtharchive.crawl` group
- `docs/planning/roadmap.md` — item #18 resolved and removed

## Historical Context

Full implementation detail is preserved in git history. The original plan was scoped as four parallel batches (scope regex, circuit breaker, dep separation, alert rules) followed by a docs batch.
