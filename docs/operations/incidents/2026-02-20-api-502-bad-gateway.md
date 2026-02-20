# Incident: API 502 Bad Gateway (2026-02-20)

Status: closed

## Metadata

- Date (UTC): 2026-02-20
- Severity (see `operations/incidents/severity.md`): sev1
- Environment: production
- Primary area: api
- Owner: Jeremy Dawson
- Start (UTC): 2026-02-20T02:53:24Z
- End (UTC): 2026-02-20T16:29:00Z

---

## Summary

UptimeRobot detected an HTTP 502 Bad Gateway on `api.healtharchive.ca/api/health`. This indicates Caddy is unable to route traffic to the backend API (`healtharchive-api.service`).

## Impact

- User-facing impact: The public website and API are unreachable, returning a 502 error.
- Internal impact: All public API routes, admin routes, and metrics are unavailable.
- Data impact:
  - Data loss: unknown (likely none, as worker/db might still be running)
  - Data integrity risk: no
  - Recovery completeness: unknown
- Duration: 13 hours 35 minutes

## Detection

- How was it detected (alert, operator check, user report)? UptimeRobot alert for `api.healtharchive.ca/api/health`.
- What signals were most useful (commands/metrics/logs)? The 502 status code indicates a proxy-to-upstream failure (Caddy -> uvicorn).

## Decision log (optional but recommended for sev0/sev1)

- 2026-02-20T02:53:24Z — Decision: Investigated the 502 Bad Gateway alert and prepared recovery steps (why: Incident triggered by UptimeRobot, risks: None)

## Timeline (UTC)

- 2026-02-20T02:53:24Z — UptimeRobot detected HTTP 502 Bad Gateway on api.healtharchive.ca/api/health.
- 2026-02-20T16:29:00Z — Investigated logs indicating `ModuleNotFoundError: No module named 'slowapi'`.
- 2026-02-20T16:32:54Z — Restored via `./scripts/vps-deploy.sh --apply` which installed the missing dependencies and restarted the API service.

## Root cause

- Immediate trigger: `healtharchive-api.service` was crashing with `ModuleNotFoundError: No module named 'slowapi'`.
- Underlying cause(s): A code change merged into `main` added the `slowapi` rate limiting dependency to pyproject.toml and source code, but the production VPS environment was pulled or restarted without running the deployment script, leaving the virtual environment without the new dependency.

## Contributing factors

- What made this worse or harder to debug? Deployment without executing the full script (`vps-deploy.sh`) skipped dependency resolution.

## Resolution / Recovery

1. Identified API service crash via `sudo journalctl -u healtharchive-api -n 200`.
2. Error was `ModuleNotFoundError: No module named 'slowapi'`.
3. Noticed `slowapi` was missing from `.venv` site-packages.
4. Ran `./scripts/vps-deploy.sh --apply` to reinstall dependencies (`pip install -e .`) and restart the fastAPI process securely.
5. Verified recovery with `./scripts/verify_public_surface.py` showing all checks passed.

## Post-incident verification

- Public surface checks: `./scripts/verify_public_surface.py` passes (0 failures).
- Worker/job health checks: Worker was unaffected, continued processing crawl jobs securely.
- Storage/mount checks (if relevant):
- Integrity checks (if relevant):

## Public communication (optional; do this when it changes user expectations)

- Public status update (where/when):
- Changelog entry (date/link):
- Public summary (2–5 sentences):

## Open questions (still unknown)

- Why did the API service crash?

## Action items (TODOs)

- [x] Investigate root cause of API crash from journalctl logs (owner=, priority=, due=)
- [ ] Determine how code was updated on the VPS without a deploy triggered (e.g. manual `git pull`) and reinforce operator runbooks to continually use `vps-deploy.sh`.

## Automation opportunities

- What can be automated safely?
- What should stay manual (risk/false positives)?

## References / Artifacts

- Relevant log path(s): `sudo journalctl -u healtharchive-api`
- Related playbooks/runbooks: `docs/deployment/production-single-vps.md`
