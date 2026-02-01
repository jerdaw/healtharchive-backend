# HealthArchive 6-Phase Upgrade Roadmap (Implemented 2025-12-24)

**Status:** Implemented | **Scope:** Comprehensive upgrade program (Phases 0–6) covering narrative tightening, governance, change tracking, and researcher tools.

> For current operations, see `docs/operations/README.md` and `docs/operations/healtharchive-ops-roadmap.md`.

## Outcomes

- **Phase 0 (Narrative):** Standardized mission statement; "archived, not current" messaging on all workflow pages
- **Phase 1 (Governance):** Published Governance, Terms, Privacy, Changelog, and Report-an-issue flow
- **Phase 2 (Stats):** `/status` and `/impact` pages with real archive metrics
- **Phase 3 (Change Tracking):** Change detection, compare views, RSS feed for changes
- **Phase 4 (Researcher Tools):** Timeline views, citation helpers, export endpoints
- **Phase 5 (Monitoring):** Prometheus/Grafana observability stack via Tailscale
- **Phase 6 (Refinement):** Ongoing iteration on search, snippets, and ops automation

## Canonical Docs Updated

- Architecture: [architecture.md](../../architecture.md)
- Production runbook: [deployment/production-single-vps.md](../../deployment/production-single-vps.md)
- Annual campaign: [operations/annual-campaign.md](../../operations/annual-campaign.md)
- Replay service: [deployment/replay-service-pywb.md](../../deployment/replay-service-pywb.md)
- Cross-repo config: [deployment/environments-and-configuration.md](../../deployment/environments-and-configuration.md)
- Observability: [operations/observability-and-private-stats.md](../../operations/observability-and-private-stats.md)

## Key Design Decisions

- **Single VPS model:** Postgres + API + worker + storage on one Hetzner server
- **Tailscale-only SSH:** No public SSH access; private observability via tailnet
- **CSP security headers:** Restrictive Content-Security-Policy in report-only mode
- **No AI summaries:** Preserve provenance; avoid medical interpretation features
- **Aggregate-only usage:** Daily counters only; no per-user tracking

## Historical Context

This was the foundational multi-phase upgrade that established the current architecture. The detailed phase-by-phase narrative (2,400+ lines) is preserved in git history. Key appendices covered:

- Appendix A: Stats pages structure
- Appendix B: /status page metrics
- Appendix C: /impact page design
- Appendix D: Changelog template
- Appendix E: Report-an-issue categories
- Appendix F: Governance page outline
- Appendix G: Change tracking decisions
- Appendix H: Digest/RSS structure
